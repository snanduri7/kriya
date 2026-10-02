"""tree-sitter front end for the structural symbol model (Java, Python).

The only module in Kriya that imports tree-sitter. Everything it returns is
a ``FileStructure`` of plain dataclasses, so no caller ever holds a syntax
tree or depends on grammar node names.
"""
from __future__ import annotations

import bisect
import functools
import importlib.metadata
import os
from typing import Dict, Iterable, List, Optional, Tuple

from kriya.code_intel.model import (
    CALLABLE_KINDS,
    FileStructure,
    ParserIdentity,
    ParseState,
    Span,
    Symbol,
    source_digest,
)

# Bump when the extraction rules below change what is produced for the same
# bytes (a stored structure under an older version is then never reused).
# /2: configuration structure (config_parsing.py: Spring XML, properties, YAML).
STRUCTURAL_PARSER_VERSION = "ci-structural/2"

_LANGUAGE_BY_EXTENSION = {".java": "java", ".py": "python"}


def language_for_path(path: str) -> Optional[str]:
    """The CODE language (tree-sitter) of ``path``, or None."""
    return _LANGUAGE_BY_EXTENSION.get(os.path.splitext(path)[1].lower())


def is_structural_path(path: str) -> bool:
    """Code or configuration the structural index holds (configuration
    files are decided by name; a non-Spring XML parses UNSUPPORTED)."""
    from kriya.code_intel.config_parsing import config_language_for_path

    return language_for_path(path) is not None or config_language_for_path(path) is not None


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unavailable"


@functools.lru_cache(maxsize=1)
def parser_identity() -> ParserIdentity:
    return ParserIdentity(
        tree_sitter=_version("tree-sitter"), java_grammar=_version("tree-sitter-java"),
        python_grammar=_version("tree-sitter-python"), structural_parser=STRUCTURAL_PARSER_VERSION,
        pyyaml=_version("PyYAML"),
    )


@functools.lru_cache(maxsize=None)
def _language(language: str):
    import tree_sitter

    if language == "java":
        import tree_sitter_java as grammar
    else:
        import tree_sitter_python as grammar
    return tree_sitter.Language(grammar.language())


def _parse_tree(language: str, data: bytes):
    import tree_sitter

    return tree_sitter.Parser(_language(language)).parse(data)


def parse_file(path: str, data: bytes) -> FileStructure:
    """The structure of ``data`` (the raw bytes of ``path``). Never raises:
    an unsupported language or a parser failure is a typed state."""
    digest = source_digest(data)
    identity = parser_identity().digest
    language = language_for_path(path)
    if language is None:
        from kriya.code_intel.config_parsing import config_language_for_path, parse_config_file

        if config_language_for_path(path) is not None:
            return parse_config_file(path, data)
        return FileStructure(path, "", digest, ParseState.UNSUPPORTED, identity, detail="no structural parser")
    try:
        tree = _parse_tree(language, data)
        extractor = _JavaExtractor if language == "java" else _PythonExtractor
        extracted = extractor(path, data, digest)
        symbols, imports, namespace = extracted.run(tree.root_node)
        calls = extracted.calls(tree.root_node)
    except Exception as error:  # a grammar/binding failure must never look like "no symbols"
        return FileStructure(path, language, digest, ParseState.PARSE_FAILED, identity,
                             detail=f"{type(error).__name__}: {error}")
    errors = _error_count(tree.root_node)
    state = ParseState.PARTIALLY_PARSED if errors else ParseState.PARSED
    return FileStructure(path, language, digest, state, identity, tuple(symbols), tuple(imports), namespace,
                         calls, errors)


def parse_text(path: str, content: str) -> FileStructure:
    return parse_file(path, content.encode("utf-8"))


def _error_count(root) -> int:
    if not root.has_error:
        return 0
    count = 0
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error or node.is_missing:
            count += 1
            continue
        if node.has_error:
            stack.extend(node.children)
    return count


def _compact(text: str) -> str:
    return " ".join(text.split())


