"""Dedicated, exhaustive tests for symbol_client.py - the one genuinely new
module this spike depends on. Per explicit instruction, this spike's result
is informing a real architectural decision, so nothing in symbol_client.py is
trusted until it's independently verified here FIRST, separately from
run_comparison.py.

Two tiers:
  - Pure unit tests (no jdtls needed): position math, range replacement,
    symbol flattening/lookup - hand-constructed inputs, fully deterministic.
  - Live integration tests against a REAL jdtls process and the REAL fixture
    project (project/) - verifies symbol_client.py's claims about LSP
    protocol behavior actually hold, not just that the code runs without
    raising.

Found live while first writing this suite, via a separate throwaway
diagnostic script (_diagnose.py, since deleted) BEFORE trusting any live
test result here: `textDocument/documentSymbol` needs no polling once jdtls
is ready (a direct request/response, confirmed - 0.0s response time in the
diagnostic, run as a plain single-event-loop script).

The FIRST version of this suite's `live_client` fixture used
`@pytest.fixture(scope="module")` and every live test hung until timeout.
The initial (WRONG) hypothesis was that jdtls needed real settle time for its
own project import before it could answer - a 30s sleep was added and the
suite passed, which looked like confirmation. It wasn't: re-running with that
sleep set to 0 (below) still passes, in ~4s instead of ~32s, once the ACTUAL
bug was found and fixed - `pytest-asyncio`'s function-scoped default event
loop doesn't match a module-scoped async fixture; the client's background
`_reader_task` (which pumps jdtls's responses back to whichever coroutine is
awaiting them) gets created against the fixture-setup loop, then orphaned the
moment a later test runs on its own, different, per-function loop - so no
request from that point on ever gets a response. Fixed by aligning both the
fixture AND every dependent test to one shared loop scope
(`@pytest_asyncio.fixture(scope="module", loop_scope="module")` +
`@pytest.mark.asyncio(loop_scope="module")`) - a settle delay was never the
real fix, just a coincidentally-passing red herring that happened to also
be true (jdtls DOES need some real time to start, just not anywhere near
30s for a 2-file fixture project, and the diagnostic's own single-loop,
zero-hang success at every timing already showed that).

Run: .venv/bin/pytest spikes/symbol_edit_reliability/test_symbol_client.py -v
(Kriya's own venv - pytest/pytest-asyncio are already there, and this spike
needs no other new Python dependency, just the `jdtls` binary on PATH.)
"""
import os
import shutil
import sys
from pathlib import Path

import pytest
import pytest_asyncio

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for kriya.*
sys.path.insert(0, str(Path(__file__).resolve().parent))  # this dir, for symbol_client (flat import,
                                                            # matching every other spike this session -
                                                            # spikes/ itself is not a package)

from kriya.tools.lsp import find_jdtls  # noqa: E402
from symbol_client import (  # noqa: E402
    SymbolAwareJdtlsClient,
    _base_name,
    _position_to_offset,
    find_symbols_by_name,
    flatten_symbols,
    replace_symbol_by_name,
    replace_symbol_range,
    resolve_unique_symbol,
)

FIXTURE_PROJECT = str(Path(__file__).resolve().parent / "project")
CALCULATOR_JAVA = str(Path(FIXTURE_PROJECT) / "src/main/java/com/example/Calculator.java")
OTHER_JAVA = str(Path(FIXTURE_PROJECT) / "src/main/java/com/example/Other.java")

pytestmark = pytest.mark.skipif(find_jdtls() is None, reason="jdtls not found on PATH")


# --- Pure unit tests: _position_to_offset ---

def test_position_to_offset_start_of_file():
    assert _position_to_offset("abc\ndef\n", 0, 0) == 0

def test_position_to_offset_mid_first_line():
    assert _position_to_offset("abc\ndef\n", 0, 2) == 2

def test_position_to_offset_start_of_second_line():
    text = "abc\ndef\n"
    assert _position_to_offset(text, 1, 0) == 4  # after "abc\n"
    assert text[4] == "d"

def test_position_to_offset_mid_third_line():
    text = "abc\ndef\nghi\n"
    offset = _position_to_offset(text, 2, 1)
    assert text[offset] == "h"

def test_position_to_offset_rejects_out_of_range_line():
    with pytest.raises(ValueError):
        _position_to_offset("abc\n", 5, 0)

def test_position_to_offset_rejects_offset_past_end():
    with pytest.raises(ValueError):
        _position_to_offset("abc\n", 0, 100)


