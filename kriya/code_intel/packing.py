"""Member-level context packing (T0..T2) for a localized mutation target.

T0 is the only mutation-authoritative part: the target member's exact
current bytes (path, raw sha256, span, symbol id) plus its enclosing header
(package/imports, enclosing type signatures, fields, constructor
signatures). T1 (collaborator signatures), T2 (linked tests) and T3 (linked
configuration: Spring beans of the enclosing type, component scans covering
its package, properties/YAML keys the member or its type references) are read
context; configuration is never mutation authority. T0 is never dropped: a budget that cannot hold it is reported as
``over_budget``, never satisfied by trimming the target. Rendering puts the
stable material first and the volatile member body last, so a provider's
prefix cache survives retries on the same file.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

from kriya.code_intel.model import Symbol

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z0-9_.\-\[\]]+)(?::[^}]*)?\}")
_CONFIG_PROPERTIES_PREFIX_RE = re.compile(r'ConfigurationProperties\(\s*(?:(?:prefix|value)\s*=\s*)?"([^"]+)"')


def default_token_count(text: str) -> int:
    return max(1, len(text) // 4)


@dataclass
class ContextPackage:
    target: Symbol
    path: str
    source_digest: str
    member_text: str
    header: List[str] = field(default_factory=list)
    members: List[str] = field(default_factory=list)  # other members of the enclosing type (signatures)
    collaborators: List[Tuple[str, List[str]]] = field(default_factory=list)  # (type key, member signatures)
    tests: List[Tuple[str, str]] = field(default_factory=list)  # (symbol id, signature)
    config: List[Tuple[str, str]] = field(default_factory=list)  # (symbol id, "path:line  declaration")
    over_budget: bool = False
    dropped: List[str] = field(default_factory=list)
    include_target: bool = True

    def render(self, include_target: bool = True) -> str:
        """The package, stable material first. ``include_target=False``
        leaves the target body out (a caller that already shows the exact
        member as its own authoritative item)."""
        span = f"{self.target_span[0]}-{self.target_span[1]}"
        parts = [f"### Context for {self.target.lookup_key} ({self.path})"]
        if self.header:
            parts.append("#### Enclosing declarations (read-only)\n" + "\n".join(self.header))
        if self.members:
            parts.append("#### Other members of the enclosing type (signatures, read-only)\n"
                         + "\n".join(self.members))
        for key, signatures in self.collaborators:
            parts.append(f"#### Collaborator {key} (signatures, read-only)\n" + "\n".join(signatures))
        if self.tests:
            parts.append("#### Linked tests (read-only)\n" + "\n".join(sig for _, sig in self.tests))
        if self.config:
            parts.append("#### Linked configuration (read-only)\n" + "\n".join(line for _, line in self.config))
        if include_target:
            parts.append(f"#### Target member (authoritative source) {self.path}:{span} sha256={self.source_digest}"
                         f" id={self.target.symbol_id}\n{self.member_text}")
        return "\n\n".join(parts)

    @property
    def target_span(self) -> Tuple[int, int]:
        return self.target.declaration.start_line, self.target.declaration.end_line


_TYPE_REFERENCE_RE = re.compile(r"\b([A-Z][A-Za-z0-9_]*)\b")
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_MAX_HEADER_MEMBERS = 40


def _import_names(statement: str, language: str) -> set:
    """The names an import binds: Java ``a.b.C`` -> {C} (static: the member);
    Python ``import a.b`` -> {a}, ``import a as b`` -> {b},
    ``from x import a, b as c`` -> {a, c}."""
    if language == "java":
        return {statement.rsplit(".", 1)[-1]}
    if statement.startswith("from "):
        names = statement.split(" import ", 1)[-1].strip("() ")
    else:
        names = statement[len("import "):]
    bound = set()
    for part in names.split(","):
        part = part.strip().strip("()")
        if not part:
            continue
        bound.add(part.split(" as ", 1)[1].strip() if " as " in part else part.split(".", 1)[0].strip())
    return bound


def build_package(service, symbol_id: str, budget_tokens: Optional[int] = None,
                  count_tokens: Callable[[str], int] = default_token_count,
                  max_collaborators: int = 4, max_tests: int = 6, max_config: int = 8,
                  include_target: bool = True, member_signatures: bool = False) -> Optional[ContextPackage]:
    """``include_target`` decides whether budgets count the target body
    (False when a caller shows it separately); ``member_signatures`` adds
    the enclosing type's other members as an optional tier."""
    member = service.get_member(symbol_id)
    if member is None:
        return None
    target = member.symbol
    structure = service.current_structure(member.path)
    symbols = list(structure.symbols) if structure else [target]
    by_id = {s.symbol_id: s for s in symbols}
    header: List[str] = []
    used = set(_IDENTIFIER_RE.findall(member.text))
    if structure is not None:
        if structure.language == "java" and structure.namespace:
            header.append(f"package {structure.namespace};")
        # Only the imports the member uses ("necessary imports"); a wildcard
        # import cannot be decided and is kept.
        header.extend(f"import {i};" if structure.language == "java" else i
                      for i in structure.imports if _import_names(i, structure.language) & used
                      or i.endswith("*"))
    owner_chain = []
    parent = by_id.get(target.parent_id) if target.parent_id else None
    while parent is not None:
        owner_chain.insert(0, parent)
        parent = by_id.get(parent.parent_id) if parent.parent_id else None
    for owner in owner_chain:
        annotations = " ".join(f"@{a}" for a in owner.annotations if owner.language == "java")
        header.append(f"{annotations + ' ' if annotations else ''}{owner.signature_text}")
        siblings = [s for s in symbols if s.parent_id == owner.symbol_id and s.symbol_id != target.symbol_id
                    and s.kind in ("field", "attribute", "enum_constant", "constructor")]
        # Fields the member references first, then the rest (bounded).
        siblings.sort(key=lambda s: (s.name not in used, s.declaration.start_line))
        for sibling in siblings[:_MAX_HEADER_MEMBERS]:
            annotations = " ".join(f"@{a}" for a in sibling.annotations if sibling.language == "java")
            header.append(f"    {annotations + ' ' if annotations else ''}{sibling.signature_text}")
    package = ContextPackage(target, member.path, member.source_digest, member.text, header)
    package.include_target = include_target
    if budget_tokens is not None and count_tokens(package.render(include_target)) > budget_tokens:
        package.over_budget = True  # T0 stays whole; nothing optional is added
        return package
    owner_names = {o.name for o in owner_chain}
    owner_ids = {o.symbol_id for o in owner_chain}
    if member_signatures and owner_chain:
        for sibling in symbols:
            if sibling.parent_id == owner_chain[-1].symbol_id and sibling.is_callable and \
                    sibling.kind != "constructor" and sibling.symbol_id != target.symbol_id:
                _add_within_budget(package, package.members, f"    {sibling.signature_text}", budget_tokens,
                                   count_tokens, f"member:{sibling.symbol_id}")
    # Collaborators: the declared types of the fields the member uses, then
    # the types it names itself.
    field_types = [_TYPE_REFERENCE_RE.findall(s.return_type or "")[:1] for s in symbols
                   if s.parent_id in owner_ids and s.kind in ("field", "attribute") and s.name in used]
    candidates = [name for found in field_types for name in found] + _TYPE_REFERENCE_RE.findall(member.text)
    for type_name in dict.fromkeys(candidates):
        if len(package.collaborators) >= max_collaborators or type_name in owner_names:
            continue
        types = [s for s in service.find_symbol(type_name) if s.is_type and s.name == type_name]
        if len(types) != 1:
            continue  # unknown or ambiguous: never guess a collaborator
        signatures = [s.signature_text for s in service.store.by_path(types[0].path)
                      if s.parent_id == types[0].symbol_id and s.is_callable][:12]
        if signatures:
            _add_within_budget(package, package.collaborators, (types[0].lookup_key, signatures), budget_tokens,
                               count_tokens, f"collaborator:{types[0].lookup_key}")
    for test in _linked_tests(service, target, owner_chain)[:max_tests]:
        _add_within_budget(package, package.tests, (test.symbol_id, test.signature_text), budget_tokens,
                           count_tokens, f"test:{test.symbol_id}")
    data = service.current_bytes(member.path) or b""
    referencing = [member.text] + [
        s.signature_text for s in symbols if s.parent_id in owner_ids and s.kind in ("field", "attribute")] + [
        data[o.declaration.start_byte:o.signature.start_byte].decode("utf-8", "replace") for o in owner_chain]
    namespace = structure.namespace if structure is not None else ""
    for entry in linked_configuration(service, owner_chain, namespace, referencing)[:max_config]:
        line = f"{entry.path}:{entry.declaration.start_line}  {entry.signature_text}"
        _add_within_budget(package, package.config, (entry.symbol_id, line), budget_tokens, count_tokens,
                           f"config:{entry.symbol_id}")
    return package


