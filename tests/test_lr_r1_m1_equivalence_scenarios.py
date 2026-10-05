"""LR-R1-M1.10: invariant I-2 over the remaining paths (design §12 T3, §13
M1.10): enforce (success, a refused verified-no-change, Planner repair),
fallback refusal and substitution, and resume.

Same method as tests/test_lr_r1_m1_equivalence.py: every recorder variant
(``full``, ``digest_only``, ``off`` twice, a store that cannot open, a store
whose every write fails) runs the identical scripted scenario; request
bytes, workspace bytes, the result, every trace row's run events (minus the
one pointer event), the RunRecords and the decision ledger must be
identical. Volatile values are measured between the two ``off`` runs, never
guessed.

The enforce scenarios run the real WorkflowController enforce loop and the
real workflow (tests/test_enforce_verified_no_change.py's shape): the model
is scripted at ``LLMClient._request_once`` - every prompt and request
argument reaching it is compared - and only the toolchain gates and the
Planner's structured-output parse are stubbed.
"""
import asyncio
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Callable, Dict, List
from unittest.mock import AsyncMock
from unittest.mock import patch as mock_patch

import pytest
import test_enforce_verified_no_change as enforce_shape
from _chaos_harness import CALC, CALC_WITH_SUB, TEST_SUB, benign_roles, run_direct
from _protocol_responses import sentinel
from test_lr_r1_m1_equivalence import (
    COMPARED,
    VARIANTS,
    _control,
    _mask,
    _tree,
    _untimed,
    _volatile_paths,
    assert_recorder_has_no_effect,
)

from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig
from kriya.core import model_runtime
from kriya.core.attempt_evidence import reader
from kriya.core.attempt_evidence import scope as evidence_scope
from kriya.core.attempt_evidence import writer as writer_module
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.core.state_paths import ENV_STATE_DIR, trace_db_path
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.workflow import WorkflowEngine

FILES = {"calc.py": CALC, "test_calc.py": TEST_SUB}
WRONG = CALC.replace("a + b", "a - b")
_ADDRESS = re.compile(r"0x[0-9a-f]{6,}")


# -- direct scenarios through the chaos runtime ------------------------------------------------

def _always_wrong():
    return lambda role, request: WRONG if role == "developer" else benign_roles(role, request)


def _fallback_substituted(cfg, patch):
    """fb-one passes escalation but cannot serve the call; fb-two serves it."""
    import kriya.workflow.model_transition as model_transition

    cfg.llm_chain = [FallbackModelConfig(model=name, inference_runtime="chaos", context_window=8192,
                                         extra_body={}) for name in ("fb-one:1", "fb-two:1")]
    patch.setattr(model_transition, "fallback_incompatibilities",
                  lambda cfg_, profile, **kwargs: ["TEST_CALL_REJECTION"]
                  if profile.model == "fb-one:1" and "patch_required_files" in kwargs else [])


def _fallback_refused(cfg, patch):
    import kriya.workflow.model_transition as model_transition

    cfg.llm_chain = [FallbackModelConfig(model="fb:1", inference_runtime="chaos", context_window=8192,
                                         extra_body={})]
    patch.setattr(model_transition, "fallback_incompatibilities",
                  lambda cfg_, profile, **kwargs: ["TEST_REJECTION"] if profile.model == "fb:1" else [])


_RESUME = {"phase": "wrong"}


def _resume_responder():
    _RESUME["phase"] = "wrong"
    return lambda role, request: (WRONG if _RESUME["phase"] == "wrong" else CALC_WITH_SUB) \
        if role == "developer" else benign_roles(role, request)


def _resume_drive(engine_factory, workspace):
    """Run 1 fails and leaves its stage checkpoints; run 2 resumes them."""
    run_direct(engine_factory(), "add sub to calc.py", workspace)
    _RESUME["phase"] = "right"
    return run_direct(engine_factory(), "add sub to calc.py", workspace, resume=True)