# --- Pure unit tests: replace_symbol_range ---

def test_replace_symbol_range_replaces_exact_span():
    content = "before\nTARGET\nafter\n"
    rng = {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": len("TARGET")}}
    result = replace_symbol_range(content, rng, "REPLACED")
    assert result == "before\nREPLACED\nafter\n"

def test_replace_symbol_range_multiline_span():
    content = "before\nline1\nline2\nafter\n"
    rng = {"start": {"line": 1, "character": 0}, "end": {"line": 2, "character": len("line2")}}
    result = replace_symbol_range(content, rng, "NEW")
    assert result == "before\nNEW\nafter\n"

def test_replace_symbol_range_rejects_inverted_range():
    content = "abc\ndef\n"
    rng = {"start": {"line": 1, "character": 0}, "end": {"line": 0, "character": 0}}
    with pytest.raises(ValueError):
        replace_symbol_range(content, rng, "X")


# --- Pure unit tests: flatten_symbols / find_symbols_by_name / resolve_unique_symbol ---

def test_flatten_symbols_handles_hierarchical_shape_with_children():
    doc_symbols = [{
        "name": "Outer", "kind": 5, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 10, "character": 1}},
        "children": [
            {"name": "inner", "kind": 6, "range": {"start": {"line": 1, "character": 0}, "end": {"line": 2, "character": 1}}},
        ],
    }]
    flat = flatten_symbols(doc_symbols)
    names = [s["name"] for s in flat]
    assert names == ["Outer", "inner"]
    assert flat[1]["name_path"] == "Outer.inner"

def test_flatten_symbols_handles_flat_symbolinformation_shape():
    sym_info = [{
        "name": "foo", "kind": 6,
        "location": {"uri": "file:///x.java", "range": {"start": {"line": 3, "character": 0}, "end": {"line": 4, "character": 1}}},
    }]
    flat = flatten_symbols(sym_info)
    assert len(flat) == 1
    assert flat[0]["name"] == "foo"
    assert flat[0]["range"]["start"]["line"] == 3

def test_flatten_symbols_skips_symbol_with_no_determinable_range():
    malformed = [{"name": "noRange", "kind": 6}]
    assert flatten_symbols(malformed) == []


# --- Pure unit tests: _base_name ---
# Found live via _diagnose.py against a real jdtls response, not assumed:
# jdtls names method symbols with their signature embedded, e.g.
# "add(int, int)" - find_symbols_by_name() must strip that suffix before
# matching, or a bare "add" lookup would never match anything at all.

def test_base_name_strips_method_signature_suffix():
    assert _base_name("add(int, int)") == "add"
    assert _base_name("describe(String)") == "describe"

def test_base_name_no_op_when_no_parens_present():
    assert _base_name("Calculator") == "Calculator"
    assert _base_name("com.example") == "com.example"


# Hand-rolled dicts are deliberately NOT used to test find_symbols_by_name/
# resolve_unique_symbol below - a dict that drifts from what flatten_symbols()
# actually produces (e.g. missing the base_name key this fix just added)
# would silently test something unreal. Always go through flatten_symbols()
# itself with a realistic, jdtls-shaped input instead, so these tests can
# never disagree with what the rest of the module actually does.

def test_find_symbols_by_name_matches_on_base_name_not_full_signature():
    doc_symbols = [{
        "name": "add(int, int)", "kind": 6,
        "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 1}},
    }]
    flat = flatten_symbols(doc_symbols)
    assert [s["name"] for s in find_symbols_by_name(flat, "add")] == ["add(int, int)"]
    assert find_symbols_by_name(flat, "add(int, int)") == []  # the full signature is NOT the lookup key

def test_resolve_unique_symbol_raises_on_zero_matches():
    doc_symbols = [{"name": "a(int)", "kind": 6, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}]
    flat = flatten_symbols(doc_symbols)
    with pytest.raises(ValueError, match="No symbol named"):
        resolve_unique_symbol(flat, "b")

def test_resolve_unique_symbol_raises_on_ambiguous_overloads():
    doc_symbols = [
        {"name": "describe()", "kind": 6, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 1, "character": 1}}},
        {"name": "describe(String)", "kind": 6, "range": {"start": {"line": 2, "character": 0}, "end": {"line": 3, "character": 1}}},
    ]
    flat = flatten_symbols(doc_symbols)
    with pytest.raises(ValueError, match="ambiguous") as exc_info:
        resolve_unique_symbol(flat, "describe")
    # Both full-signature candidate names should be named in the error, not just "2 matches".
    assert "describe()" in str(exc_info.value)
    assert "describe(String)" in str(exc_info.value)

