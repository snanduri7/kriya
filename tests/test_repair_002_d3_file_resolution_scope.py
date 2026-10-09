"""FILE-RESOLUTION-SCOPE-ESCAPE-001 (BACKEND-FINAL-CLOSURE-005 cohort 2, C2-S4_A; second repair cycle).

Measured live: an enforce unit planned to CREATE a new test file in one module; the Architect-stage owner
resolver (scored tier, on the controller-built goal vocabulary) redirected it to an unrelated existing test in
another module - outside the unit's validated write scope - the Developer was asked to write that file, the write
authority refused the resolver's own target and the run ended PLAN_SCOPE_REVISION_REQUIRED, discarding an earlier
unit's correct, applied fix (FALSE NEGATIVE). Deterministic reproduction on the frozen workspace:
repair-002/prefix/D3_file_resolution_before.txt.

The resolver now decides two things from the unit's own validated plan, per call and reported, never silently:
an owner outside the authorized files is refused (planned path kept, FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE), and a
planned ``create`` is never redirected. Allowed redirect, forbidden redirect, retry (identical decision), stale /
revised scope, and the end-to-end enforce shape are covered. Repository-independent names throughout.
"""
from _t6_harness import enforce_run

from kriya.workflow.file_resolution import (
    FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE,
    planned_new_artifact_paths,
    prefer_existing_artifact_owners,
    resolve_planned_artifact_owners,
)
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow_controller import build_subtask_goal_text

# A two-module repository: the unit may touch core/ only; a similarly named test lives in another module.
PLANNED_NEW_TEST = "core/tests/test_malformed_path.py"
OTHER_MODULE_TEST = "assertions/tests/test_has_no_json_path.py"
CORE_SOURCE = "core/path_compiler.py"
GOAL = ("compile_path must raise ValueError for an empty path instead of returning it.\n"
        "Add tests for the malformed inputs in a new file.\n")


def _plan():
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "existing tests keep passing"}],
        "acceptance_criteria": [{"id": "ac1", "description": "malformed paths are rejected and tested",
                                 "method": "tool", "tool_name": "test"}],
        "subtasks": [
            {"id": "s1", "description": "Reject an empty path in compile_path.", "execution_method": "model",
             "planned_files": [{"path": CORE_SOURCE, "action": "modify"}], "provides": ["fixed_compiler"],
             "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": [],
             "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]},
            {"id": "s2", "description": "Add tests for the specific malformed inputs that previously went unrejected in a new file.",
             "execution_method": "model", "depends_on": ["s1"], "requires": ["fixed_compiler"], "provides": ["new_tests"],
             "planned_files": [{"path": PLANNED_NEW_TEST, "action": "create"}],
             "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
        ]})


def _workspace(tmp_path):
    for relpath, text in _files().items():
        full = tmp_path / relpath
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(text)
    return str(tmp_path)


def _files():
    return {
        "core/__init__.py": "",
        CORE_SOURCE: "def compile_path(text):\n    return text.strip()\n",
        "core/tests/__init__.py": "",
        "assertions/__init__.py": "",
        "assertions/tests/__init__.py": "",
        OTHER_MODULE_TEST: ("from core.path_compiler import compile_path\n\n\n"
                            "def test_has_no_json_path():\n    assert compile_path(' a ') == 'a'\n"),
    }


def _controller_goal():
    plan = _plan()
    return build_subtask_goal_text(plan.subtask_by_id("s2"), 2, 2, plan=plan, grounding_goal=GOAL), plan


def test_the_measured_shape_redirects_only_when_the_resolver_is_unrestricted(tmp_path):
    """Pre-fix behaviour, kept for the legacy direct path (no scope, no plan): with the controller-built goal
    vocabulary the planned new test resolves to the other module's test (scored tier)."""
    workspace = _workspace(tmp_path)
    goal_text, _plan_ = _controller_goal()

    assert prefer_existing_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace) == [OTHER_MODULE_TEST]
    assert prefer_existing_artifact_owners([PLANNED_NEW_TEST], GOAL, workspace) == [PLANNED_NEW_TEST]


def test_a_redirect_outside_the_authorized_files_is_refused_and_reported_and_a_retry_decides_identically(tmp_path):
    workspace = _workspace(tmp_path)
    goal_text, _plan_ = _controller_goal()

    first = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=[PLANNED_NEW_TEST])
    second = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=[PLANNED_NEW_TEST])

    assert first.resolved == [PLANNED_NEW_TEST]
    assert first.refused_redirects == [{"planned": PLANNED_NEW_TEST, "owner": OTHER_MODULE_TEST, "tier": "scored",
                                        "reason_code": FILE_RESOLUTION_REDIRECT_OUT_OF_SCOPE}]
    assert first.new_artifacts_kept == []
    assert second == first  # a retry with the same scope never widens the decision


