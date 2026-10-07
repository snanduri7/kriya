"""FS-1 deterministic reproducer: the R2 T3 false success
(handover/LR_R1_POST_P5_LIVE_SUCCESS_VALIDATION_R2.md section 4;
handover/FS1_FALSE_SUCCESS_INVESTIGATION.md).

The live specimen (commons-lang CharSetUtils.containsOnly): the candidate
violates an explicit requirement of the goal, the test written for it is never
executed by the runner (no @Test), the model spec-compliance verifier says the
requirement is satisfied, every gate passes, and Kriya reported SUCCESS and
applied the candidate.

Same shape here, through the real WorkflowController enforce loop with real
pytest gates; only the model transport is scripted:
- s1 adds ``contains_only`` that returns True for an empty ``allowed`` (the
  goal requires False);
- s2 adds the check for it under a name pytest never collects
  (``check_contains_only``), the Python counterpart of a missing @Test;
- the spec-compliance verifier answers compliant / REQ satisfied.

Before FS-1 the run reached SUCCESS on that model judgment alone (pinned by
the pre-fix evidence, handover/evidence/fs1/). After FS-1A the test subtask's
unexecuted test delta is a typed, repairable TEST_NOT_EXECUTED naming the
identity; after FS-1B the verifier's "satisfied" is a claim (UNVERIFIED) that
the production policy blocks. The live run had ``runtime_profile:
production``, so the reproducer runs under its requirement policies.
"""
import json
import re
import subprocess
import sys

from _t6_harness import enforce_run

from kriya.workflow.plan_schema import EngineeringPlan

MODULE = "textutil/charset.py"
TESTS = "tests/test_charset.py"
BASE_MODULE = "def contains_any(text, allowed):\n    return any(ch in allowed for ch in text)\n"
BASE_TESTS = ("from textutil.charset import contains_any\n\n\n"
              "def test_contains_any():\n    assert contains_any(\"abc\", \"c\")\n")
# The defect T3 had: an empty set short-circuits to True.
WRONG_MODULE = BASE_MODULE + ("\n\ndef contains_only(text, allowed):\n    if not text or not allowed:\n"
                              "        return True\n    return all(ch in allowed for ch in text)\n")
# The check exists and would fail on the candidate, but pytest never collects it.
UNCOLLECTED_TESTS = BASE_TESTS.replace("import contains_any", "import contains_any, contains_only") + (
    "\n\ndef check_contains_only():\n    assert contains_only(\"hello\", \"helo\")\n"
    "    assert not contains_only(\"hello\", \"\")\n")
GOAL = ("Add a function contains_only(text, allowed) to textutil/charset.py that returns True when every "
        "character of text is in allowed, returns True for an empty text, and returns False for a non-empty "
        "text when allowed is empty, and add tests for it in tests/test_charset.py.")


def _plan():
    def sub(sid, path, **extra):
        return {"id": sid, "description": f"change {path}", "execution_method": "model",
                "planned_files": [{"path": path, "action": "modify"}], "relevant_global_invariant_ids": ["gi1"],
                "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}], **extra}
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [],
        "subtasks": [sub("s1", MODULE, provides=["contains_only"]),
                     sub("s2", TESTS, requires=["contains_only"], depends_on=["s1"])]})


def _responder(role, request):
    from _chaos_harness import benign_roles
    from _protocol_responses import sentinel

    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    user = next((m["content"] for m in reversed(request.messages) if m["role"] == "user"), "")
    if role == "file_list":
        return json.dumps({"files": [TESTS if f'path="{TESTS}"' in system or TESTS in user.split("ONLY")[-1]
                                     else MODULE]})
    if role == "developer":
        if f'path="{TESTS}"' in system:
            return sentinel(TESTS, analysis="add tests.", content=UNCOLLECTED_TESTS)
        return sentinel(MODULE, analysis="add contains_only.", content=WRONG_MODULE)
    if '"compliant"' in system or '"compliant"' in user:
        ids = sorted(set(re.findall(r"\bREQ-\d+\b", system + user)))
        return json.dumps({"compliant": True, "reasoning": "contains_only exists and tests exist.",
                           "missing_requirements": [], "likely_files": [],
                           "requirement_verdicts": [{"id": rid, "verdict": "satisfied",
                                                     "evidence": "contains_only exists; tests exist"}
                                                    for rid in ids]})
    return benign_roles(role, request, target=MODULE)


# What a correct candidate writes: the goal's behaviour, and a test pytest collects.
RIGHT_MODULE = BASE_MODULE + ("\n\ndef contains_only(text, allowed):\n"
                              "    return all(ch in allowed for ch in text)\n")
COLLECTED_TESTS = UNCOLLECTED_TESTS.replace("def check_contains_only", "def test_contains_only")

# The live T3 config: runtime_profile production seals both requirement policies to block.
PRODUCTION = {"requirement_unknown_policy": "block", "requirement_unverified_policy": "block"}
RECORD = {"requirement_unknown_policy": "record", "requirement_unverified_policy": "record"}