def test_resolve_unique_symbol_returns_the_one_match():
    doc_symbols = [{"name": "add(int, int)", "kind": 6, "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}}}]
    flat = flatten_symbols(doc_symbols)
    result = resolve_unique_symbol(flat, "add")
    assert result["name"] == "add(int, int)"


# --- Live integration tests against a real jdtls process + the real fixture project ---


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def live_client():
    jdtls_path = find_jdtls()
    client = SymbolAwareJdtlsClient(FIXTURE_PROJECT, jdtls_path)
    await client.start()
    yield client
    await client.shutdown()


@pytest.mark.asyncio(loop_scope="module")
async def test_live_get_document_symbols_finds_all_top_level_methods(live_client):
    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    symbols = await live_client.get_document_symbols(CALCULATOR_JAVA, content)
    flat = flatten_symbols(symbols)
    names = sorted(s["name"] for s in flat)
    base_names = [s["base_name"] for s in flat]
    # jdtls names method symbols with their signature embedded (e.g.
    # "describe(String)"), confirmed live via _diagnose.py - the two
    # overloads are distinct full names sharing one base_name.
    assert "describe()" in names, names
    assert "describe(String)" in names, names
    assert base_names.count("describe") == 2, f"expected 2 overloaded 'describe' symbols by base_name, got: {base_names}"
    assert "add" in base_names
    assert "computeTotal" in base_names

@pytest.mark.asyncio(loop_scope="module")
async def test_live_symbol_ranges_are_sane_and_ordered(live_client):
    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    symbols = await live_client.get_document_symbols(CALCULATOR_JAVA, content)
    flat = flatten_symbols(symbols)
    add_symbol = resolve_unique_symbol(flat, "add")
    start, end = add_symbol["range"]["start"], add_symbol["range"]["end"]
    assert (end["line"], end["character"]) > (start["line"], start["character"])
    # The symbol's range, spliced out of the real file, should contain the
    # method's own real body text - a direct, real-data check that the
    # position math and the LSP range agree with each other, not just that
    # neither individually raised.
    start_off = _position_to_offset(content, start["line"], start["character"])
    end_off = _position_to_offset(content, end["line"], end["character"])
    assert "return a + b;" in content[start_off:end_off]

@pytest.mark.asyncio(loop_scope="module")
async def test_live_replace_symbol_by_name_end_to_end_clean_case(live_client):
    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    new_content = await replace_symbol_by_name(
        live_client, CALCULATOR_JAVA, content, "add",
        "public int add(int a, int b) {\n        return a + b + 1;\n    }",
    )
    assert "return a + b + 1;" in new_content
    # Nothing else in the file should have changed - splice, not full rewrite.
    assert "computeTotal" in new_content
    assert "describe" in new_content
    assert new_content.count("public int add") == 1

@pytest.mark.asyncio(loop_scope="module")
async def test_live_replace_symbol_by_name_raises_on_ambiguous_overload(live_client):
    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    with pytest.raises(ValueError, match="ambiguous"):
        await replace_symbol_by_name(live_client, CALCULATOR_JAVA, content, "describe", "irrelevant")

@pytest.mark.asyncio(loop_scope="module")
async def test_live_replace_symbol_by_name_raises_when_symbol_from_a_different_file(live_client):
    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    # "subtract" only exists in Other.java, not Calculator.java.
    with pytest.raises(ValueError, match="No symbol named"):
        await replace_symbol_by_name(live_client, CALCULATOR_JAVA, content, "subtract", "irrelevant")

@pytest.mark.asyncio(loop_scope="module")
async def test_live_result_is_structurally_valid_java(live_client):
    """Reuses Kriya's own real find_structural_corruption() (not a
    reimplementation) to catch any position-math bug in this module that
    would otherwise silently produce broken output - the same check the real
    retry loop runs before ever reaching a compiler."""
    from kriya.workflow.edit_safety import find_structural_corruption

    with open(CALCULATOR_JAVA, encoding="utf-8") as f:
        content = f.read()
    new_content = await replace_symbol_by_name(
        live_client, CALCULATOR_JAVA, content, "computeTotal",
        "public int computeTotal(int a, int b, int bonus) {\n        return a + b + bonus + 1;\n    }",
    )
    assert find_structural_corruption("Calculator.java", new_content) is None
