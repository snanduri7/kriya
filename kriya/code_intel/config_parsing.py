"""Structural configuration model: Spring XML, application properties/YAML (E-08).

Same contract as ``parsing.py``: a ``FileStructure`` of plain ``Symbol``s
with exact line and byte spans of the raw bytes named by ``source_digest``,
never raising (a malformed file is ``PARSE_FAILED``). Facts are structural
only: a bean's ``class`` is the text the XML names, never a resolved type.
Configuration is localization and read-only context evidence, never
mutation authority.

Spring XML is parsed namespace-aware with expat: elements and attributes are
matched by LOCAL name, whatever prefix a file binds (``beans:``,
``context:``, ``p:``), and the source bytes are never rewritten.

Symbol kinds (``language`` = ``spring-xml`` / ``properties`` / ``yaml``):
- ``bean``: a ``<bean>`` (or any element with an ``id``); ``return_type`` =
  its ``class``, ``extends`` = its ``parent``, ``implements`` = the bean
  names it references (``ref``/``p:x-ref``/``c:x-ref``/nested ``<ref>``).
- ``bean_property`` / ``bean_constructor_arg``: ``<property>``,
  ``<constructor-arg>`` and ``p:``/``c:`` attributes of a bean.
- ``component_scan``: ``component-scan`` / ``repositories`` base packages.
- ``config_import``: ``<import resource>`` and ``property-placeholder``
  locations.
- ``config_key``: a properties/YAML leaf key (YAML keys flattened with ``.``
  and ``[i]``, the Spring relaxed form). A profile (``<beans profile>``, an
  ``application-<profile>`` file name, a YAML document's
  ``spring.config.activate.on-profile``/``spring.profiles``) is recorded as a
  ``profile:<name>`` modifier.
"""
from __future__ import annotations

import bisect
import os
import re
from typing import Dict, List, Optional, Tuple
from xml.parsers import expat

from kriya.code_intel.model import FileStructure, ParseState, Span, Symbol, source_digest

_SPRING_P = "http://www.springframework.org/schema/p"
_SPRING_C = "http://www.springframework.org/schema/c"
_SPRING_BEANS_ROOT = "beans"
_APPLICATION_RE = re.compile(r"^(application|bootstrap)(-[\w.-]+)?\.(properties|ya?ml)$")
_RESOURCES_RE = re.compile(r"(^|/)src/[^/]+/resources/")
_MESSAGE_BUNDLE_RE = re.compile(r"(?i)messages")
_MAX_VALUE = 160


def config_language_for_path(path: str) -> Optional[str]:
    """The configuration language of ``path``, or None. Spring XML is
    decided by content (``parse_config_file``); every ``.xml`` is a
    candidate. Properties: application/bootstrap files anywhere, and other
    ``src/*/resources`` properties files except message bundles."""
    name = os.path.basename(path)
    extension = os.path.splitext(name)[1].lower()
    if extension == ".xml":
        return "spring-xml"
    if _APPLICATION_RE.match(name):
        return "properties" if extension == ".properties" else "yaml"
    if extension == ".properties" and _RESOURCES_RE.search(path.replace(os.sep, "/")) and not \
            _MESSAGE_BUNDLE_RE.search(name):
        return "properties"
    return None


def file_profile(path: str) -> Optional[str]:
    match = _APPLICATION_RE.match(os.path.basename(path))
    return match.group(2)[1:] if match and match.group(2) else None


class _Builder:
    def __init__(self, language: str, path: str, data: bytes) -> None:
        self.language = language
        self.path = path
        self.data = data
        self.digest = source_digest(data)
        self.symbols: List[Symbol] = []
        self._ids: Dict[str, int] = {}
        self._newlines = [i for i, b in enumerate(data) if b == 10]

    def line_of(self, byte: int) -> int:
        return bisect.bisect_left(self._newlines, byte) + 1

    def span(self, start: int, end: int) -> Span:
        return Span(self.line_of(start), self.line_of(max(end - 1, start)), start, end)

    def add(self, kind: str, name: str, lookup_key: str, start: int, end: int, *, parent_id: Optional[str] = None,
            signature_end: Optional[int] = None, signature_text: str = "", **extra) -> Symbol:
        base = f"{self.language}:{self.path}#{lookup_key}"
        seen = self._ids.get(base, 0)
        self._ids[base] = seen + 1
        declaration = self.span(start, end)
        symbol = Symbol(
            symbol_id=base if seen == 0 else f"{base}#{seen + 1}", language=self.language, kind=kind, name=name,
            lookup_key=lookup_key, path=self.path, source_digest=self.digest, declaration=declaration,
            signature=self.span(start, signature_end or end), body=None, parent_id=parent_id,
            signature_text=" ".join(signature_text.split())[:400], **extra)
        self.symbols.append(symbol)
        return symbol

    def structure(self, state: ParseState = ParseState.PARSED, detail: str = "") -> FileStructure:
        from kriya.code_intel.parsing import parser_identity

        return FileStructure(self.path, self.language, self.digest, state, parser_identity().digest,
                             tuple(self.symbols), detail=detail)