class _Extractor:
    language = ""

    def __init__(self, path: str, data: bytes, digest: str) -> None:
        self.path = path
        self.data = data
        self.digest = digest
        self.symbols: List[Symbol] = []
        self.imports: List[str] = []
        self._ids: Dict[str, int] = {}
        # Lines come from byte offsets, never from Node.start_point/end_point
        # (see the tree-sitter pin in pyproject.toml).
        self._newlines = [i for i, b in enumerate(data) if b == 10]

    def line_of(self, byte: int) -> int:
        return bisect.bisect_left(self._newlines, byte) + 1

    def span(self, node, start_node=None, end_byte: Optional[int] = None) -> Span:
        start = (start_node or node).start_byte
        end = node.end_byte if end_byte is None else end_byte
        return Span(self.line_of(start), self.line_of(max(end - 1, start)), start, end)

    def calls(self, root) -> Tuple[str, ...]:
        del root
        return ()

    def text(self, node) -> str:
        return self.data[node.start_byte:node.end_byte].decode("utf-8", "replace")

    def unique_id(self, base: str) -> str:
        seen = self._ids.get(base, 0)
        self._ids[base] = seen + 1
        return base if seen == 0 else f"{base}#{seen + 1}"

    def add(self, *, kind: str, name: str, lookup_key: str, node, signature: Span, body_node,
            parent_id: Optional[str], declaration: Optional[Span] = None, parameter_types: Tuple[str, ...] = (),
            **extra) -> Symbol:
        base = f"{self.language}:{self.path}#{lookup_key}"
        if kind in CALLABLE_KINDS:
            base += "(" + ",".join(t.replace(" ", "") for t in parameter_types) + ")"
        symbol = Symbol(
            symbol_id=self.unique_id(base), language=self.language, kind=kind, name=name, lookup_key=lookup_key,
            path=self.path, source_digest=self.digest, declaration=declaration or self.span(node), signature=signature,
            body=self.span(body_node) if body_node is not None else None, parent_id=parent_id,
            parameter_types=parameter_types, **extra,
        )
        self.symbols.append(symbol)
        return symbol

    def signature_span(self, node, start_node, body_node) -> Span:
        if body_node is None:
            return self.span(node, start_node=start_node)
        end = body_node.start_byte
        text_before = self.data[start_node.start_byte:end].rstrip()
        last = start_node.start_byte + max(len(text_before) - 1, 0)
        return Span(self.line_of(start_node.start_byte), self.line_of(last), start_node.start_byte, end)

    def doc_from_comment(self, node) -> str:
        previous = node.prev_sibling
        if previous is None or previous.type != "block_comment":
            return ""
        raw = self.text(previous)
        if not raw.startswith("/**"):
            return ""
        for line in raw[3:].splitlines():
            line = line.strip().lstrip("*").strip().rstrip("*/").strip()
            if line:
                return line[:200]
        return ""


_JAVA_TYPE_KINDS = {
    "class_declaration": "class", "interface_declaration": "interface", "enum_declaration": "enum",
    "record_declaration": "record", "annotation_type_declaration": "annotation_type",
}
_JAVA_CALLABLES = {
    "method_declaration": "method", "constructor_declaration": "constructor",
    "compact_constructor_declaration": "constructor", "annotation_type_element_declaration": "annotation_element",
}
_JAVA_FIELDS = {"field_declaration", "constant_declaration"}
_JAVA_BODY_WRAPPERS = {"enum_body_declarations"}


@functools.lru_cache(maxsize=None)
def _java_invocation_query():
    import tree_sitter

    return tree_sitter.Query(_language("java"), "(method_invocation name: (identifier) @name)")


def _type_name(text: str) -> str:
    """``a.b.Bar<T>`` -> ``a.b.Bar``: the written name without type arguments."""
    return _compact(text.split("<", 1)[0]).replace(" ", "")