def test_a_planned_create_is_never_redirected_even_to_an_owner_inside_the_scope(tmp_path):
    workspace = _workspace(tmp_path)
    goal_text, plan = _controller_goal()
    scope = [PLANNED_NEW_TEST, OTHER_MODULE_TEST]

    kept = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=scope,
                                           new_artifact_paths=planned_new_artifact_paths(plan, "s2"))
    redirected = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=scope)

    assert planned_new_artifact_paths(plan, "s2") == [PLANNED_NEW_TEST] and planned_new_artifact_paths(plan, "s1") == []
    assert kept.resolved == [PLANNED_NEW_TEST] and kept.new_artifacts_kept == [PLANNED_NEW_TEST] and kept.refused_redirects == []
    assert redirected.resolved == [OTHER_MODULE_TEST] and redirected.refused_redirects == []  # the allowed redirect


def test_a_redirect_to_an_owner_inside_the_authorized_files_still_happens(tmp_path):
    """The allowed redirect: a Developer-invented parallel path for a file the unit owns (exact-name tier)."""
    workspace = _workspace(tmp_path)

    resolution = resolve_planned_artifact_owners(
        ["src/core/path_compiler.py"], GOAL, workspace, authorized_paths=[CORE_SOURCE])

    assert resolution.resolved == [CORE_SOURCE] and resolution.refused_redirects == []


def test_a_revised_scope_is_honoured_on_the_next_call_and_a_stale_scope_never_leaks(tmp_path):
    """The decision is a function of the scope passed per call - the shape a plan-scope revision produces: the
    same planned path is refused under the old scope and redirected once the revised scope admits the owner."""
    workspace = _workspace(tmp_path)
    goal_text, _plan_ = _controller_goal()

    stale = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=[PLANNED_NEW_TEST])
    revised = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace,
                                              authorized_paths=[PLANNED_NEW_TEST, OTHER_MODULE_TEST])
    stale_again = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace, authorized_paths=[PLANNED_NEW_TEST])

    assert stale.resolved == [PLANNED_NEW_TEST] and len(stale.refused_redirects) == 1
    assert revised.resolved == [OTHER_MODULE_TEST] and revised.refused_redirects == []
    assert stale_again == stale


def test_an_unrestricted_caller_without_a_plan_keeps_the_legacy_decision(tmp_path):
    workspace = _workspace(tmp_path)
    goal_text, _plan_ = _controller_goal()

    resolution = resolve_planned_artifact_owners([PLANNED_NEW_TEST], goal_text, workspace)

    assert resolution.resolved == [OTHER_MODULE_TEST] and resolution.refused_redirects == []


FIXED_SOURCE = ("def compile_path(text):\n    if not text.strip():\n        raise ValueError('empty path')\n"
                "    return text.strip()\n")
NEW_TEST = ("import pytest\n\nfrom core.path_compiler import compile_path\n\n\n"
            "def test_empty_path_is_rejected():\n    with pytest.raises(ValueError):\n        compile_path('   ')\n")


def _responder(role, request):
    from _chaos_harness import benign_roles
    from _protocol_responses import sentinel

    if role != "developer":
        return benign_roles(role, request, target=CORE_SOURCE)
    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    if f'path="{PLANNED_NEW_TEST}"' in system:
        return sentinel(PLANNED_NEW_TEST, analysis="add the malformed-input tests.", content=NEW_TEST)
    if f'path="{OTHER_MODULE_TEST}"' in system:
        # The pre-fix shape: the Developer is asked for the other module's test; it would write there.
        return sentinel(OTHER_MODULE_TEST, analysis="redirected.", content=NEW_TEST)
    return sentinel(CORE_SOURCE, analysis="reject empty paths.", content=FIXED_SOURCE)


