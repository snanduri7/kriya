"""GR-R1B ATTEMPT SPEC-GATE MODEL AUTHORITY: LLM output can authorize nothing,
in either direction, at the per-attempt Goal Spec Compliance gate too.

Measured before the fix (this file, no model: the verifier's verdict is
scripted at LLMClient.complete through the PRD-020 lineage harness): a
correct candidate that passed every deterministic gate was failed by a model
"missing" verdict alone - the attempt raised goal_spec_compliance, the
Developer was asked again, and the retry was spent on the model's word. The
model's verdict is now advisory diagnostic evidence at the attempt; the
terminal requirement gate (GR-R0) decides from trusted evidence."""
import json
import sqlite3
from unittest.mock import AsyncMock, patch

import pytest
from test_prd020_requirement_lineage import CONTENT, GOAL, _engine, _events, _gates_pass, _verdict_json

from kriya.core.state_paths import trace_db_path
from kriya.workflow.requirements import derive_requirements


def _workspace(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return workspace


async def _run(engine, workspace, compile_results=None, test_results=None):
    p1, p2 = _gates_pass()
    if compile_results is not None:
        p1 = patch("kriya.tools.validate.PolymorphicValidator.run_compile_check", side_effect=compile_results)
    if test_results is not None:
        p2 = patch("kriya.tools.validate.PolymorphicValidator.run_tests", side_effect=test_results)
    with p1, p2:
        return await engine.run_generation_workflow(goal=GOAL, workspace_path=str(workspace))


def _gates(cfg):
    """Every gate outcome the run persisted (traces.db), in order."""
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT gate_outcomes FROM runs ORDER BY rowid").fetchall()
    return [g for (payload,) in rows for g in json.loads(payload or "[]")]


def _spec_outcomes(cfg):
    return [g for g in _gates(cfg) if g.get("type") == "goal_spec_compliance"]


@pytest.mark.asyncio
async def test_1_a_model_missing_alone_spends_no_retry_and_reaches_the_terminal_gate(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",)))
    workspace = _workspace(tmp_path)
    res = await _run(engine, workspace)
    assert engine.developer.run_generation.await_count == 1  # no Developer retry on the model's word
    assert len(calls["spec"]) == 1
    [gate] = _spec_outcomes(cfg)
    assert gate["success"] is True and gate["status"] == "model_reported_missing"
    assert gate["model_missing_requirements"] and "REQ-3" in gate["model_missing_requirements"][0]
    # Terminal: the unrefuted model negative is not evidence either way - UNVERIFIED, and it blocks (GR-R0).
    assert res["requirements"]["outcomes"]["REQ-3"] == "unverified"
    assert res["quality_gates_passed"] is False and res["failure_category"] == "requirements_unresolved"
    assert not (workspace / "greeting.py").exists()  # nothing applied


@pytest.mark.asyncio
async def test_3_no_closure_and_model_satisfied_is_unverified_and_blocks_under_production(tmp_path):
    """REQUIREMENT-CLOSURE-PLAIN-GOAL-001: under production a goal whose requirements have no deterministic closer
    is refused before the verifier (or any model) runs - the model's "satisfied" can never be heard, let alone
    authorize anything."""
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    res = await _run(engine, _workspace(tmp_path))
    assert res["quality_gates_passed"] is False and res["failure_category"] == "verification_authority_required"
    assert res["requirements_admission"]["residual"] and calls["spec"] == []
    assert engine.developer.run_generation.await_count == 0


@pytest.mark.asyncio
async def test_4_no_closure_and_model_missing_is_unverified_and_blocks_under_production(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",)),
                                 requirement_unknown_policy="block", requirement_unverified_policy="block")
    res = await _run(engine, _workspace(tmp_path))
    assert res["quality_gates_passed"] is False and res["failure_category"] == "verification_authority_required"
    assert engine.developer.run_generation.await_count == 0 and calls["spec"] == []  # refused before any model call


@pytest.mark.asyncio
async def test_5_a_compile_failure_still_fails_the_attempt_whatever_the_model_says(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt))
    fail = {"success": False, "output": "SyntaxError: invalid syntax (greeting.py, line 1)"}
    ok = {"success": True, "output": ""}
    res = await _run(engine, _workspace(tmp_path), compile_results=[fail, ok, ok, ok, ok, ok])
    assert res["quality_gates_passed"] is True
    assert any(g.get("type") == "compile" and g.get("success") is False for g in _gates(cfg))
    assert engine.developer.run_generation.await_count >= 2  # the deterministic failure still drives the retry
    assert len(calls["spec"]) >= 1


@pytest.mark.asyncio
async def test_6_a_test_failure_still_fails_whatever_the_model_says(tmp_path):
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",)))
    # The candidate carries a test, so the deterministic test gate runs (and fails).
    engine.developer.run_generation = AsyncMock(return_value=[
        {"filepath": "greeting.py", "content": CONTENT},
        {"filepath": "test_greeting.py", "content": "from greeting import greet\n\n\ndef test_greet():\n"
                                                     "    assert greet('Ann') == 'Hello, Ann'\n"}])
    failing = {"success": False, "output": "FAILED test_greeting.py::test_greet - AssertionError"}
    res = await _run(engine, _workspace(tmp_path), test_results=[failing] * 20)
    assert res["quality_gates_passed"] is False
    failed = [g for g in _gates(cfg) if g.get("success") is False]
    assert failed and all("test_greeting.py::test_greet" in str(g.get("output")) for g in failed), failed
    assert calls["spec"] == []  # the semantic check never runs on a deterministically failed attempt


@pytest.mark.asyncio
async def test_9_the_model_negative_stays_recorded_as_diagnostic_evidence(tmp_path):
    cfg, engine, _ = _engine(tmp_path, lambda n, prompt: _verdict_json(prompt, missing=("REQ-3",)))
    await _run(engine, _workspace(tmp_path))
    [advisory] = _events(cfg, "spec_compliance.model_advisory")
    assert advisory["status"] == "model_reported_missing" and advisory["authority"] == "advisory_only"
    assert any("REQ-3" in item for item in advisory["missing_requirements"])
    [verdicts] = _events(cfg, "requirement.verdicts")
    assert verdicts["outcomes"]["REQ-3"] == "unverified"
    assert verdicts["verdicts"]["REQ-3"]["model_outcome"] == "violated"
    assert verdicts["requirement_set_digest"] == derive_requirements(GOAL).digest


@pytest.mark.asyncio
async def test_a_contradictory_indeterminate_verdict_is_advisory_too(tmp_path):
    """compliant=false naming no requirement, twice: before the fix a typed
    spec_compliance_indeterminate failure - a retry spent on the model alone."""
    indeterminate = json.dumps({"compliant": False, "reasoning": "unsure", "missing_requirements": [],
                                "likely_files": []})
    cfg, engine, calls = _engine(tmp_path, lambda n, prompt: indeterminate)
    res = await _run(engine, _workspace(tmp_path))
    assert engine.developer.run_generation.await_count == 1
    assert not any(g.get("type") == "spec_compliance_indeterminate" for g in _gates(cfg))
    statuses = [g.get("status") for g in _spec_outcomes(cfg)]
    assert statuses == ["model_indeterminate"]
    # No verdict was given, so nothing is closed: every requirement stays UNKNOWN for the terminal policy.
    assert set(res["requirements"]["outcomes"].values()) == {"unknown"}
