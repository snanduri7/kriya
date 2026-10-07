"""CONTEXT-EDIT-PROTOCOL-LARGE-FILE-001: a localized change in a file far larger than the context budget stays
editable through a byte-exact window at the located declaration - never by exposing the whole file.

Measured (blind cohort T6, commons-csv CSVFormat.java, 3369 lines, 32646 tokens): localization adopted the field
``CSVFormat.EXCEL`` (declaration line 1091, exact, margin 7.0) but the edit capability had zero loci - the goal quoted
no code verbatim, there was no failure and no prior anchor, and a field is not a member hint - so no exact window
existed and attempt 1 stopped CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE before any Developer call (budget_chars 3368; a
radius-8 window at line 1091 is 558 chars). Fix: every structural localization candidate carries its declaration
line bound to the digest of the bytes it was read from (retrieval_symbol_loci); a grounded member the known-target
package could not show whole carries its boundary and revision (omitted entry); the capability decision turns both
into loci while the bytes are unchanged. Windows, anchors, revision binding and escalation are the existing ones.
"""
import dataclasses
import hashlib

import pytest
from test_prd016_adaptive_budget import _attempt_ctx, _cfg

from kriya.code_intel.service import CodeIntelligenceService
from kriya.workflow import context_budget as budget
from kriya.workflow.attempt import (
    MAX_SYMBOL_LOCI_PER_FILE,
    _decide_edit_capabilities,
    _edit_capability_loci,
    _record_omitted_members,
)
from kriya.workflow.context_source import member_boundaries_for
from kriya.workflow.edit_capability import (
    ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT,
    ANCHORED_EDIT,
    CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE,
    EXACT_SOURCE_HEADER,
    build_edit_capability,
)
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_integrity import read_shown_text
from kriya.workflow.graph_retrieval import GraphRetrievalResult, _code_intelligence_candidates
from kriya.workflow.state import GenerationState

PATH = "src/main/java/org/example/csv/Big.java"
EXCEL_LINE_TEXT = "    public static final Big EXCEL = DEFAULT.builder().setIgnoreEmptyLines(false).build();"
GOAL = ("Big.EXCEL should not ignore empty lines: a record made of nothing but a line terminator must be kept "
        "exactly like every other record. Do not change DEFAULT.")


def _method(index: int) -> str:
    body = "".join(f"        int v{k} = compute{k}(v{k - 1 if k else 0}, {index});\n" for k in range(10))
    return f"    public int method{index}(int v0) {{\n{body}        return v9;\n    }}\n\n"


def big_java_source() -> str:
    head = "package org.example.csv;\n\npublic final class Big {\n\n"
    first = "".join(_method(i) for i in range(90))
    fields = ("    public static final Big DEFAULT = new Big();\n\n"
              f"{EXCEL_LINE_TEXT}\n\n")
    huge = ("    public int hugeMethod(int v0) {\n"
            + "".join(f"        int h{k} = step{k}(h{k - 1 if k else 0}, v0);\n" for k in range(400))
            + "        return h399;\n    }\n\n")
    last = "".join(_method(i) for i in range(90, 200))
    return head + first + fields + huge + last + "    public Builder builder() { return new Builder(); }\n}\n"


SOURCE = big_java_source()
LINES = SOURCE.splitlines()
EXCEL_LINE = LINES.index(EXCEL_LINE_TEXT) + 1
assert len(LINES) > 3000 and EXCEL_LINE > 1000


@pytest.fixture
def workspace(tmp_path):
    full = tmp_path / PATH
    full.parent.mkdir(parents=True)
    full.write_text(SOURCE)
    return tmp_path


def _state():
    state = GenerationState()
    state.attempt_number = 1
    return state


def _ctx(tmp_path, **overrides):
    cfg = _cfg()
    cfg.llm.inference_runtime = None
    ctx = _attempt_ctx(tmp_path, cfg, developer=None)
    return dataclasses.replace(ctx, goal=GOAL, grounding_goal=None, architect_files=[PATH], expected_files_upfront=[PATH],
                               architect_basename_to_path={"Big.java": PATH}, **overrides)


def _decide(tmp_path, ctx, state=None):
    kwargs = {"known_target_files": [PATH], "existing_code_context": "=== skeleton ===\npublic final class Big {}\n"}
    return _decide_edit_capabilities(state or _state(), ctx, kwargs), kwargs


def _raw_digest(workspace):
    return hashlib.sha256((workspace / PATH).read_bytes()).hexdigest()