def test_end_to_end_the_planned_new_test_is_written_at_its_planned_path_and_the_refusal_is_recorded(tmp_path, monkeypatch):
    """The full enforce loop with the measured shape: unit s2's planned new test is kept in its own module (the
    refused redirect is an authoritative run event), the other module's test is untouched, and the run succeeds -
    before the fix the Developer was asked for the out-of-scope file and the write authority refused it."""
    from _chaos_harness import chaos_config

    cfg = chaos_config()
    cfg.engineering_triage.enabled = True  # the live configuration: the Architect-stage resolver runs for a TASK route
    observed = enforce_run(tmp_path, monkeypatch, _responder, _files(), GOAL, [_plan] * 4, cfg=cfg)

    legacy = observed.result.legacy_result
    assert legacy.get("quality_gates_passed") is True, (
        legacy.get("status"), legacy.get("error"), legacy.get("reason_codes"), legacy.get("failure_type"))
    assert (observed.workspace / PLANNED_NEW_TEST).read_text() == NEW_TEST
    assert (observed.workspace / OTHER_MODULE_TEST).read_text() == _files()[OTHER_MODULE_TEST]
    assert (observed.workspace / CORE_SOURCE).read_text() == FIXED_SOURCE
    mirrored = [r.get("payload") or {} for r in observed.of("mirror.event")]
    kept = [e for e in mirrored if e.get("kind") == "file_resolution.new_artifact_kept"]
    assert kept and kept[0].get("source") == "workflow.architect_file_resolution", (
        sorted({e.get("kind") for e in mirrored}))
    # A validated plan canonicalizes a nonexistent planned file to action=create, so the Architect stage never
    # even attempts the redirect; nothing was refused and the Developer was never asked for the other module's test.
    assert not [e for e in mirrored if e.get("kind") == "file_resolution.redirect_refused"]
    assert f'path="{OTHER_MODULE_TEST}"' not in observed.runtime.transcript()
    assert f'path="{PLANNED_NEW_TEST}"' in observed.runtime.transcript()
    assert not any("outside_authorized_scope" in str(r.get("payload")) for r in observed.of("recovery.decision"))


# ------------------------------------------------------------------ the Developer-path call site (review F5)

def test_a_developer_invented_path_resolving_to_an_out_of_scope_owner_is_refused_at_the_developer_path(tmp_path):
    """The attempt-stage resolver (kriya/workflow/attempt.py): the Developer's result names an invented parallel path
    whose unique exact-name owner exists OUTSIDE the unit's scope. The redirect is refused and recorded (stage
    developer); the Developer's own path is not rewritten to the out-of-scope owner, so the write authority decides
    it as it always has (a policy denial, never a redirected write into the other module). The structured protocol
    rejects an answer naming a path other than the requested one before resolution (measured: INVALID_EDIT_PROTOCOL),
    so the invented path enters through the Developer's result list, the legacy-shaped answer this site still serves."""
    import asyncio
    import json
    from unittest.mock import AsyncMock, patch

    from kriya.config import AppConfig
    from kriya.core import model_runtime
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.workflow.state import GenerationState
    from kriya.workflow.workflow import WorkflowEngine

    planned = "core/compiler.py"          # the unit's own file (a different basename: the invented name is unique)
    other = "legacy/path_compiler.py"      # the only existing file with the invented basename - outside the scope
    invented = "src/path_compiler.py"
    model_runtime.clear_model_runtime_cache()
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    workspace = tmp_path / "ws"
    for rel, text in {"core/__init__.py": "", planned: "def compile_path(text):\n    return text.strip()\n",
                      "legacy/__init__.py": "", other: "def compile_path(text):\n    return text\n"}.items():
        (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
        (workspace / rel).write_text(text)
    import subprocess
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=workspace, check=True, capture_output=True)
    plan = EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [{"id": "ac1", "description": "compiles", "method": "tool", "tool_name": "compile"}],
        "subtasks": [{"id": "s1", "description": "reject an empty path", "execution_method": "model",
                      "planned_files": [{"path": planned, "action": "modify"}], "relevant_global_invariant_ids": ["gi1"],
                      "acceptance_criteria_ids": ["ac1"],
                      "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}]})
    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, user_prompt, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        content = json.dumps({"files": [planned]}) if "File List Planner" in first else "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 100, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.developer.run_generation = AsyncMock(return_value=[
        {"filepath": invented, "content": "def compile_path(text):\n    raise ValueError\n"}])
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests", new=lambda *a, **k: {"success": True, "output": "1 passed"}):
        result = asyncio.run(engine.run_generation_workflow(
            goal="Make compile_path reject an empty path.", workspace_path=str(workspace), predetermined_plan="p",
            predetermined_design="", predetermined_architect_files=[planned], allowed_write_relpaths=[planned],
            structured_plan=plan, current_subtask_id="s1",
            required_verification=[{"type": "tool", "tool_name": "compile", "description": "compile"}],
            approval_callback=AsyncMock(return_value=True)))

    refused = [e for e in events if e.kind == "file_resolution.redirect_refused"]
    assert refused and refused[0].details["stage"] == "developer", [e.kind for e in events][:30]
    assert refused[0].details["planned"] == invented and refused[0].details["owner"] == other
    assert refused[0].details["tier"] == "exact_name" and refused[0].details["authorized_paths"] == [planned]
    assert result["quality_gates_passed"] is False  # the invented path is outside the scope: the write authority refused it
    assert (workspace / other).read_text() == "def compile_path(text):\n    return text\n"  # never redirected into the other module
    assert (workspace / planned).read_text() == "def compile_path(text):\n    return text.strip()\n"
