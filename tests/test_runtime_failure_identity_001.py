"""RUNTIME-FAILURE-IDENTITY-001 (Stage B run 8, 20261118T161538-f3d8b246).

MEASURED: the identity of a dependent failure was a hash of the failure type and the last 4,000 characters of the run's output with the
grader's reasoning appended. Run 8's third failure carried 39 clock stamps in that tail, so the same failure a second later was a
"different" one; and a model-graded failure's identity contained model text.

The three run 8 shapes:  A = crash, exit 1, no verdict line, graded by the model;
                         B = exit 0, the program's own FAIL "Exception occurred - No bean named 'igniteNode' available";
                         C = exit 0, the program's own FAIL "Protocols are not equal after round-trip".

These check the identity function, its one exit, the transport and the one reader directly. They do not run a workflow.
"""
from types import SimpleNamespace

import pytest

import kriya.workflow.runtime_verification as rv
from kriya.workflow.enforce_loop import dependent_failure_identity
from kriya.workflow.failure import Failure, QualityGateFailure
from kriya.workflow.outcome import last_failure_export
from kriya.workflow.runtime_verification import finite_runtime_failure_identity
from kriya.workflow.verification_contract import (
    RUNTIME_FAILURE_IDENTITY_KEY,
    extract_contract_verdict,
    verification_markers,
)
from kriya.workflow.verifier_replay import failure_identity

COMMAND = ["mvn", "-e", "-q", "compile", "exec:exec", "-Dexec.mainClass=com.example.App"]
NOISE = "[11:26:35] Ignite node started OK (id=7d696600)\n[11:26:35] Topology snapshot [ver=1]\n"
OTHER_NOISE = "[23:59:01] Ignite node started OK (id=0a1b2c3d)\n[23:59:01] Performance suggestions for grid\n[23:59:02] Topology snapshot [ver=1]\n"
BEAN = "[VERIFICATION] FAIL: Exception occurred - No bean named 'igniteNode' available"
NOT_EQUAL = "[VERIFICATION] FAIL: Protocols are not equal after round-trip"


def _outcome(*, exit_code=0, stdout="", stderr="", graded_by="contract", gate="run_verification", command=None, timed_out=False, steps=None,
             reasoning="deterministic"):
    steps = steps if steps is not None else [{"command": list(command or COMMAND), "exit_code": exit_code, "stdout": stdout, "stderr": stderr,
                                              "timed_out": timed_out, "egress": {}, "toolchain_identity": "jdk-17"}]
    output = "".join(str(step.get("stdout") or "") + str(step.get("stderr") or "") for step in steps)
    return {"type": gate, "success": False, "graded_by": graded_by, "commands": [list(command or COMMAND)], "steps": steps,
            "output": output + f"\n\n[Grader reasoning]: {reasoning}", "likely_files": ["src/main/java/com/example/App.java"]}


A = _outcome(exit_code=1, stderr="Exception in thread \"main\" NoSuchBeanDefinitionException: No bean named 'igniteNode' available\n", graded_by="llm",
             reasoning="The application failed to run with exit code 1, throwing a NoSuchBeanDefinitionException.")
B = _outcome(stdout=BEAN + "\n")
C = _outcome(stdout=NOISE + "[RESULT] protocolVersion=1, equals=false\n" + NOT_EQUAL + "\n[11:26:35] Ignite node stopped OK [uptime=00:00:00.164]\n")


def test_the_three_run8_failures_are_three_identities():
    a, b, c = (finite_runtime_failure_identity(outcome) for outcome in (A, B, C))
    assert all(isinstance(identity, str) and len(identity) == 64 for identity in (a, b, c))
    assert a != b and b != c and a != c


def test_the_same_failure_with_other_clock_stamps_and_log_banners_is_the_same_identity():
    later = _outcome(stdout=OTHER_NOISE + "[RESULT] protocolVersion=1, equals=false\n" + NOT_EQUAL + "\n[23:59:02] Ignite node stopped OK [uptime=00:00:01.9]\n",
                     stderr="Nov 19, 2026 1:02:03 AM org.apache.ignite.logger.java.JavaLogger warning\n")
    assert finite_runtime_failure_identity(later) == finite_runtime_failure_identity(C)
    # the legacy identity of the same two outputs differs: this is the measured defect
    assert failure_identity("run_verification", later["output"][-4000:]) != failure_identity("run_verification", C["output"][-4000:])


