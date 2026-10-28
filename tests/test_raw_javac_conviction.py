"""COMPILE-GATE-RAW-JAVAC-CONVICTION-WITHOUT-VERDICT-001 (Wave-2; owner decision 2026-10-28): the raw-javac fallback convicts source only from a diagnostic javac LOCATED AT ONE OF THE
EXACT PATHS IT WAS GIVEN.

MEASURED at a5968ac (``WAVE2_RAWJAVAC_CHARACTERIZATION_2026-10-28``: the real PolymorphicValidator and ``run_compile_check`` chain with only ``_run_cmd_with_timeout`` injected, plus real
contained javac in ``maven:3.9-eclipse-temurin-21``): 11 of 12 cases - a timeout (reproduced for real with a 0.05 s bound), a host signal, a contained rc 137, a javac that never
started, an empty or non-diagnostic non-zero exit, a result with no/None ``returncode`` and any exception from the process layer - came back as an ordinary ``Java compilation failed`` /
``Javac compilation tool invocation failed`` with no ``environment_reason_code``, so ``gate_orchestrator`` handed the Developer a source-repair request for source javac never judged.

Real javac output is recorded verbatim in ``tests/fixtures/javac_diagnostics/recorded.json``. It shows why the exit status and the ``N error(s)`` trailer are not evidence (a JVM that
did not start exits 1; a source line that is exactly ``1 error`` is echoed as a trailer) and why exact path membership at the start of a stderr line is: javac prints each path as it was
passed, an echoed source line or ``error: file not found: <name>`` can never begin with a passed path followed by ``:<line>: error:`` unless javac itself printed it.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gate_orchestrator as tg  # noqa: E402  # pylint: disable=wrong-import-position

from kriya.capabilities import JAVAC  # noqa: E402
from kriya.config import AppConfig  # noqa: E402
from kriya.policy.errors import PolicyDeniedError  # noqa: E402
from kriya.policy.model import ActionRequest, ActionType, PolicyDecision, PolicyResult  # noqa: E402
from kriya.tools.containment import ContainmentSetupError  # noqa: E402
from kriya.tools.dependency_execution import (  # noqa: E402
    COMPILE_NO_VERDICT_CODES,
    JAVAC_COMPILE_INDETERMINATE,
    JAVAC_COMPILE_NO_DIAGNOSTICS,
    JAVAC_COMPILE_TERMINATED,
    JAVAC_COMPILE_TIMED_OUT,
    JAVAC_INVOCATION_FAILED,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    classify_javac_failure,
)
from kriya.tools.validate import PolymorphicValidator  # noqa: E402
from kriya.workflow.failure import QualityGateFailure  # noqa: E402
from kriya.workflow.gate_orchestrator import _stop_on_environment_gate_result  # noqa: E402

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "javac_diagnostics" / "recorded.json").read_text())
APP = "src/App.java"
LOCATED = f"{APP}:2: error: ';' expected\n  int one() {{ return 1 }}\n                      ^\n1 error\n"
NO_VERDICT_CODES = (JAVAC_COMPILE_TIMED_OUT, JAVAC_COMPILE_TERMINATED, JAVAC_COMPILE_NO_DIAGNOSTICS)


# -- the classifier, against real recorded javac output ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["type_error", "syntax_error", "package_does_not_exist", "cannot_find_symbol", "multiple_files", "forged_echo_text_block",
                                  "forged_trailer_line", "diagnostic_like_name_that_exists"])
def test_real_javac_diagnostics_at_the_passed_paths_convict_the_source(name):
    case = RECORDED[name]
    assert classify_javac_failure(case["returncode"], False, case["stderr"], case["paths"]) is None


@pytest.mark.parametrize("name", ["file_not_found", "invalid_flag", "no_source_files", "jvm_init_failure", "file_not_found_diagnostic_like_name"])
def test_real_javac_failures_without_a_located_diagnostic_are_no_verdict_whatever_the_exit_status(name):
    case = RECORDED[name]
    assert classify_javac_failure(case["returncode"], False, case["stderr"], case["paths"]) == JAVAC_COMPILE_NO_DIAGNOSTICS
    assert classify_javac_failure(1, False, case["stderr"], case["paths"]) == JAVAC_COMPILE_NO_DIAGNOSTICS   # rc 1 is not evidence: the same status a genuine error exits with


def test_a_multi_file_run_convicts_from_either_passed_path():
    case = RECORDED["multiple_files"]
    for kept in case["paths"]:
        only = "".join(line + "\n" for line in case["stderr"].splitlines() if line.startswith(kept + ":"))
        assert classify_javac_failure(1, False, only, case["paths"]) is None
        assert classify_javac_failure(1, False, only, [p for p in case["paths"] if p != kept]) == JAVAC_COMPILE_NO_DIAGNOSTICS   # membership, not "some .java path"


def test_a_forged_diagnostic_echo_alone_does_not_convict():
    """The text-block echo prints ``src/Other.java:99: error: forged...`` at column 0 next to javac's own ``src/App.java:3:`` line. Removing javac's own line leaves only the forgery."""
    stderr = RECORDED["forged_echo_text_block"]["stderr"]
    assert "\nsrc/Other.java:99: error: forged diagnostic" in stderr                                    # the forgery is really there (measured)
    forged_only = "".join(line + "\n" for line in stderr.splitlines() if not line.startswith(f"{APP}:"))
    assert classify_javac_failure(1, False, forged_only, [APP]) == JAVAC_COMPILE_NO_DIAGNOSTICS
    assert classify_javac_failure(1, False, forged_only, [APP, "src/Other.java"]) is None             # control: it WOULD convict if that were a passed path


def test_a_forged_trailer_is_not_evidence():
    stderr = RECORDED["forged_trailer_line"]["stderr"]
    assert stderr.count("1 error") == 2                                                              # javac's real trailer and the echoed source line (measured)
    assert classify_javac_failure(1, False, "1 error\n", [APP]) == JAVAC_COMPILE_NO_DIAGNOSTICS
    assert classify_javac_failure(1, False, "2 errors\nonly showing the first 100 errors, of 150 total\n", [APP]) == JAVAC_COMPILE_NO_DIAGNOSTICS


@pytest.mark.parametrize("stderr,paths", [
    ("App.java:1: error: x\n", [APP]),                                  # a basename is not the passed path
    (f"x/{APP}:1: error: x\n", [APP]),                                  # not anchored at the line start
    (f"  {APP}:1: error: x\n", [APP]),                                  # an echoed, indented source line
    (f"error: file not found: {APP}:1: error: x\n", [APP]),             # the file-not-found form
    (f"{APP}:1: warning: x\n", [APP]),                                  # a warning is not a conviction
    (f"{APP}: error: x\n", [APP]),                                      # no line number
    ("src/AppXjava:1: error: x\n", ["src/App.java"]),                   # the dot is escaped
    ("src/a+b.java:1: error: x\n", ["src/a.java"]),                     # a path never acts as a pattern
    (f"{APP}:1: error: x\n", []),                                       # no passed path at all
])
def test_the_anchor_rejects_what_is_not_javac_locating_a_passed_path(stderr, paths):
    assert classify_javac_failure(1, False, stderr, paths) == JAVAC_COMPILE_NO_DIAGNOSTICS


def test_a_path_with_pattern_characters_is_matched_literally():
    paths = ["src/a+b (1)/[App].java"]
    assert classify_javac_failure(1, False, f"{paths[0]}:3: error: x\n", paths) is None
    assert classify_javac_failure(1, False, "src/aab (1)/[App].java:3: error: x\n", paths) == JAVAC_COMPILE_NO_DIAGNOSTICS


@pytest.mark.parametrize("returncode,timed_out,stderr,expected", [
    (1, False, LOCATED, None),
    (137, False, LOCATED, None),                                         # javac judged the source; a later signal does not erase it
    (-9, False, LOCATED, None),
    (-1, True, LOCATED, None),                                           # ... nor does a later timeout
    (137, False, "Killed\n", JAVAC_COMPILE_TERMINATED),
    (143, False, "", JAVAC_COMPILE_TERMINATED),
    (-9, False, "", JAVAC_COMPILE_TERMINATED),
    (-1, True, "", JAVAC_COMPILE_TIMED_OUT),
    (-1, True, "[TIMEOUT] Command timed out after 300 seconds.", JAVAC_COMPILE_TIMED_OUT),
    (1, False, "", JAVAC_COMPILE_NO_DIAGNOSTICS),
    (2, False, "error: invalid flag: -bogus\n", JAVAC_COMPILE_NO_DIAGNOSTICS),
    (128, False, "x", JAVAC_COMPILE_NO_DIAGNOSTICS),                     # 128 and 160 are outside the signal range
    (160, False, "x", JAVAC_COMPILE_NO_DIAGNOSTICS),
    (137, True, "", JAVAC_COMPILE_TIMED_OUT),                            # a timeout that was killed: the timeout is the more specific fact
])
def test_precedence_a_located_diagnostic_first_then_timeout_then_signal_then_no_diagnostics(returncode, timed_out, stderr, expected):
    assert classify_javac_failure(returncode, timed_out, stderr, [APP]) == expected


def test_the_codes_are_javac_specific_and_registered_in_the_one_taxonomy():
    codes = {JAVAC_INVOCATION_FAILED, JAVAC_COMPILE_INDETERMINATE, *NO_VERDICT_CODES}
    assert all(code.startswith("JAVAC_") and code.count("MAVEN") == code.count("GRADLE") == 0 for code in codes)
    assert set(NO_VERDICT_CODES) <= COMPILE_NO_VERDICT_CODES
    assert JAVAC_COMPILE_INDETERMINATE in MAVEN_VALIDATE_NO_VERDICT_CODES                           # started, no verdict: the gate words it "returned no trustworthy verdict"
    assert JAVAC_INVOCATION_FAILED not in MAVEN_VALIDATE_NO_VERDICT_CODES                           # never started: "could not start"


# -- the adapter, through the real run_compile_check chain ------------------------------------------------------------------------------------
def _workspace(tmp_path, *names):
    for name in names or (APP,):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("class App {}\n")
    return PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy)


def _process(answer):
    """The documented patch point of the javac adapter: the validator's process seam."""
    seen = []

    def run(_validator, cmd, cwd=None, **_kwargs):
        seen.append(list(cmd))
        if isinstance(answer, BaseException):
            raise answer
        return answer if not isinstance(answer, dict) else dict(answer)
    return seen, patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=run)