# ---------------------------------------------------------------- retrieval: a declaration line for any symbol kind
def test_01_retrieval_records_the_digest_bound_declaration_line_of_a_field(workspace, tmp_path):
    service = CodeIntelligenceService(str(workspace), str(tmp_path / "index.db"))
    try:
        service.refresh([PATH])
        result = GraphRetrievalResult()
        direct, _seeds = _code_intelligence_candidates(result, service, GOAL, [], None, None, None, 12)
    finally:
        service.close()
    assert PATH in direct
    excel = next(c for c in result.localization if c.lookup_key.endswith(".EXCEL"))
    assert excel.kind == "field"
    assert (EXCEL_LINE, _raw_digest(workspace)) in result.retrieval_symbol_loci[PATH]
    assert result.symbol_loci[excel.symbol_id] == (PATH, EXCEL_LINE, _raw_digest(workspace))
    # CI-6: the adopted target leads the loci of its file, whatever its rank was
    later = next(c for c in result.localization if c.path == PATH and c.symbol_id in result.symbol_loci
                 and result.symbol_loci[c.symbol_id][1:] != result.retrieval_symbol_loci[PATH][0])
    assert result.retrieval_symbol_loci[PATH][0] != result.symbol_loci[later.symbol_id][1:]
    result.adopt_decision([later.symbol_id])
    assert result.retrieval_symbol_loci[PATH][0] == result.symbol_loci[later.symbol_id][1:]
    assert len(result.retrieval_symbol_loci[PATH]) == len(set(result.retrieval_symbol_loci[PATH]))


# ---------------------------------------------------------------- the measured shape, then the fix
def test_02_zero_loci_is_the_measured_refusal_and_a_symbol_locus_opens_one_exact_window(workspace):
    with pytest.raises(QualityGateFailure) as refused:
        _decide(workspace, _ctx(workspace))
    assert refused.value.failure.diagnostics["reason_code"] == CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE
    assert "no exact source for any located edit region" in refused.value.failure.message

    ctx = _ctx(workspace, retrieval_symbol_loci={PATH: [(EXCEL_LINE, _raw_digest(workspace))]})
    capabilities, kwargs = _decide(workspace, ctx)
    capability = capabilities[PATH]
    assert capability.operations == (ANCHORED_EDIT,) and capability.full_file is False
    assert capability.loci == (EXCEL_LINE,) and capability.uncovered_loci == ()
    [span] = capability.spans
    assert span.start_line <= EXCEL_LINE <= span.end_line and span.unit == "window"
    assert span.revision == read_shown_text(str(workspace / PATH))[1]
    # a bounded window, never the file: the whole source is 3000+ lines
    assert span.end_line - span.start_line < 40 and len(span.text) < 4000
    assert kwargs["edit_operations"] == {PATH: (ANCHORED_EDIT,)}
    assert EXACT_SOURCE_HEADER in kwargs["existing_code_context"] and EXCEL_LINE_TEXT in kwargs["existing_code_context"]
    assert SOURCE not in kwargs["existing_code_context"]
    # anchors are authorized only inside the window's bytes
    assert capability.anchor_status(EXCEL_LINE_TEXT, SOURCE) is None
    assert capability.anchor_status("    public int method5(int v0) {", SOURCE) == ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT


def test_03_a_locus_of_another_revision_authorizes_nothing(workspace):
    stale = hashlib.sha256(b"an earlier revision").hexdigest()
    ctx = _ctx(workspace, retrieval_symbol_loci={PATH: [(EXCEL_LINE, stale)]})
    with pytest.raises(QualityGateFailure) as refused:
        _decide(workspace, ctx)
    assert refused.value.failure.diagnostics["reason_code"] == CONTEXT_EDIT_PROTOCOL_UNSATISFIABLE
    # the file changes between retrieval and the decision: the recorded digest no longer matches
    digest = _raw_digest(workspace)
    (workspace / PATH).write_text(SOURCE.replace("new Big();", "new Big(1);"))
    with pytest.raises(QualityGateFailure):
        _decide(workspace, _ctx(workspace, retrieval_symbol_loci={PATH: [(EXCEL_LINE, digest)]}))


def test_04_at_most_two_candidates_of_a_file_open_windows_and_the_budget_is_shared(workspace):
    digest = _raw_digest(workspace)
    other = LINES.index("    public static final Big DEFAULT = new Big();") + 1
    third = LINES.index("    public int method5(int v0) {") + 1
    ctx = _ctx(workspace, retrieval_symbol_loci={PATH: [(EXCEL_LINE, digest), (other, digest), (third, digest)]})
    capabilities, _ = _decide(workspace, ctx)
    capability = capabilities[PATH]
    assert MAX_SYMBOL_LOCI_PER_FILE == 2 and capability.loci == tuple(sorted((EXCEL_LINE, other)))
    assert all(any(s.start_line <= locus <= s.end_line for s in capability.spans) for locus in capability.loci)
    assert "src/other.java" not in capabilities  # a locus of another path never reaches this file
    lines = SOURCE.splitlines()
    unrelated = _edit_capability_loci(_state(), dataclasses.replace(ctx, retrieval_symbol_loci={"src/other.java": [(3, digest)]}),
                                      PATH, lines, revision="r", raw_digest=digest)
    assert unrelated == []


