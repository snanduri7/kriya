"""P3 deterministic reproducers (handover/P3_DEVELOPER_PROTOCOL_DECOMPOSITION.md).

Each drives the real production seam that rejected a live Developer answer,
with the live bytes from the LR-R1 R2 attempt-evidence stores
(tests/fixtures/p3/live_cases.json, extracted and checked by
handover/evidence/p3/): the target file at the run's base commit and the
model's own SEARCH/REPLACE or candidate.

- P3-A (R2-T4 s1 a4): a retry's request carries the whole current target
  verbatim (the planned-source section); the model anchors on real text from
  it; _authorize_anchors refuses it as ANCHOR_OUTSIDE_AUTHORITATIVE_CONTEXT,
  because the capability was decided from mandatory text only, before the
  request fit decided what is sent.
- P3-B (R2-T4 s1 a1 -> a2): a SEARCH stitched from two shown member units
  that are not adjacent in the file (ANCHOR_NOT_IN_FILE); the loci it leaves
  lie inside the shown units, so the next capability is identical and the
  retry is refused before any model call (ANCHOR_CONTEXT_NOT_ESCALATED); the
  lines between the two units - the missing fact - are never shown.
- P3-C (R2-T2 s1 a2): the prose-contamination check flags a docstring line
  that is in the unchanged base file, so every candidate for that file is
  rejected whatever the model writes.
"""
import asyncio
import json
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "p3"
LIVE = json.loads((FIXTURES / "live_cases.json").read_text())
ARRAYFILL_PATH = "src/main/java/org/apache/commons/lang3/ArrayFill.java"
ARRAYFILL = (FIXTURES / "ArrayFill.java").read_text()
TREE_PATH = "cssselect2/tree.py"
TREE = (FIXTURES / "cssselect2_tree.py").read_text()
# The member units the live request showed exactly (R2-T4 s1: fill(int[], int) and fill(long[], long)).
MEMBER_UNITS = ((190, 195), (205, 210))


def _lines(start, end, source=ARRAYFILL):
    return "".join(source.splitlines(keepends=True)[start - 1:end])


def _member_items():
    from kriya.workflow.context_package import make_context_item
    from kriya.workflow.edit_safety import content_revision

    return tuple(make_context_item(
        path=ARRAYFILL_PATH, content=_lines(start, end), reason="grounded member", source_type="named_in_request",
        trust_level="repository", tier="member_exact", is_exact=True, revision=content_revision(ARRAYFILL),
        member_id="ArrayFill.fill", start_line=start, end_line=end) for start, end in MEMBER_UNITS)


def _mandatory_units(items):
    return "".join(f"\n=== EXISTING OWNER (member ArrayFill.fill, lines {i.start_line}-{i.end_line}, "
                   f"tier=member_exact): {ARRAYFILL_PATH} ===\n{i.content}" for i in items)


def _workspace(tmp_path):
    target = tmp_path / ARRAYFILL_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(ARRAYFILL)
    return tmp_path