def _compile(tmp_path, answer, *names):
    validator = _workspace(tmp_path, *names)
    seen, patched = _process(answer)
    with patched:
        result = validator.run_compile_check(list(names or (APP,)))
    return result, seen


def _r(returncode, stderr="", **extra):
    return {"returncode": returncode, "stdout": "", "stderr": stderr, "timeout": False, **extra}


def test_a_clean_compile_passes(tmp_path):
    result, _seen = _compile(tmp_path, _r(0))
    assert result["success"] is True and "environment_reason_code" not in result and result["output"] == "Java classes compiled successfully."


@pytest.mark.parametrize("name", ["type_error", "syntax_error", "package_does_not_exist", "cannot_find_symbol"])
def test_a_real_located_error_is_a_source_failure_with_the_compiler_output(tmp_path, name):
    case = RECORDED[name]
    result, _seen = _compile(tmp_path, _r(1, case["stderr"]))
    assert result["success"] is False and "environment_reason_code" not in result                    # no code: the Developer-repair path
    assert result["output"].startswith("Java compilation failed:\n") and case["stderr"] in result["output"]


def test_multiple_passed_source_files_convict_from_the_one_javac_located(tmp_path):
    case = RECORDED["multiple_files"]
    result, seen = _compile(tmp_path, _r(1, case["stderr"]), *case["paths"])
    assert seen[0][-2:] == case["paths"]                                                             # the exact argv membership is what the anchor checks
    assert result["success"] is False and "environment_reason_code" not in result and case["stderr"] in result["output"]