def linked_configuration(service, owners: List[Symbol], namespace: str, referencing: List[str]) -> List[Symbol]:
    """Deterministic configuration linkage (structural, never resolved):
    Spring beans whose ``class`` is the enclosing type's qualified name;
    ``component-scan`` packages containing ``namespace``; property keys that
    ``referencing`` (the member, the enclosing types' field signatures and
    annotations) names as ``${key}``; and keys under a
    ``@ConfigurationProperties`` prefix found there. Beans, scans, then keys;
    each symbol once."""
    found: List[Symbol] = []
    owner = owners[-1] if owners else None
    if owner is not None and owner.language == "java":
        found.extend(service.store.by_return_type(owner.lookup_key, kind="bean"))
        if namespace:
            found.extend(scan for scan in service.store.by_kind("component_scan")
                         if namespace == scan.lookup_key or namespace.startswith(scan.lookup_key + "."))
    for key in dict.fromkeys(key for text in referencing for key in _PLACEHOLDER_RE.findall(text)):
        found.extend(s for s in service.find_symbol(key) if s.kind == "config_key" and s.lookup_key == key)
    for prefix in dict.fromkeys(m for text in referencing for m in _CONFIG_PROPERTIES_PREFIX_RE.findall(text)):
        found.extend(service.store.by_lookup_prefix(prefix + ".", kind="config_key"))
    return list({s.symbol_id: s for s in found}.values())