class _JavaExtractor(_Extractor):
    language = "java"

    def calls(self, root) -> Tuple[str, ...]:
        import tree_sitter

        nodes = tree_sitter.QueryCursor(_java_invocation_query()).captures(root).get("name", [])
        return tuple(dict.fromkeys(self.text(n) for n in sorted(nodes, key=lambda n: n.start_byte)))

    def supertypes(self, node) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
        extends: List[str] = []
        implements: List[str] = []
        for child in node.children:
            if child.type == "superclass":
                extends.extend(_type_name(self.text(c)) for c in child.named_children)
            elif child.type in ("super_interfaces", "extends_interfaces"):
                target = implements if child.type == "super_interfaces" else extends
                for type_list in child.named_children:
                    target.extend(_type_name(self.text(c)) for c in type_list.named_children)
        return tuple(extends), tuple(implements)

    def run(self, root) -> Tuple[List[Symbol], List[str], str]:
        package = ""
        for child in root.named_children:
            if child.type == "package_declaration":
                name = next((c for c in child.named_children if c.type in ("scoped_identifier", "identifier")), None)
                package = self.text(name) if name is not None else ""
            elif child.type == "import_declaration":
                self.imports.append(_compact(self.text(child))[len("import"):].rstrip(";").strip())
            elif child.type in _JAVA_TYPE_KINDS:
                self.visit_type(child, package, None)
        return self.symbols, self.imports, package

    def modifiers(self, node) -> Tuple[Tuple[str, ...], Tuple[str, ...], object]:
        """(keywords, annotation names, first non-annotation node)."""
        modifiers = next((c for c in node.children if c.type == "modifiers"), None)
        if modifiers is None:
            return (), (), node
        keywords, annotations, first_keyword = [], [], None
        for child in modifiers.children:
            if child.type in ("marker_annotation", "annotation"):
                name = child.child_by_field_name("name")
                annotations.append(self.text(name) if name is not None else self.text(child).lstrip("@"))
            elif child.type not in ("line_comment", "block_comment"):
                keywords.append(self.text(child))
                first_keyword = first_keyword or child
        if first_keyword is None:
            first_keyword = modifiers.next_sibling or node
        return tuple(keywords), tuple(annotations), first_keyword

    def members(self, body) -> Iterable:
        for child in body.named_children:
            if child.type in _JAVA_BODY_WRAPPERS:
                yield from child.named_children
            else:
                yield child

    def visit_type(self, node, prefix: str, parent: Optional[Symbol]) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        qualified = f"{prefix}.{name}" if prefix else name
        keywords, annotations, start = self.modifiers(node)
        body = node.child_by_field_name("body")
        extends, implements = self.supertypes(node)
        symbol = self.add(
            kind=_JAVA_TYPE_KINDS[node.type], extends=extends, implements=implements, name=name, lookup_key=qualified, node=node,
            signature=self.signature_span(node, start, body), body_node=body,
            parent_id=parent.symbol_id if parent else None, modifiers=keywords, annotations=annotations,
            signature_text=_compact(self.data[start.start_byte:(body or node).start_byte].decode("utf-8", "replace"))
            if body is not None else _compact(self.text(node)),
            doc_summary=self.doc_from_comment(node),
        )
        if node.type == "record_declaration":
            params = node.child_by_field_name("parameters")
            for component in params.named_children if params is not None else ():
                if component.type == "formal_parameter" and component.child_by_field_name("name") is not None:
                    field_name = self.text(component.child_by_field_name("name"))
                    self.add(kind="field", name=field_name, lookup_key=f"{qualified}.{field_name}", node=component,
                             signature=self.span(component), body_node=None, parent_id=symbol.symbol_id,
                             modifiers=("record_component",),
                             return_type=_compact(self.text(component.child_by_field_name("type"))),
                             signature_text=_compact(self.text(component)))
        if body is None:
            return
        for member in self.members(body):
            if member.type in _JAVA_TYPE_KINDS:
                self.visit_type(member, qualified, symbol)
            elif member.type in _JAVA_CALLABLES:
                self.visit_callable(member, qualified, symbol, node)
            elif member.type in _JAVA_FIELDS:
                self.visit_field(member, qualified, symbol)
            elif member.type == "enum_constant":
                constant = self.text(member.child_by_field_name("name"))
                self.add(kind="enum_constant", name=constant, lookup_key=f"{qualified}.{constant}", node=member,
                         signature=self.span(member), body_node=member.child_by_field_name("body"),
                         parent_id=symbol.symbol_id, signature_text=constant)

    def parameter_types(self, params) -> Tuple[str, ...]:
        types = []
        for param in params.named_children if params is not None else ():
            if param.type == "formal_parameter":
                type_node = param.child_by_field_name("type")
                dims = param.child_by_field_name("dimensions")
                text = _compact(self.text(type_node)) if type_node is not None else "?"
                types.append(text + (self.text(dims).replace(" ", "") if dims is not None else ""))
            elif param.type == "spread_parameter":
                type_node = next((c for c in param.named_children if c.type not in ("modifiers", "variable_declarator")),
                                 None)
                types.append((_compact(self.text(type_node)) if type_node is not None else "?") + "...")
        return tuple(types)

    def visit_callable(self, node, owner: str, parent: Symbol, owner_node) -> None:
        name = self.text(node.child_by_field_name("name"))
        keywords, annotations, start = self.modifiers(node)
        body = node.child_by_field_name("body")
        if node.type == "compact_constructor_declaration":
            params = owner_node.child_by_field_name("parameters")
        else:
            params = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("type")
        kind = _JAVA_CALLABLES[node.type]
        signature_end = body.start_byte if body is not None else node.end_byte
        self.add(
            kind=kind, name=name, lookup_key=f"{owner}.{name}", node=node,
            signature=self.signature_span(node, start, body), body_node=body, parent_id=parent.symbol_id,
            modifiers=keywords, annotations=annotations, parameter_types=self.parameter_types(params),
            return_type=_compact(self.text(return_node)) if return_node is not None else None,
            signature_text=_compact(self.data[start.start_byte:signature_end].decode("utf-8", "replace")).rstrip(";"),
            doc_summary=self.doc_from_comment(node),
        )

    def visit_field(self, node, owner: str, parent: Symbol) -> None:
        keywords, annotations, start = self.modifiers(node)
        type_node = node.child_by_field_name("type")
        for declarator in node.children_by_field_name("declarator"):
            name_node = declarator.child_by_field_name("name")
            if name_node is None:
                continue
            name = self.text(name_node)
            self.add(kind="field", name=name, lookup_key=f"{owner}.{name}", node=node,
                     signature=self.signature_span(node, start, None), body_node=None, parent_id=parent.symbol_id,
                     modifiers=keywords, annotations=annotations,
                     return_type=_compact(self.text(type_node)) if type_node is not None else None,
                     signature_text=_compact(self.text(node)).rstrip(";")[:300],
                     doc_summary=self.doc_from_comment(node))


