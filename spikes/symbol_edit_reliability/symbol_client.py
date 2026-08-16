"""The ONE genuinely new piece of code this spike depends on - everything
else it uses is Kriya's own real, already-tested production code
(kriya.tools.lsp.JdtlsClient for the jdtls process/JSON-RPC plumbing,
kriya.workflow.edit_safety.apply_anchored_edits for the text-based comparison
side). Kept deliberately small and auditable: one subclass adding exactly one
new LSP capability (textDocument/documentSymbol) the parent class doesn't
have, plus pure, independently-testable functions for symbol-tree flattening,
name resolution, and position-based text replacement.

Every function here has a dedicated test in test_symbol_client.py, run and
verified BEFORE this module is ever used in the actual comparison
(run_comparison.py) - per explicit instruction, this spike's results are
informing a real architectural decision, so nothing here is trusted on faith.

Deliberately NOT attempted in this first version (see README.md's own "what
this does not attempt" section for why each is a real, separate follow-up
rather than an oversight):
  - Overload disambiguation by signature/detail text - a symbol name that
    matches more than one candidate FAILS LOUDLY (raises, lists every
    candidate), the same safety posture kriya.workflow.edit_safety already
    uses for ambiguous text matches and oraios/serena's own
    _find_unique_symbol() uses for ambiguous name paths - never silently
    guesses.
  - Editing just a symbol's BODY (between braces) rather than its whole
    declaration (signature + body) - carving out just the body needs brace-
    matching logic (comment/string-aware, exactly the kind of thing
    kriya.workflow.edit_safety._strip_java_comments_and_strings needed real
    care for) that adds real bug surface for no benefit to what this spike
    is actually measuring. Replacing the symbol's full LSP-provided `range`
    sidesteps brace-matching entirely - the tradeoff is that a caller's
    replacement text must include the full signature, not just a new body.
  - CRLF line endings / non-ASCII UTF-16 surrogate pairs in position math -
    see _position_to_offset()'s own docstring; not applicable to this
    spike's LF-only, ASCII-only Java fixtures, but a real gap for a
    general-purpose version of this code.
"""
import os
from typing import Any, Dict, List, Optional

from kriya.tools.lsp import JDTLS_INIT_TIMEOUT_SECONDS, JdtlsClient


class SymbolAwareJdtlsClient(JdtlsClient):
    """Adds textDocument/documentSymbol on top of kriya.tools.lsp's real
    JdtlsClient - reuses its already-tested process lifecycle, JSON-RPC
    framing, and JAVA_HOME handling completely unchanged. Nothing here
    touches or duplicates the parent's diagnostics logic."""

    async def start(self) -> None:
        """Copied from JdtlsClient.start() (kriya/tools/lsp.py) with exactly
        ONE change: the initialize request's capabilities now declare
        hierarchicalDocumentSymbolSupport. The parent sends an empty
        capabilities dict - per the LSP spec, without this declaration a
        server MAY fall back to the flatter, non-nested SymbolInformation
        response shape instead of the richer DocumentSymbol shape.
        get_document_symbols()/_flatten_symbols() below handle BOTH shapes
        defensively regardless (never assume which one actually came back),
        but requesting the richer shape is still worth doing since it's what
        actually exercises nested-symbol handling. Not calling super().start()
        because that method takes no parameters to override this with - kept
        as close to the parent's own implementation as possible (same data
        dir prefix pattern, same env handling, same timeout) so this diff is
        easy to audit against it line by line."""
        import asyncio
        import tempfile

        self._data_dir = tempfile.mkdtemp(prefix="kriya-jdtls-symbol-spike-data-")
        jdtls_env = {k: v for k, v in os.environ.items() if k != "JAVA_HOME"}
        self.process = await asyncio.create_subprocess_exec(
            self.jdtls_path, "-data", self._data_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=jdtls_env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        root_uri = "file://" + self.project_root
        await self._request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {
                    "textDocument": {"documentSymbol": {"hierarchicalDocumentSymbolSupport": True}},
                },
            },
            timeout=JDTLS_INIT_TIMEOUT_SECONDS,
        )
        self._notify("initialized", {})

    async def get_document_symbols(self, file_path: str, content: str, timeout: float = 30) -> List[Dict[str, Any]]:
        """Opens/updates the file (identical didOpen/didChange pattern to the
        parent's own check_file()) then requests textDocument/documentSymbol.
        Unlike diagnostics - pushed asynchronously as a separate notification
        after indexing, which is why check_file() polls - documentSymbol is a
        direct request/response the LSP spec defines as reflecting the
        CURRENT document state as of the most recent didOpen/didChange, so no
        polling loop is used here. That's a claim about protocol behavior,
        not just an assumption - test_symbol_client.py verifies it actually
        holds against a real jdtls process before anything else in this
        module is trusted."""
        uri = "file://" + file_path
        if uri in self._open_docs:
            self._open_docs[uri] += 1
            self._notify("textDocument/didChange", {
                "textDocument": {"uri": uri, "version": self._open_docs[uri]},
                "contentChanges": [{"text": content}],
            })
        else:
            self._open_docs[uri] = 1
            self._notify("textDocument/didOpen", {
                "textDocument": {"uri": uri, "languageId": "java", "version": 1, "text": content},
            })
        result = await self._request(
            "textDocument/documentSymbol", {"textDocument": {"uri": uri}}, timeout=timeout,
        )
        return result or []