DIRECT_SCENARIOS = {
    "fallback_refused": dict(make=_always_wrong, configure=_fallback_refused),
    "fallback_substituted": dict(make=_always_wrong, configure=_fallback_substituted),
    "resume": dict(make=_resume_responder, drive=_resume_drive),
}


@pytest.mark.parametrize("scenario", sorted(DIRECT_SCENARIOS))
def test_direct_paths_are_unchanged_by_the_recorder(tmp_path, monkeypatch, scenario):
    spec = dict(DIRECT_SCENARIOS[scenario])
    make = spec.pop("make")
    observations = assert_recorder_has_no_effect(tmp_path, monkeypatch, make, FILES, scenario, **spec)
    full_state = observations["full"]["_state"]
    runs = reader.list_runs(full_state)
    assert runs and all(reader.open_run(full_state, run).verify().status == reader.VERIFIED for run in runs)
    kinds = {r["kind"] for run in runs for r in reader.open_run(full_state, run).records()}
    if scenario.startswith("fallback"):
        assert "fallback.decision" in kinds
    if scenario == "resume":
        assert len(runs) == 2


# -- enforce scenarios through the real controller ---------------------------------------------

def _invalid_plan(criterion):
    plan = enforce_shape._plan(criterion).model_dump()
    plan["subtasks"][1]["depends_on"] = ["s9"]          # an unknown dependency: plan validation refuses it
    return EngineeringPlan.model_validate(plan)


def _enforce_observe(tmp_path: Path, monkeypatch, variant: str, plans: List[Callable], criterion) -> Dict[str, Any]:
    state = tmp_path / "states" / variant
    state.mkdir(parents=True)
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    for name in ("GIT_AUTHOR_DATE", "GIT_COMMITTER_DATE"):
        monkeypatch.setenv(name, "2026-01-01T00:00:00+00:00")
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
    if workspace.exists():
        shutil.rmtree(workspace)
    (workspace / "shop").mkdir(parents=True)
    (workspace / "shop/__init__.py").write_text("")
    (workspace / enforce_shape.SERVICE).write_text(enforce_shape.UNCACHED_SERVICE)
    (workspace / enforce_shape.CONTROLLER).write_text(enforce_shape.CONTROLLER_SRC)
    enforce_shape._git(workspace, "init", "-q")
    enforce_shape._git(workspace, "add", "-A")
    enforce_shape._git(workspace, "commit", "-qm", "base")
    requests: List[str] = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client
        requests.append(_ADDRESS.sub("0x…", _untimed(json.dumps(
            [model, system_prompt, user_prompt, args, kwargs], sort_keys=True, default=str))))
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        service, controller = enforce_shape.SERVICE, enforce_shape.CONTROLLER
        if "File List Planner" in first:
            path = service if service in (user_prompt or "") and controller not in (user_prompt or "") else controller
            content = '{"files": ["%s"]}' % path
        elif "Developer Agent" in first:
            if f'path="{service}"' in system_prompt or "cache find_pet_types" in (user_prompt or ""):
                content = sentinel(service, analysis="cache it.", content=enforce_shape.SERVICE_SRC)
            else:
                content = sentinel(controller, analysis="already calls the cached service.", no_change=True)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    capture = {"off_again": "off", "open_fails": "full", "write_fails": "full"}.get(variant, variant)
    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    engine.planner.run = AsyncMock(return_value="structured plan")
    from kriya.workflow.workflow_controller import WorkflowController

    with monkeypatch.context() as patch:
        patch.setattr(evidence_scope, "_configured_capture", lambda _cfg: capture)
        if variant == "open_fails":
            def refuse(*_args, **_kwargs):
                raise writer_module.RecorderUnavailable("injected: disk full")
            patch.setattr(writer_module.AttemptEvidenceWriter, "__init__", refuse)
        elif variant == "write_fails":
            def fail(self, *_args, **_kwargs):
                raise OSError(28, "injected: No space left on device")
            patch.setattr(writer_module.AttemptEvidenceWriter, "append", fail)
        with mock_patch.object(LLMClient, "_request_once", new=transport), \
             mock_patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
                        new=lambda *a, **k: {"success": True, "output": "ok"}), \
             mock_patch("kriya.tools.validate.PolymorphicValidator.run_tests",
                        new=lambda *a, **k: enforce_shape.TESTS_PASS), \
             mock_patch("kriya.workflow.workflow_controller.parse_planner_structured_output",
                        return_value=(object(), None)), \
             mock_patch("kriya.workflow.workflow_controller.build_engineering_plan_from_planner_output",
                        side_effect=[build(criterion) for build in plans]):
            result = asyncio.run(WorkflowController(engine).execute(
                enforce_shape.GOAL, str(workspace), migration_mode="enforce"))
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = list(db.execute("SELECT run_events FROM runs ORDER BY rowid"))
    events, pointers = [], []
    for (raw,) in rows:
        for event in json.loads(raw or "[]"):
            (pointers if event["kind"] == "evidence.attempt_store" else events).append(
                event["details"] if event["kind"] == "evidence.attempt_store" else event)
    outcome = {"legacy": result.legacy_result,
               "subtasks": [[r.subtask_id, r.status.value, list(r.reason_codes or [])]
                            for r in result.subtask_results]}
    return {
        "requests": json.dumps(requests),
        "tree": _tree(workspace),
        "result": json.loads(_untimed(json.dumps(outcome, default=str))),
        "events": json.loads(_untimed(json.dumps(events, default=str))),
        "run_records": json.loads(_untimed(json.dumps(_control(workspace, "runs")))),
        "decisions": json.loads(_untimed(json.dumps(_control(workspace, "decisions.jsonl")))),
        "planner_calls": engine.planner.run.await_count,
        "_pointers": pointers, "_state": str(state),
    }