_PY_CONTAINERS = {"if_statement", "try_statement", "else_clause", "elif_clause", "except_clause",
                  "finally_clause", "with_statement"}


def python_module_name(path: str) -> str:
    """Lexical module path: ``kriya/a/b.py`` -> ``kriya.a.b``; a leading
    ``src/`` layout root and a trailing ``__init__`` are dropped."""
    parts = os.path.splitext(path.replace(os.sep, "/"))[0].split("/")
    if len(parts) > 1 and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(p for p in parts if p)


class _PythonExtractor(_Extractor):
    language = "python"

    def run(self, root) -> Tuple[List[Symbol], List[str], str]:
        module = python_module_name(self.path)
        self.visit_block(root, module, None, scope="module")
        return self.symbols, self.imports, module

    def docstring(self, block) -> str:
        if block is None:
            return ""
        first = next(iter(block.named_children), None)
        if first is None or first.type != "expression_statement":
            return ""
        string = next(iter(first.named_children), None)
        if string is None or string.type != "string":
            return ""
        content = "".join(self.text(c) for c in string.named_children if c.type == "string_content")
        return next((line.strip()[:200] for line in content.splitlines() if line.strip()), "")

    def visit_block(self, block, prefix: str, parent: Optional[Symbol], scope: str) -> None:
        for child in block.named_children:
            if child.type == "decorated_definition":
                definition = child.child_by_field_name("definition")
                decorators = tuple(self.text(d).lstrip("@").strip() for d in child.named_children
                                   if d.type == "decorator")
                if definition is not None:
                    self.visit_definition(definition, child, decorators, prefix, parent, scope)
            elif child.type in ("function_definition", "class_definition"):
                self.visit_definition(child, child, (), prefix, parent, scope)
            elif child.type in ("import_statement", "import_from_statement", "future_import_statement"):
                if scope == "module":
                    self.imports.append(_compact(self.text(child)))
            elif child.type == "expression_statement" and scope in ("module", "class"):
                self.visit_assignment(child, prefix, parent, scope)
            elif child.type in _PY_CONTAINERS or child.type == "block":
                self.visit_block(child, prefix, parent, scope)

    def visit_definition(self, node, outer, decorators, prefix: str, parent: Optional[Symbol], scope: str) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = self.text(name_node)
        qualified = f"{prefix}.{name}" if prefix else name
        body = node.child_by_field_name("body")
        bases: Tuple[str, ...] = ()
        if node.type == "class_definition":
            kind = "class"
            modifiers: Tuple[str, ...] = ()
            superclasses = node.child_by_field_name("superclasses")
            bases = tuple(self.text(b) for b in superclasses.named_children
                          if b.type in ("identifier", "attribute")) if superclasses is not None else ()
        else:
            kind = "method" if scope == "class" else "function"
            modifiers = ("async",) if any(c.type == "async" for c in node.children) else ()
        signature_end = body.start_byte if body is not None else node.end_byte
        symbol = self.add(
            kind=kind, name=name, lookup_key=qualified, node=node, declaration=self.span(outer),
            signature=self.signature_span(node, node, body), body_node=body,
            parent_id=parent.symbol_id if parent else None, modifiers=modifiers, annotations=decorators, extends=bases,
            signature_text=_compact(self.data[node.start_byte:signature_end].decode("utf-8", "replace")).rstrip(":"),
            doc_summary=self.docstring(body),
        )
        if body is not None:
            self.visit_block(body, qualified, symbol, "class" if kind == "class" else "function")

    def visit_assignment(self, statement, prefix: str, parent: Optional[Symbol], scope: str) -> None:
        assignment = next(iter(statement.named_children), None)
        if assignment is None or assignment.type != "assignment":
            return
        left = assignment.child_by_field_name("left")
        if left is None or left.type != "identifier":
            return
        name = self.text(left)
        type_node = assignment.child_by_field_name("type")
        self.add(kind="attribute" if scope == "class" else "variable", name=name, lookup_key=f"{prefix}.{name}",
                 node=statement, signature=self.span(statement), body_node=None,
                 parent_id=parent.symbol_id if parent else None,
                 return_type=_compact(self.text(type_node)) if type_node is not None else None,
                 signature_text=_compact(self.text(statement))[:300])