def flatten_symbols(symbols: List[Dict[str, Any]], path_prefix: str = "") -> List[Dict[str, Any]]:
    """Flattens a textDocument/documentSymbol response into a flat list of
    {"name", "name_path", "kind", "range", "detail"} dicts, recursing into
    'children' for nested symbols (inner classes, etc.). Handles BOTH
    possible LSP response shapes defensively, regardless of which one the
    server actually returned:
      - DocumentSymbol (hierarchical): has 'range' directly, optional
        'children'.
      - SymbolInformation (flat): has 'location' (containing 'range')
        instead of a direct 'range', never nested.
    A symbol whose range can't be determined from either shape is skipped,
    not force-included with a fabricated range - callers already treat "not
    found" as an error case they must handle, so silently omitting it is
    safe and correct, unlike inventing a wrong range would be."""
    flat: List[Dict[str, Any]] = []
    for s in symbols:
        name = s.get("name", "")
        name_path = f"{path_prefix}.{name}" if path_prefix else name
        if "range" in s:
            rng = s["range"]
        elif "location" in s and "range" in s["location"]:
            rng = s["location"]["range"]
        else:
            rng = None
        if rng is not None:
            flat.append({
                "name": name, "base_name": _base_name(name), "name_path": name_path,
                "kind": s.get("kind"), "range": rng, "detail": s.get("detail"),
            })
        children = s.get("children") or []
        if children:
            flat.extend(flatten_symbols(children, path_prefix=name_path))
    return flat


def _base_name(name: str) -> str:
    """Strips jdtls's own method-signature suffix from a symbol name. Found
    live via _diagnose.py against a real jdtls response, not assumed from the
    LSP spec (which doesn't mandate any particular naming): jdtls names
    method symbols like "add(int, int)" or "describe(String)", never the
    bare "add"/"describe" a caller would naturally search for. Strips
    everything from the first '(' onward, if present; a symbol with no
    parens (classes, fields, packages) is returned unchanged."""
    paren = name.find("(")
    return name[:paren] if paren != -1 else name


def find_symbols_by_name(symbols_flat: List[Dict[str, Any]], name: str) -> List[Dict[str, Any]]:
    """Returns every flattened symbol whose BASE name (signature suffix
    stripped - see _base_name()) exactly equals `name`. Deliberately simple,
    exact-match-only lookup - no signature-based disambiguation attempted
    (see this module's own docstring): searching by base name alone means
    two overloaded methods are indistinguishable and BOTH match, which
    resolve_unique_symbol() below correctly treats as ambiguous rather than
    guessing. Callers MUST handle the zero-match and multiple-match cases
    themselves; this function never guesses which one was meant."""
    return [s for s in symbols_flat if s["base_name"] == name]