def _add_within_budget(package: ContextPackage, tier: list, item, budget: Optional[int], count_tokens,
                       label: str) -> None:
    """Add ``item`` to an optional tier only if the whole rendered package
    still fits (measured on the real rendering, headings included)."""
    tier.append(item)
    if budget is not None and count_tokens(package.render(package.include_target)) > budget:
        tier.pop()
        package.dropped.append(label)


def _linked_tests(service, target: Symbol, owners: List[Symbol]) -> List[Symbol]:
    """Deterministic test linkage by naming convention only: test callables
    whose name contains the target's name, in a test type named after the
    enclosing type (FooTest, FooTests, TestFoo) or a test_<module>.py file."""
    from kriya.code_intel.locate import is_test_path

    stem = target.name.lower().lstrip("_")
    owner = owners[-1].name if owners else ""
    test_types = {f"{owner}Test", f"{owner}Tests", f"Test{owner}"} if owner else set()
    found = []
    for candidate in service.find_symbol(f"test{target.name[:1].upper()}{target.name[1:]}") + service.find_symbol(
            f"test_{target.name}"):
        if is_test_path(candidate.path):
            found.append(candidate)
    for type_name in sorted(test_types):
        for test_type in service.find_symbol(type_name):
            for symbol in service.store.by_path(test_type.path):
                if symbol.is_callable and stem and stem in symbol.name.lower() and symbol not in found:
                    found.append(symbol)
    return sorted(dict((s.symbol_id, s) for s in found).values(), key=lambda s: s.symbol_id)
