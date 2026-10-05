"""FS-1 deterministic reproducer: the R2 T3 false success
(handover/LR_R1_POST_P5_LIVE_SUCCESS_VALIDATION_R2.md section 4;
handover/FS1_FALSE_SUCCESS_INVESTIGATION.md).

The live specimen (commons-lang CharSetUtils.containsOnly): the candidate
violates an explicit requirement of the goal, the test written for it is never
executed by the runner (no @Test), the model spec-compliance verifier says the
requirement is satisfied, every gate passes, and Kriya reports SUCCESS and
applies the candidate.

Same shape here, through the real WorkflowController enforce loop with real
pytest gates; only the model transport is scripted:
- s1 adds ``contains_only`` that returns True for an empty ``allowed`` (the
  goal requires False);
- s2 adds the check for it under a name pytest never collects
  (``check_contains_only``), the Python counterpart of a missing @Test;
- the spec-compliance verifier answers compliant / REQ satisfied.

``test_the_specimen_reaches_success_on_model_judgment_alone`` pins how it
reaches SUCCESS today. The required behaviour is asserted by
handover/evidence/fs1/test_fs1_required_behaviour.py (run explicitly; it fails
until FS-1 is fixed, and moves here, replacing this pin, when the fix is
authorized - the P1/P4/P5 reproducer convention; no xfail).
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


def _run(tmp_path, monkeypatch):
    from _chaos_harness import chaos_config

    cfg = chaos_config()
    cfg.autonomy.spec_compliance_enabled = True
    files = {"textutil/__init__.py": "", MODULE: BASE_MODULE, "tests/__init__.py": "", TESTS: BASE_TESTS}
    return enforce_run(tmp_path, monkeypatch, _responder, files, GOAL, [_plan] * 4, cfg=cfg)


def _applied_contains_only(workspace, text, allowed):
    code = ("import sys; sys.path.insert(0, sys.argv[1]); from textutil.charset import contains_only; "
            "print(contains_only(sys.argv[2], sys.argv[3]))")
    return subprocess.run([sys.executable, "-c", code, str(workspace), text, allowed], capture_output=True,
                          text=True, check=True).stdout.strip()


def test_the_specimen_reaches_success_on_model_judgment_alone(tmp_path, monkeypatch):
    """Pins today's path (it passes now; its assertions describe the defect)."""
    observed = _run(tmp_path, monkeypatch)
    workspace = observed.workspace

    # Kriya reports SUCCESS and applies the candidate ...
    assert observed.result.legacy_result["status"] == "success"
    assert (workspace / MODULE).read_text() == WRONG_MODULE
    # ... which violates the goal (executed, not inferred) ...
    assert _applied_contains_only(workspace, "hello", "") == "True"   # the goal requires False
    # ... and the only check that would catch it was never executed by any test gate.
    test_outputs = [r for r in observed.of("gate.result") if r["payload"].get("gate") == "tests"]
    assert test_outputs and all(r["payload"]["success"] is True for r in test_outputs)
    assert "check_contains_only" in (workspace / TESTS).read_text()
    for record in test_outputs:
        output = observed.run.blob(record["blobs"]["output"]).decode()
        assert "check_contains_only" not in output
    # The success-authorizing evidence: a MODEL verdict recorded as a satisfied requirement.
    [verdicts] = [json.loads(observed.run.blob(r["blobs"]["event"]))["details"]
                  for r in observed.of("mirror.event") if r["payload"].get("kind") == "requirement.verdicts"]
    assert set(verdicts["outcomes"].values()) == {"satisfied"}
    assert {v["reason_code"] for v in verdicts["verdicts"].values()} == {"VERIFIER_CONFIRMED"}
    terminal = {r["payload"]["gate"]: r["payload"]["success"] for r in observed.of("gate.result")
                if r["payload"].get("stage") == "terminal"}
    assert terminal["original_requirements"] is True and terminal["terminal_obligations"] is True