def parse_config_file(path: str, data: bytes) -> FileStructure:
    """Never raises; a file outside the configuration model is UNSUPPORTED."""
    from kriya.code_intel.parsing import parser_identity

    language = config_language_for_path(path)
    if language is None:
        return FileStructure(path, "", source_digest(data), ParseState.UNSUPPORTED, parser_identity().digest,
                             detail="no configuration parser")
    builder = _Builder(language, path, data)
    try:
        if language == "spring-xml":
            if not _SpringXml(builder).run():
                return FileStructure(path, "", builder.digest, ParseState.UNSUPPORTED, parser_identity().digest,
                                     detail="not a Spring beans document")
        elif language == "properties":
            _parse_properties(builder)
        else:
            _parse_yaml(builder)
    except Exception as error:  # malformed input must never look like "no keys"
        return builder.structure(ParseState.PARSE_FAILED, f"{type(error).__name__}: {error}")
    return builder.structure()


# -- Spring XML ---------------------------------------------------------------

def _local(name: str) -> Tuple[str, str]:
    """expat ``"uri local"`` -> (uri, local)."""
    uri, _, local = name.rpartition(" ")
    return uri, local


def _tag_end(data: bytes, start: int) -> int:
    """The byte after the ``>`` closing the tag that starts at ``start``
    (quote-aware: a ``>`` inside an attribute value does not close it)."""
    quote = 0
    for index in range(start, len(data)):
        byte = data[index]
        if quote:
            if byte == quote:
                quote = 0
        elif byte in (34, 39):
            quote = byte
        elif byte == 62:
            return index + 1
    return len(data)