def test_the_graders_words_are_not_in_the_identity_only_who_graded():
    reworded = {**A, "output": A["output"].replace("failed to run", "could not start"), "likely_files": ["pom.xml"],
                "attribution_reasoning": "a model said so"}
    assert finite_runtime_failure_identity(reworded) == finite_runtime_failure_identity(A)
    assert finite_runtime_failure_identity({**A, "graded_by": "process_exit"}) != finite_runtime_failure_identity(A)


@pytest.mark.parametrize("changed", [
    _outcome(stdout=NOT_EQUAL + "\n", command=["mvn", "-q", "exec:java"]),                         # another command actually ran
    _outcome(stdout=NOT_EQUAL + "\n", exit_code=1),                                                # another exit code
    _outcome(stdout=NOT_EQUAL + "\n", timed_out=True),                                             # it timed out
    _outcome(stdout="[VERIFICATION] FAIL: Protocols differ in the body\n"),                        # another reason
    _outcome(stdout="[VERIFICATION] PASS\n" + NOT_EQUAL + "\n"),                                   # another marker sequence
    _outcome(stdout=NOT_EQUAL + "\n", gate="run_verification_hung"),                               # another gate type
    _outcome(steps=[{"command": COMMAND, "exit_code": 0, "stdout": "", "timed_out": False},
                    {"command": COMMAND, "exit_code": 0, "stdout": NOT_EQUAL + "\n", "timed_out": False}]),   # another step sequence
])
def test_a_changed_deterministic_fact_is_another_identity(changed):
    base = _outcome(stdout=NOT_EQUAL + "\n")
    assert finite_runtime_failure_identity(changed) != finite_runtime_failure_identity(base)
    assert finite_runtime_failure_identity(_outcome(stdout=NOT_EQUAL + "\n")) == finite_runtime_failure_identity(base), "deterministic"


def test_two_crashes_with_no_verdict_line_and_the_same_exit_facts_are_one_identity_by_design():
    """The negative control of the ruling: nothing reads free crash text to tell them apart."""
    other = _outcome(exit_code=1, stderr="java.lang.IllegalStateException: something else entirely\n", graded_by="llm")
    assert finite_runtime_failure_identity(other) == finite_runtime_failure_identity(A)


@pytest.mark.parametrize("outcome", [
    {**C, "type": "test"}, {**C, "type": "compile"}, {**C, "type": "regression_test"},             # compile and test failures stay legacy
    {**C, "graded_by": rv.MANAGED_SERVICE_PROBE_AUTHORITY},                                        # a managed-service probe
    {"type": "run_verification", "graded_by": "managed_service_probe", "commands": [["./serve"]], "managed_service_outcome": "probe_failed"},
    {**C, "steps": []}, {k: v for k, v in C.items() if k != "steps"},                              # no executed step was recorded
])
def test_everything_else_gets_no_canonical_identity(outcome):
    assert finite_runtime_failure_identity(outcome) is None


# ------------------------------------------------------------------ the one exit attaches it; existing diagnostics survive

def _leave(outcome, *, diagnostics=None, required=True):
    recorded, events = [], []
    state = SimpleNamespace(attempt_number=1, record_gate_outcome=recorded.append, record_event=events.append)
    failure = Failure(type=outcome["type"], message="m", raw_output=outcome.get("output", ""), diagnostics=diagnostics)
    deps = SimpleNamespace(logger=SimpleNamespace(warning=lambda *a, **k: None))
    ctx = SimpleNamespace(runtime_verification_required=required)
    if required:
        with pytest.raises(QualityGateFailure) as raised:
            rv._fail_graded_runtime_check(deps, state, ctx, failure, dict(outcome))
        assert raised.value.failure is failure and len(recorded) == 1
    else:
        rv._fail_graded_runtime_check(deps, state, ctx, failure, dict(outcome))
        assert recorded == [] and len(events) == 1
    return failure