def resolve_unique_symbol(symbols_flat: List[Dict[str, Any]], name: str) -> Dict[str, Any]:
    """The safety-critical entry point every caller should actually use
    instead of find_symbols_by_name() directly: raises ValueError (never
    returns an ambiguous or missing result silently) when the name matches
    zero or more than one symbol - the same posture
    kriya.workflow.edit_safety's own uniqueness checks and oraios/serena's
    _find_unique_symbol() both already use. On ambiguity, the error message
    lists every candidate's own full jdtls name (e.g. "describe(String)" -
    more informative than `detail`, which only ever held the return type in
    testing, not the parameter list) so a caller/log can see exactly what
    was ambiguous, not just that it was."""
    matches = find_symbols_by_name(symbols_flat, name)
    if not matches:
        raise ValueError(f"No symbol named '{name}' found.")
    if len(matches) > 1:
        candidates = [m["name"] for m in matches]
        raise ValueError(
            f"Symbol name '{name}' is ambiguous - {len(matches)} matches found: {candidates}. "
            f"Refusing to guess which one was meant."
        )
    return matches[0]


def _position_to_offset(text: str, line: int, character: int) -> int:
    """Converts an LSP position (0-based line, UTF-16 code-unit offset within
    that line) into a Python string character offset into `text`.

    Two scoped, DOCUMENTED limitations, not silent bugs:
      - Splits on plain '\\n' only. A CRLF ('\\r\\n') file would make every
        line after the first one off by the count of '\\r' characters on
        preceding lines. Fine for this spike's fixtures (LF-only, matching
        the rest of this repo's own line-ending convention) - NOT verified
        safe for CRLF input, and this function does not attempt to detect or
        normalize it.
      - Does not account for UTF-16 surrogate pairs (LSP's `character` field
        is a UTF-16 code-unit count, which differs from a Python codepoint
        count for astral-plane characters like emoji). Fine for this spike's
        ASCII-only Java fixtures - NOT a general-purpose LSP position
        converter."""
    lines = text.split("\n")
    if line < 0 or line >= len(lines):
        raise ValueError(f"line {line} out of range (file has {len(lines)} lines)")
    offset = sum(len(l) + 1 for l in lines[:line])
    offset += character
    if offset > len(text):
        raise ValueError(f"computed offset {offset} exceeds file length {len(text)}")
    return offset


def replace_symbol_range(file_content: str, symbol_range: Dict[str, Any], new_text: str) -> str:
    """Replaces the exact span covered by an LSP `range` (as returned in a
    resolved symbol's "range" field) with `new_text`, splicing directly into
    `file_content`. Replaces the symbol's FULL range (signature + body for a
    method), not just its body - see this module's own docstring for why
    body-only replacement was deliberately not attempted. Raises rather than
    silently proceeding if the range's start comes after its end (a
    malformed/unexpected range - never happened in testing, but refusing to
    guess here matches every other safety check in this module)."""
    start = symbol_range["start"]
    end = symbol_range["end"]
    start_offset = _position_to_offset(file_content, start["line"], start["character"])
    end_offset = _position_to_offset(file_content, end["line"], end["character"])
    if start_offset > end_offset:
        raise ValueError(f"symbol range start offset ({start_offset}) is after end offset ({end_offset}) - refusing to edit")
    return file_content[:start_offset] + new_text + file_content[end_offset:]


async def replace_symbol_by_name(
    client: SymbolAwareJdtlsClient, file_path: str, file_content: str, symbol_name: str, new_declaration: str,
) -> str:
    """The end-to-end convenience function run_comparison.py actually calls:
    resolves `symbol_name` uniquely within `file_content` (via a live
    documentSymbol request), then returns the new full file content with
    that symbol's entire declaration replaced by `new_declaration`. Pure with
    respect to disk - never writes a file itself, so callers/tests can
    inspect the result before deciding what to do with it. Raises (does not
    return a partial/best-effort result) on any resolution failure - the
    caller is expected to catch this and treat it as this scenario's "the
    edit did not apply" outcome, the same as apply_anchored_edits() raising
    ValueError on the text-based side."""
    symbols = await client.get_document_symbols(file_path, file_content)
    flat = flatten_symbols(symbols)
    symbol = resolve_unique_symbol(flat, symbol_name)
    return replace_symbol_range(file_content, symbol["range"], new_declaration)