def anchor_outside_retry(tmp_path, monkeypatch, *, planned_source=True):
    """P3-A: the R2-T4 s1 a4 retry through the real Developer choke point.
    Returns (rejection reason or None, the user prompt actually sent)."""
    from _protocol_responses import as_requested
    from _provider_usage import plausible_prompt_tokens
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg, _exact_ollama

    from kriya.agents.agent import DeveloperAgent
    from kriya.core.llm import LLMClient
    from kriya.workflow import attempt
    from kriya.workflow.operations import CodeOperation
    from kriya.workflow.state import GenerationState

    _exact_ollama(monkeypatch)
    _workspace(tmp_path)
    cfg = _cfg(max_tokens=4096)
    cfg.llm.inference_runtime = None  # the packaged runtime (that module's tier double is not registered here)
    cfg.llm.capabilities.preferred_edit_protocol = "search_replace"
    llm = LLMClient(cfg)
    sent = []
    answer = (f"FIX ANALYSIS: add the int[] range overload after fill(int[], int).\n"
              f"SEARCH:\n{LIVE['r2_t4_a4_outside_search']}\nREPLACE:\n{LIVE['r2_t4_a4_replace']}\n")

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        del client, model, args, kwargs
        sent.append(user_prompt)
        return {"content": as_requested(answer, system_prompt, ARRAYFILL_PATH), "reasoning_chars": 0,
                "prompt_tokens": plausible_prompt_tokens(system_prompt, user_prompt), "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    ctx = _attempt_ctx(tmp_path, cfg, DeveloperAgent("developer", llm))
    ctx.goal = ("Add `public static int[] fill(final int[] a, final int fromIndex, final int toIndex, final int val)` "
                "to org.apache.commons.lang3.ArrayFill.")
    ctx.expected_files_upfront = [ARRAYFILL_PATH]
    ctx.architect_files = [ARRAYFILL_PATH]
    ctx.planned_source_files = (ARRAYFILL_PATH,) if planned_source else ()
    state = GenerationState()
    state.attempt_number = 4
    items = _member_items()
    state.known_target_context_items[ARRAYFILL_PATH] = items[0]
    state.known_target_member_items[ARRAYFILL_PATH] = items
    planned = attempt._planned_source_context(ctx, set())
    files = asyncio.run(attempt._run_developer_generation(
        state, ctx, task_description=ctx.goal, design_context="",
        existing_code_context=_mandatory_units(items) + planned, known_target_files=[ARRAYFILL_PATH],
        operation_by_file={ARRAYFILL_PATH: CodeOperation.REPAIR_WITH_PATCH},
        default_operation=CodeOperation.REPAIR_WITH_PATCH,
        optional_sections=attempt._developer_optional_sections(ctx, "", set(), "", planned_source=planned),
    ))
    [file_obj] = [f for f in files if f.get("edits")]
    try:
        attempt._authorize_anchors(state, ARRAYFILL_PATH, file_obj["edits"], ARRAYFILL)
    except ValueError as rejected:
        return str(rejected).split(":", 1)[0], sent[-1]
    return None, sent[-1]


def stitched_anchor_retry(tmp_path):
    """P3-B: the R2-T4 s1 a1 -> a2 sequence on the real capability seams.
    Returns (first rejection reason, second invocation's outcome, both
    capabilities)."""
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg

    from kriya.workflow import attempt
    from kriya.workflow.operations import CodeOperation
    from kriya.workflow.state import GenerationState

    _workspace(tmp_path)
    cfg = _cfg()
    cfg.llm.inference_runtime = None
    ctx = _attempt_ctx(tmp_path, cfg, developer=None)
    ctx.goal = "Add an int[] range fill overload to org.apache.commons.lang3.ArrayFill."
    ctx.expected_files_upfront = [ARRAYFILL_PATH]
    state = GenerationState()
    items = _member_items()
    state.known_target_context_items[ARRAYFILL_PATH] = items[0]
    state.known_target_member_items[ARRAYFILL_PATH] = items

    def invocation(number):
        state.attempt_number = number
        return attempt._decide_edit_capabilities(state, ctx, {
            "known_target_files": [ARRAYFILL_PATH], "existing_code_context": _mandatory_units(items),
            "operation_by_file": {ARRAYFILL_PATH: CodeOperation.REPAIR_WITH_PATCH}})[ARRAYFILL_PATH]

    first = invocation(1)
    edits = [{"search": LIVE["r2_t4_a1_stitched_search"], "replace": LIVE["r2_t4_a1_replace"]}]
    try:
        attempt._authorize_anchors(state, ARRAYFILL_PATH, edits, ARRAYFILL)
        reason = None
    except ValueError as rejected:
        reason = str(rejected).split(":", 1)[0]
        # What the apply path does with an anchor failure (attempt.py, the anchored-edit handler).
        state.budgets.anchor_failure_counts[ARRAYFILL_PATH] = 1
        attempt._record_edit_protocol_failure(state, ARRAYFILL_PATH, "anchored_edit")
        attempt._remember_anchor_loci(state, ARRAYFILL_PATH, edits, ARRAYFILL)
    try:
        second = invocation(2)
        outcome = None
    except attempt.QualityGateFailure as stopped:
        second, outcome = None, stopped.failure.diagnostics.get("reason_code")
    return reason, outcome, first, second


def prose_on_unchanged_line():
    """P3-C: the R2-T2 s1 a2 candidate through the real prose check.
    Returns the rejection's flagged line, or None."""
    from kriya.workflow import attempt
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    state.attempt_number = 2
    try:
        attempt._reject_explanatory_prose(state, TREE_PATH, LIVE["r2_t2_a2_candidate_tree_py"])
    except attempt.QualityGateFailure as rejected:
        return rejected.failure.raw_output
    return None


def prose_end_to_end(tmp_path, monkeypatch, response=None):
    """P3-C through the real direct workflow: the live base tree.py, the live
    R2-T2 s1 a2 Developer response verbatim (sentinel protocol), gates
    stubbed green, so only the Developer-output checks decide. Returns
    (quality_gates_passed, recorded failure types, the target's final bytes)."""
    import subprocess
    from unittest.mock import AsyncMock, patch

    from _edit_protocol_harness import make_config

    from kriya.core import model_runtime
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.state import GenerationState
    from kriya.workflow.workflow import WorkflowEngine

    del monkeypatch
    model_runtime.clear_model_runtime_cache()
    cfg = make_config(tmp_path)
    workspace = tmp_path / "ws"
    (workspace / "cssselect2").mkdir(parents=True)
    (workspace / "cssselect2" / "__init__.py").write_text("")
    (workspace / TREE_PATH).write_text(TREE)
    for args in (["init", "-q"], ["add", "-A"], ["commit", "-qm", "base"]):
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=workspace, check=True,
                       capture_output=True)
    answer = LIVE["r2_t2_a2_developer_response"] if response is None else response
    failures = []
    real_record = GenerationState.record_gate_outcome

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TREE_PATH]})
        elif "Developer Agent" in first:
            content = answer
        else:
            content = "Review: Approved"
        prompt_tokens = len(((system_prompt or "") + (user_prompt or "")).encode("utf-8")) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": prompt_tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    def record(state, outcome):
        failures.append(outcome.get("type"))
        return real_record(state, outcome)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_gate_outcome", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "1 passed"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal="Add a read-only property `depth` to `cssselect2.tree.ElementWrapper` in cssselect2/tree.py that "
                 "returns the number of ancestors of the element (0 for the root element).",
            workspace_path=str(workspace), predetermined_plan=f"Repair {TREE_PATH}", predetermined_design="",
            predetermined_architect_files=[TREE_PATH], approval_callback=AsyncMock(return_value=True)))
    return bool(result.get("quality_gates_passed")), failures, (workspace / TREE_PATH).read_text()


def candidate_added_lines(base=TREE, candidate=None):
    """Lines the R2-T2 candidate adds to the base (multiset difference)."""
    from collections import Counter

    candidate = LIVE["r2_t2_a2_candidate_tree_py"] if candidate is None else candidate
    return list((Counter(candidate.splitlines()) - Counter(base.splitlines())).elements())


__all__ = ["ARRAYFILL", "ARRAYFILL_PATH", "LIVE", "MEMBER_UNITS", "TREE", "TREE_PATH", "anchor_outside_retry",
           "candidate_added_lines", "prose_end_to_end", "prose_on_unchanged_line", "stitched_anchor_retry"]