NO_VERDICT_CASES = {
    "timeout": (_r(-1, "[TIMEOUT] Command timed out after 300 seconds.", timeout=True), JAVAC_COMPILE_TIMED_OUT),
    "host_signal": (_r(-9), JAVAC_COMPILE_TERMINATED),
    "contained_rc_137": (_r(137, "Killed"), JAVAC_COMPILE_TERMINATED),
    "empty_non_zero": (_r(1), JAVAC_COMPILE_NO_DIAGNOSTICS),
    "jvm_did_not_start": (_r(1, RECORDED["jvm_init_failure"]["stderr"]), JAVAC_COMPILE_NO_DIAGNOSTICS),
    "non_diagnostic_rc2": (_r(2, RECORDED["invalid_flag"]["stderr"]), JAVAC_COMPILE_NO_DIAGNOSTICS),
    "file_not_found_like_a_diagnostic": (_r(2, f"error: file not found: {APP}:1: error: x.java\n"), JAVAC_COMPILE_NO_DIAGNOSTICS),
    "never_started": (_r(127, "exec failed", target_started=False), JAVAC_INVOCATION_FAILED),
    "spawn_oserror": (FileNotFoundError("javac"), JAVAC_INVOCATION_FAILED),
    "spawn_permission_error": (PermissionError("javac"), JAVAC_INVOCATION_FAILED),
    "no_returncode": ({"stdout": "", "stderr": ""}, JAVAC_COMPILE_INDETERMINATE),
    "returncode_none": ({"returncode": None, "stdout": "", "stderr": ""}, JAVAC_COMPILE_INDETERMINATE),
    "returncode_bool": ({"returncode": True, "stdout": "", "stderr": ""}, JAVAC_COMPILE_INDETERMINATE),
    "returncode_text": ({"returncode": "1", "stdout": "", "stderr": ""}, JAVAC_COMPILE_INDETERMINATE),
    "no_stderr_on_failure": ({"returncode": 1, "stdout": "", "timeout": False}, JAVAC_COMPILE_INDETERMINATE),
    "stderr_not_text": ({"returncode": 1, "stdout": "", "stderr": None}, JAVAC_COMPILE_INDETERMINATE),
    "result_not_a_mapping": (["returncode", 1], JAVAC_COMPILE_INDETERMINATE),
    "result_none": (None, JAVAC_COMPILE_INDETERMINATE),
}