class _SpringXml:
    def __init__(self, builder: _Builder) -> None:
        self.b = builder
        self.stack: List[dict] = []
        self.is_beans = False
        self.root_seen = False
        self.offset = 0

    def run(self) -> bool:
        data = self.b.data
        # Whitespace before the XML declaration is tolerated (common in
        # hand-edited files; strict XML refuses it): parse past it, while
        # every offset stays relative to the real bytes.
        stripped = data.lstrip()
        self.offset = len(data) - len(stripped) if stripped.startswith(b"<?xml") else 0
        parser = expat.ParserCreate(namespace_separator=" ")
        parser.StartElementHandler = lambda name, attrs: self.start(parser, name, attrs)
        parser.EndElementHandler = lambda name: self.end(parser, name)
        parser.Parse(data[self.offset:], True)
        return self.is_beans

    def _profiles(self) -> Tuple[str, ...]:
        found = [p for frame in self.stack for p in frame.get("profiles", ())]
        return tuple(f"profile:{p}" for p in dict.fromkeys(found))

    def _bean_frame(self) -> Optional[dict]:
        return next((f for f in reversed(self.stack) if f.get("symbol_kind") == "bean"), None)

    def start(self, parser, name: str, attrs: Dict[str, str]) -> None:
        uri, local = _local(name)
        start = parser.CurrentByteIndex + self.offset
        frame: dict = {"local": local, "start": start, "attrs": attrs}
        if not self.root_seen:
            self.root_seen = True
            self.is_beans = local == _SPRING_BEANS_ROOT
        plain = {_local(k)[1]: v for k, v in attrs.items() if not _local(k)[0]}
        if local == "beans" and plain.get("profile"):
            frame["profiles"] = [p.strip() for p in re.split(r"[,\s]+", plain["profile"]) if p.strip()]
        if local == "bean" or (plain.get("id") and local not in ("beans",)):
            frame["symbol_kind"] = "bean"
            frame["refs"] = [plain["ref"]] if plain.get("ref") else []
            for key, value in attrs.items():
                attr_uri, attr_local = _local(key)
                if attr_uri in (_SPRING_P, _SPRING_C) and attr_local.endswith("-ref"):
                    frame["refs"].append(value)
        elif local in ("property", "constructor-arg"):
            frame["symbol_kind"] = "bean_property" if local == "property" else "bean_constructor_arg"
        elif local == "ref" and self._bean_frame() is not None and plain.get("bean"):
            self._bean_frame()["refs"].append(plain["bean"])
        self.stack.append(frame)
        if local in ("component-scan", "repositories") and plain.get("base-package"):
            end = _tag_end(self.b.data, start)
            for package in [p for p in re.split(r"[,;\s]+", plain["base-package"]) if p]:
                self.b.add("component_scan", package, package, start, end, signature_text=self._text(start, end),
                           modifiers=self._profiles())
        elif local in ("import", "property-placeholder") and (plain.get("resource") or plain.get("location")):
            end = _tag_end(self.b.data, start)
            for resource in [r for r in re.split(r"[,\s]+", plain.get("resource") or plain["location"]) if r]:
                self.b.add("config_import", resource, resource, start, end, signature_text=self._text(start, end),
                           modifiers=self._profiles())

    def _text(self, start: int, end: int) -> str:
        return self.b.data[start:end].decode("utf-8", "replace")

    def end(self, parser, name: str) -> None:
        frame = self.stack.pop()
        kind = frame.get("symbol_kind")
        if kind is None:
            return
        start = frame["start"]
        signature_end = _tag_end(self.b.data, start)
        # A self-closing element ends with its start tag; otherwise expat
        # reports the end event at the start of the closing tag.
        self_closing = self.b.data[start:signature_end].rstrip(b"> \t\r\n").endswith(b"/")
        end = signature_end if self_closing else _tag_end(self.b.data, parser.CurrentByteIndex + self.offset)
        plain = {_local(k)[1]: v for k, v in frame["attrs"].items() if not _local(k)[0]}
        if kind != "bean":
            owner = self._bean_frame()
            if owner is not None and plain.get("ref"):
                owner["refs"].append(plain["ref"])
            child = {"kind": kind, "name": plain.get("name") or plain.get("index") or plain.get("type") or "",
                     "start": start, "end": end, "signature_end": signature_end,
                     "text": self._text(start, signature_end), "value": plain.get("ref") or plain.get("value")}
            if owner is not None:
                owner.setdefault("children", []).append(child)
            else:
                self._add_child(child, None, None)
            return
        bean_class = plain.get("class") or ""
        bean_name = plain.get("id") or (plain.get("name") or "").split(",")[0].strip() or \
            bean_class.rsplit(".", 1)[-1] or frame["local"]
        symbol = self.b.add("bean", bean_name, bean_name, start, end, signature_end=signature_end,
                            signature_text=self._text(start, signature_end), return_type=bean_class or None,
                            extends=(plain["parent"],) if plain.get("parent") else (),
                            implements=tuple(dict.fromkeys(frame["refs"])), modifiers=self._profiles())
        for key, value in frame["attrs"].items():
            attr_uri, attr_local = _local(key)
            if attr_uri in (_SPRING_P, _SPRING_C):
                self._add_child({
                    "kind": "bean_property" if attr_uri == _SPRING_P else "bean_constructor_arg",
                    "name": attr_local[:-4] if attr_local.endswith("-ref") else attr_local,
                    "start": start, "end": signature_end, "signature_end": signature_end,
                    "text": f'{attr_local}="{value}"', "value": value}, symbol.symbol_id, bean_name)
        # An unnamed constructor-arg is addressed by its position among the
        # bean's constructor args (Spring's own index order).
        positions: Dict[str, int] = {}
        for child in frame.get("children", ()):
            position = positions.get(child["kind"], 0)
            positions[child["kind"]] = position + 1
            if not child["name"]:
                child["name"] = str(position)
            self._add_child(child, symbol.symbol_id, bean_name)

    def _add_child(self, child: dict, parent_id: Optional[str], bean_name: Optional[str]) -> None:
        name = child["name"] or "0"
        self.b.add(child["kind"], name, f"{bean_name}.{name}" if bean_name else name, child["start"], child["end"],
                   parent_id=parent_id, signature_end=child["signature_end"], signature_text=child["text"],
                   return_type=child["value"] or None, modifiers=self._profiles())