# ---------------------------------------------------------------- an omitted grounded member is a locus too
def test_05_a_grounded_member_too_large_to_show_records_its_boundary_and_becomes_a_locus(workspace):
    boundaries = member_boundaries_for(PATH, SOURCE)
    huge = next(b for b in boundaries if b.member_id.endswith("hugeMethod"))
    _rendered, package = budget.build_known_target_context(
        [PATH], str(workspace), None, 1200, member_hints={PATH: [huge.member_id]}, exact_member_budget=800)
    omitted = [entry for entry in package.omitted if entry.get("member_id") == huge.member_id]
    assert omitted, package.omitted
    entry = omitted[0]
    revision = read_shown_text(str(workspace / PATH))[1]
    assert (entry["start_line"], entry["end_line"], entry["revision"]) == (huge.start_line, huge.end_line, revision)
    assert "text" not in entry and "content" not in entry  # lines only, never the bytes

    state = _state()
    _record_omitted_members(state, package.omitted)
    assert state.known_target_omitted_members[PATH] == [(huge.start_line, huge.end_line, revision)]
    ctx = _ctx(workspace)
    assert _edit_capability_loci(state, ctx, PATH, LINES, revision=revision) == [huge.start_line]
    assert _edit_capability_loci(state, ctx, PATH, LINES, revision="another-revision") == []
    capabilities, _ = _decide(workspace, ctx, state)
    [span] = capabilities[PATH].spans
    assert span.start_line <= huge.start_line <= span.end_line and span.end_line < huge.end_line


def test_06_an_anchor_miss_widens_the_window_at_the_same_locus():
    narrow = build_edit_capability(PATH, SOURCE, full_file=False, loci=[EXCEL_LINE], budget_chars=3368, level=0)
    wider = build_edit_capability(PATH, SOURCE, full_file=False, loci=[EXCEL_LINE], budget_chars=3368, level=1)
    assert (narrow.spans[0].end_line - narrow.spans[0].start_line) < (wider.spans[0].end_line - wider.spans[0].start_line)
    assert wider.feasible and len(wider.spans[0].text) <= 3368


# ---------------------------------------------------------------- review reconciliation (2026-10-08)
def test_07_omitted_members_are_replaced_wholesale_by_the_next_package(workspace):
    """Review 3.2: a path shown whole in a later attempt keeps no stale omission record."""
    state = _state()
    entry = {"path": PATH, "rank": 1, "reason": "body_elided", "estimated_tokens": 9, "member_id": "Big.hugeMethod",
             "start_line": 10, "end_line": 400, "revision": "rev-1"}
    _record_omitted_members(state, [entry])
    assert state.known_target_omitted_members == {PATH: [(10, 400, "rev-1")]}
    _record_omitted_members(state, [{"path": "other.java", "rank": 1, "reason": "body_elided", "estimated_tokens": 1,
                                     "member_id": "O.m", "start_line": 1, "end_line": 2, "revision": "r"}])
    assert PATH not in state.known_target_omitted_members
    _record_omitted_members(state, [{"path": PATH, "rank": 1, "reason": "minimum_authority_unfit", "estimated_tokens": 9}])
    assert state.known_target_omitted_members == {}


def test_08_the_workflow_threads_the_retrieval_loci_into_the_attempt_context():
    """Review 3.1: the symbol loci reach AttemptContext from the retrieval result (structural tripwire: the
    retrieval stage assigns them and the AttemptContext construction passes them)."""
    import ast
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "kriya" / "workflow" / "workflow.py").read_text()
    tree = ast.parse(source)
    assigned = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "retrieval_symbol_loci" for t in node.targets)
                and isinstance(node.value, ast.Attribute) and node.value.attr == "retrieval_symbol_loci"]
    assert assigned, "the retrieval stage no longer hands its symbol loci over"
    passed = [kw for node in ast.walk(tree) if isinstance(node, ast.Call) for kw in node.keywords
              if kw.arg == "retrieval_symbol_loci" and isinstance(kw.value, ast.Name) and kw.value.id == "retrieval_symbol_loci"]
    assert passed, "AttemptContext is no longer built with the retrieval's symbol loci"
    from kriya.workflow.attempt import AttemptContext
    assert "retrieval_symbol_loci" in AttemptContext.__dataclass_fields__