def test_the_exit_attaches_the_identity_and_keeps_existing_diagnostics():
    failure = _leave(C, diagnostics={"reason_code": "KEEP_ME"})
    assert failure.diagnostics == {"reason_code": "KEEP_ME", RUNTIME_FAILURE_IDENTITY_KEY: finite_runtime_failure_identity(C)}
    assert _leave(C).diagnostics == {RUNTIME_FAILURE_IDENTITY_KEY: finite_runtime_failure_identity(C)}


def test_a_managed_service_probe_failure_leaves_through_the_same_exit_without_one():
    probe = {"type": "run_verification", "success": False, "graded_by": "managed_service_probe", "commands": [["./serve"]],
             "managed_service_outcome": "probe_failed", "output": "stdout:\n\nstderr:\n"}
    assert _leave(probe).diagnostics is None
    assert _leave({**C, "type": "test"}, diagnostics={"reason_code": "X"}).diagnostics == {"reason_code": "X"}


# ------------------------------------------------------------------ transport and the one reader

def test_last_failure_carries_the_identity_beside_the_unchanged_fields():
    failure = _leave(C)
    exported = last_failure_export(failure)
    assert exported == {"type": "run_verification", "message": "m", "raw_output": C["output"][-4000:],
                        RUNTIME_FAILURE_IDENTITY_KEY: finite_runtime_failure_identity(C)}
    legacy = Failure(type="compile", message="x" * 3000, raw_output="y" * 5000, diagnostics={"reason_code": "R"})
    assert last_failure_export(legacy) == {"type": "compile", "message": "x" * 2000, "raw_output": "y" * 4000}
    assert last_failure_export(None) is None


def test_the_reader_prefers_the_canonical_identity_and_otherwise_returns_exactly_the_legacy_one():
    canonical = last_failure_export(_leave(C))
    assert dependent_failure_identity({"last_failure": canonical}) == finite_runtime_failure_identity(C)
    legacy = {"type": "test", "message": "m", "raw_output": "1 failed in 0.04s"}
    assert dependent_failure_identity({"last_failure": legacy}) == failure_identity("test", "1 failed in 0.04s")
    assert dependent_failure_identity({"last_failure": None}) is None and dependent_failure_identity({}) is None
    # the same failure a second later: unchanged for every reader of this function
    later = last_failure_export(_leave(_outcome(stdout=OTHER_NOISE + NOT_EQUAL + "\n")))
    assert dependent_failure_identity({"last_failure": later}) == dependent_failure_identity({"last_failure": last_failure_export(_leave(_outcome(stdout=NOISE + NOT_EQUAL + "\n")))})


def test_every_reader_in_the_loop_goes_through_the_one_function():
    """`_dependent_failure_identity` is the loop's only way to an identity of a unit result, and it only delegates."""
    import inspect

    import kriya.workflow.enforce_loop as loop

    source = inspect.getsource(loop)
    start = source.index("def _dependent_failure_identity(")
    body = source[start:source.index("def _drop_pre_repair_owner(", start)]
    assert "return dependent_failure_identity(unit_result)" in body and "failure_identity(" not in body.replace("dependent_failure_identity(", "")
    assert source.count("failure_identity(last.get(") == 1, "the legacy hash of a unit result's last failure is taken in one place"


# ------------------------------------------------------------------ the marker parser is one

def test_the_marker_parse_is_structured_ordered_and_the_verdict_reads_it():
    output = "noise\n[VERIFICATION] PASS\n  [VERIFICATION] FAIL: indented, not a marker\n[VERIFICATION] FAIL:   spaced reason  \n[VERIFICATION] FAIL\n"
    assert verification_markers(output) == (("PASS", ""), ("FAIL", "spaced reason"), ("FAIL", ""))
    assert verification_markers("no markers here") == ()
    verdict = extract_contract_verdict(output)
    assert verdict["passed"] is False and verdict["reasoning"].endswith('printed "[VERIFICATION] FAIL" - spaced reason; no reason given.')
    assert extract_contract_verdict("[VERIFICATION] PASS\n[VERIFICATION] PASS\n")["reasoning"].count("(2 check(s), all passed)") == 1
    assert extract_contract_verdict("nothing") is None