ENFORCE_SCENARIOS = {
    "enforce_success": dict(plans=[enforce_shape._plan], criterion=enforce_shape.TOOL_CRITERION),
    "enforce_no_change_refused": dict(plans=[enforce_shape._plan], criterion=enforce_shape.JUDGMENT_CRITERION),
    "enforce_planner_repair": dict(plans=[_invalid_plan, enforce_shape._plan],
                                   criterion=enforce_shape.TOOL_CRITERION),
}


@pytest.mark.parametrize("scenario", sorted(ENFORCE_SCENARIOS))
def test_enforce_paths_are_unchanged_by_the_recorder(tmp_path, monkeypatch, scenario):
    spec = ENFORCE_SCENARIOS[scenario]
    observations = {variant: _enforce_observe(tmp_path, monkeypatch, variant, **spec) for variant in VARIANTS}
    baseline, again = observations["off"], observations["off_again"]
    assert baseline["requests"] != "[]"
    for variant, observed in observations.items():
        assert observed["requests"] == baseline["requests"], (scenario, variant, "requests")
        assert observed["tree"] == baseline["tree"], (scenario, variant, "tree")
        assert observed["planner_calls"] == baseline["planner_calls"], (scenario, variant, "planner")
    for key in COMPARED:
        volatile = _volatile_paths(baseline[key], again[key])
        expected = _mask(baseline[key], volatile)
        for variant, observed in observations.items():
            assert _mask(observed[key], volatile) == expected, (scenario, variant, key)
    # The scenario really exercised its path, and the variants really differed.
    if scenario == "enforce_planner_repair":
        assert baseline["planner_calls"] == 2
    statuses = {s[0]: s[1] for s in baseline["result"]["subtasks"]}
    if scenario == "enforce_success":
        assert statuses == {"s1": "completed", "s2": "completed"}
    assert {p["status"] for p in observations["full"]["_pointers"]} == {"OPEN"}
    assert {p["status"] for p in observations["open_fails"]["_pointers"]} == {"RECORDER_UNAVAILABLE"}
    full_runs = reader.list_runs(observations["full"]["_state"])
    assert full_runs and reader.open_run(observations["full"]["_state"], full_runs[0]).verify().status == \
        reader.VERIFIED
    assert reader.list_runs(observations["off"]["_state"]) == []