# -- properties ---------------------------------------------------------------

_PROPERTY_SEPARATOR_RE = re.compile(r"(?<!\\)[=:]|(?<!\\)\s")


def _parse_properties(builder: _Builder) -> None:
    profile = file_profile(builder.path)
    modifiers = (f"profile:{profile}",) if profile else ()
    data = builder.data
    offset = 0
    lines = data.split(b"\n")
    index = 0
    while index < len(lines):
        start = offset
        raw = lines[index]
        logical = raw.decode("utf-8", "replace")
        end = offset + len(raw)
        offset = end + 1
        index += 1
        # A trailing unescaped backslash continues the logical line.
        while logical.rstrip("\r").endswith("\\") and not logical.rstrip("\r").endswith("\\\\") and index < len(lines):
            logical = logical.rstrip("\r")[:-1] + lines[index].decode("utf-8", "replace").lstrip()
            end = offset + len(lines[index])
            offset = end + 1
            index += 1
        stripped = logical.strip()
        if not stripped or stripped[0] in "#!":
            continue
        separator = _PROPERTY_SEPARATOR_RE.search(stripped)
        key = (stripped[:separator.start()] if separator else stripped).strip().replace("\\", "")
        if not key:
            continue
        builder.add("config_key", key, key, start + (len(raw) - len(raw.lstrip())), end,
                    signature_text=stripped[:_MAX_VALUE], modifiers=modifiers)


# -- YAML -----------------------------------------------------------------------

def _parse_yaml(builder: _Builder) -> None:
    import yaml

    text = builder.data.decode("utf-8")
    # Marks are character indexes; spans are bytes.
    char_to_byte = _CharToByte(text)
    file_level = file_profile(builder.path)
    for document in yaml.compose_all(text, Loader=yaml.SafeLoader):
        if document is None:
            continue
        leaves: List[Tuple[str, object, object]] = []
        _flatten(document, "", leaves)
        profile = file_level
        for key, _, value_node in leaves:
            if key in ("spring.config.activate.on-profile", "spring.profiles") and \
                    isinstance(value_node, yaml.ScalarNode):
                profile = value_node.value
        modifiers = (f"profile:{profile}",) if profile else ()
        for key, key_node, value_node in leaves:
            start = char_to_byte(key_node.start_mark.index)
            end = char_to_byte(value_node.end_mark.index)
            line_text = text[key_node.start_mark.index:value_node.end_mark.index].split("\n", 1)[0]
            builder.add("config_key", key, key, start, max(end, start + 1), signature_text=line_text[:_MAX_VALUE],
                        modifiers=modifiers)


def _flatten(node, prefix: str, out: List) -> None:
    import yaml

    if isinstance(node, yaml.MappingNode):
        for key_node, value_node in node.value:
            if not isinstance(key_node, yaml.ScalarNode):
                continue
            key = f"{prefix}.{key_node.value}" if prefix else str(key_node.value)
            if isinstance(value_node, yaml.ScalarNode):
                out.append((key, key_node, value_node))
            elif isinstance(value_node, yaml.SequenceNode) and all(
                    isinstance(item, yaml.ScalarNode) for item in value_node.value):
                out.append((key, key_node, value_node))
            else:
                _flatten_child(value_node, key, key_node, out)


def _flatten_child(node, key: str, key_node, out: List) -> None:
    import yaml

    if isinstance(node, yaml.SequenceNode):
        for index, item in enumerate(node.value):
            item_key = f"{key}[{index}]"
            if isinstance(item, yaml.ScalarNode):
                out.append((item_key, item, item))
            else:
                _flatten(item, item_key, out)
    else:
        _flatten(node, key, out)


class _CharToByte:
    """Character index -> UTF-8 byte offset, O(1) for ASCII text."""

    def __init__(self, text: str) -> None:
        self.ascii = text.isascii()
        self.text = text

    def __call__(self, index: int) -> int:
        return index if self.ascii else len(self.text[:index].encode("utf-8"))