def _run(tmp_path, monkeypatch, policy=None, responder=None):
    from _chaos_harness import chaos_config

    cfg = chaos_config()
    cfg.autonomy.spec_compliance_enabled = True
    for key, value in (policy or RECORD).items():
        setattr(cfg.autonomy, key, value)
    files = {"textutil/__init__.py": "", MODULE: BASE_MODULE, "tests/__init__.py": "", TESTS: BASE_TESTS}
    return enforce_run(tmp_path, monkeypatch, responder or _responder, files, GOAL, [_plan] * 4, cfg=cfg)


def _correct_responder(role, request):
    """The same run with a correct candidate (behaviour and a collected test)."""
    from _protocol_responses import sentinel

    system = next((m["content"] for m in request.messages if m["role"] == "system"), "")
    if role == "developer":
        if f'path="{TESTS}"' in system:
            return sentinel(TESTS, analysis="add tests.", content=COLLECTED_TESTS)
        return sentinel(MODULE, analysis="add contains_only.", content=RIGHT_MODULE)
    return _responder(role, request)


def _test_subtask_diagnoses(observed):
    return [r["payload"] for r in observed.of("diagnosis") if r["payload"].get("likely_files") == [TESTS]]


def _test_delta_outcomes(observed):
    outcomes = []
    for record in observed.of("mirror.gate_outcome"):
        blob = record.get("blobs", {}).get("outcome")
        outcome = json.loads(observed.run.blob(blob)) if blob else record["payload"]
        if outcome.get("type") == "test_delta":
            outcomes.append(outcome)
    return outcomes


def _applied_contains_only(workspace, text, allowed):
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from textutil.charset import contains_only; "
            "print(contains_only(sys.argv[2], sys.argv[3]))")
    return subprocess.run([sys.executable, "-c", code, str(workspace), text, allowed], capture_output=True,
                          text=True, check=True).stdout.strip()


def _assert_unexecuted_test_never_succeeds(observed):
    assert observed.result.legacy_result["status"] != "success"
    assert (observed.workspace / MODULE).read_text() == BASE_MODULE   # nothing applied
    diagnoses = _test_subtask_diagnoses(observed)
    # Every attempt of the test subtask is the typed, repairable TEST_NOT_EXECUTED
    # naming the test file (retried, never accepted).
    assert len(diagnoses) >= 2 and {d["reason_code"] for d in diagnoses} == {"TEST_NOT_EXECUTED"}
    deltas = _test_delta_outcomes(observed)
    assert deltas and not any(d["success"] for d in deltas)
    for delta in deltas:
        assert delta["reason_code"] == "TEST_NOT_EXECUTED"
        assert [c["identity"] for c in delta["test_delta"]["delta"]] == ["tests.test_charset.check_contains_only"]
        assert delta["test_execution"]["completeness"] == "COMPLETE"   # judged on a complete report
    # The test gate itself passed on every attempt: the structured report, not the exit code, decided.
    gates = [r["payload"] for r in observed.of("gate.result") if r["payload"].get("gate") == "tests"]
    assert gates and all(g["success"] is True for g in gates)
    assert all(g["test_execution"]["completeness"] == "COMPLETE" for g in gates)


def test_the_specimen_never_succeeds_under_the_production_policy(tmp_path, monkeypatch):
    """The live T3 configuration (FS-1A and FS-1B both active)."""
    _assert_unexecuted_test_never_succeeds(_run(tmp_path, monkeypatch, PRODUCTION))


def test_test_execution_integrity_alone_stops_the_specimen(tmp_path, monkeypatch):
    """Under the record policy no requirement outcome blocks; the unexecuted
    test delta alone (FS-1A) keeps the run from succeeding."""
    _assert_unexecuted_test_never_succeeds(_run(tmp_path, monkeypatch, RECORD))


def test_a_correct_candidate_executes_its_test_delta(tmp_path, monkeypatch):
    """Control: a collected test of the right behaviour passes test-execution
    integrity, and under the record policy the run succeeds."""
    observed = _run(tmp_path, monkeypatch, RECORD, _correct_responder)
    assert observed.result.legacy_result["status"] == "success"
    assert _applied_contains_only(observed.workspace, "hello", "") == "False"
    [delta] = [d for d in _test_delta_outcomes(observed) if d["success"]]
    assert delta["reason_code"] == "TEST_DELTA_EXECUTED"
    assert delta["test_delta"]["executed"] == ["tests.test_charset.test_contains_only"]


def test_a_correct_candidate_with_only_model_judged_requirements_is_blocked_in_production(tmp_path, monkeypatch):
    """FS-1B: the same correct candidate under the production policy. Its
    tests executed (FS-1A passes), but candidate tests close no original
    requirement and the verifier's "satisfied" is a model claim, so every
    requirement stays UNVERIFIED and the production policy blocks: the
    honest outcome is "not verified", never SUCCESS."""
    observed = _run(tmp_path, monkeypatch, PRODUCTION, _correct_responder)
    legacy = observed.result.legacy_result
    assert legacy["status"] != "success"
    assert (observed.workspace / MODULE).read_text() == BASE_MODULE   # nothing applied
    assert any(d["success"] for d in _test_delta_outcomes(observed))
    outcomes = legacy["requirements"]["outcomes"]
    assert outcomes and set(outcomes.values()) == {"unverified"}
    assert {v["model_outcome"] for v in legacy["requirements"]["verdicts"].values()} == {"satisfied"}
    assert "REQUIREMENTS_UNRESOLVED" in json.dumps(legacy, default=str)