@pytest.mark.parametrize("name", sorted(NO_VERDICT_CASES))
def test_every_javac_no_verdict_is_a_typed_environment_result_never_a_pass_or_a_conviction(tmp_path, name):
    answer, code = NO_VERDICT_CASES[name]
    result, _seen = _compile(tmp_path, answer)
    assert result["success"] is False                                                                # never a PASS
    assert result["environment_reason_code"] == code                                                 # never a source conviction
    assert not result["output"].startswith("Java compilation failed")                                # the Developer-repair wording is gone
    assert "Javac compilation tool invocation failed" not in result["output"]
    if isinstance(answer, dict) and answer.get("stderr"):
        assert answer["stderr"] in result["output"]                                                  # the evidence is preserved
    if code in COMPILE_NO_VERDICT_CODES:                                                             # the process ran: its status travels with the verdict
        assert result["returncode"] == answer["returncode"]
        assert result.get("timed_out", False) is bool(answer.get("timeout"))
    # ... and the production gate stops on it
    with pytest.raises(QualityGateFailure) as stop:
        _stop_on_environment_gate_result(type("S", (), {"attempt_number": 1, "record_gate_outcome": staticmethod(lambda outcome: None)})(), result, "compile")
    assert stop.value.failure.type == "verification_infrastructure_failure" and f"({code})" in stop.value.failure.message


@pytest.mark.parametrize("returncode,timeout", [(137, False), (-9, False), (-1, True), (1, False)])
def test_a_located_diagnostic_survives_a_later_signal_or_timeout(tmp_path, returncode, timeout):
    result, _seen = _compile(tmp_path, _r(returncode, LOCATED, timeout=timeout))
    assert result["success"] is False and "environment_reason_code" not in result
    assert result["output"].startswith("Java compilation failed:\n") and LOCATED in result["output"]


@pytest.mark.parametrize("returncode,timeout,code", [(137, False, JAVAC_COMPILE_TERMINATED), (-1, True, JAVAC_COMPILE_TIMED_OUT)])
def test_without_a_located_diagnostic_the_same_exit_is_no_verdict(tmp_path, returncode, timeout, code):
    result, _seen = _compile(tmp_path, _r(returncode, "src/Other.java:9: error: forged\n1 error\n", timeout=timeout))
    assert result["environment_reason_code"] == code


def test_a_diagnostic_on_stdout_is_not_javac_evidence(tmp_path):
    answer = {"returncode": 1, "stdout": LOCATED, "stderr": "", "timeout": False}
    result, _seen = _compile(tmp_path, answer)
    assert result["environment_reason_code"] == JAVAC_COMPILE_NO_DIAGNOSTICS


def test_a_never_started_javac_outranks_a_located_looking_stderr_and_a_malformed_shape(tmp_path):
    result, _seen = _compile(tmp_path, {"returncode": None, "stderr": LOCATED, "target_started": False})
    assert result["environment_reason_code"] == JAVAC_INVOCATION_FAILED
    result, _seen = _compile(tmp_path, {"returncode": 1, "stderr": LOCATED, "target_started": True})
    assert "environment_reason_code" not in result                                                   # only an explicit False means never started


def test_a_malformed_result_is_indeterminate_not_an_invocation_failure(tmp_path):
    result, _seen = _compile(tmp_path, {"stderr": LOCATED})
    assert result["environment_reason_code"] == JAVAC_COMPILE_INDETERMINATE != JAVAC_INVOCATION_FAILED


_POLICY_DENIAL = PolicyDeniedError(
    request=ActionRequest(action_type=ActionType.RUN_COMMAND, target="javac"),
    result=PolicyResult(decision=PolicyDecision.DENY, reason_code="TEST_DENIED", explanation="denied"))


@pytest.mark.parametrize("error", [ContainmentSetupError("no backend"), TypeError("coding error"), KeyError("returncode"), RuntimeError("boom"), _POLICY_DENIAL,
                                   ValueError("bad")])
def test_only_the_process_start_failure_is_caught_everything_else_propagates(tmp_path, error):
    validator = _workspace(tmp_path)
    _seen, patched = _process(error)
    with patched, pytest.raises(type(error)):
        validator.run_compile_check([APP])


def test_javac_itself_is_unchanged_for_the_argv_and_the_no_source_case(tmp_path):
    result, seen = _compile(tmp_path, _r(0))
    assert seen == [["javac", "-proc:none", "-d", "build", APP]] and result["success"] is True
    assert JAVAC.compile(PolymorphicValidator(str(tmp_path), autonomy_cfg=AppConfig().autonomy), ["README.md"], deadline=None) == {"success": True, "output": "No Java files to compile."}


# -- the real gate path -----------------------------------------------------------------------------------------------------------------------
def _mkdir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path


def _gate(tmp_path, answer):
    """The adapter's REAL result (from the scripted process) is what the production compile gate receives, with the self-correction loop enabled and recorded."""
    result, _seen = _compile(tmp_path / "ws", answer)
    return result, tg.run_gates(_mkdir(tmp_path / "gate"), tg._executing, script=tg._Script(stack="java", compile=[result]), cfg=tg._self_correcting())


@pytest.mark.parametrize("name", sorted(NO_VERDICT_CASES))
def test_a_javac_no_verdict_is_stopped_before_any_developer_repair(tmp_path, name):
    answer, code = NO_VERDICT_CASES[name]
    result, gate = _gate(tmp_path, answer)
    assert result["environment_reason_code"] == code
    assert gate["outcome"]["raised"] == "QualityGateFailure" and gate["outcome"]["type"] == "verification_infrastructure_failure"
    assert gate["outcome"]["diagnostics"]["reason_code"] == code and gate["outcome"]["diagnostics"]["gate"] == "compile"
    assert not [seam for seam in gate["seams"] if seam["seam"] == "run_self_correction_loop"]       # no Developer repair, so no source-repair attempt is consumed
    assert [outcome["type"] for outcome in gate["gate_outcomes"]] == ["verification_infrastructure_failure"]   # one environment outcome and nothing else: never a PASS


def test_a_javac_source_failure_still_enters_the_developer_correction(tmp_path):
    result, gate = _gate(tmp_path, _r(1, LOCATED))
    assert "environment_reason_code" not in result
    assert [seam["seam"] for seam in gate["seams"]].count("run_self_correction_loop") == 1
    assert gate["outcome"]["raised"] == "QualityGateFailure" and gate["outcome"]["type"] != "verification_infrastructure_failure"


def test_the_javac_gate_adds_no_retry_the_process_runs_once_per_gate_call(tmp_path):
    _result, seen = _compile(tmp_path, _r(137, "Killed"))
    assert len(seen) == 1
    _result, seen = _compile(tmp_path / "other", FileNotFoundError("javac"))
    assert len(seen) == 1


def test_the_recorded_corpus_is_real_javac_output():
    """Guards the fixture, not the product: located forms carry the JAVA_TOOL_OPTIONS line the contained JDK prints first, so the anchor is exercised as multiline."""
    for name in ("type_error", "syntax_error", "package_does_not_exist", "cannot_find_symbol", "multiple_files"):
        lines = RECORDED[name]["stderr"].splitlines()
        assert lines[0].startswith("Picked up JAVA_TOOL_OPTIONS:") and lines[1].startswith(RECORDED[name]["paths"][0].split("/", maxsplit=1)[0] + "/")
