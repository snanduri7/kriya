"""Slice F: what the compile gate's verdict does downstream, at the real ``gate_orchestrator`` path.

A compile result with a typed ``environment_reason_code`` (a no-verdict) is caught by ``_stop_on_environment_gate_result`` BEFORE the Developer-repair branch: the run stops as
``verification_infrastructure_failure``, the self-correction loop is never entered, the Developer is not asked again and no retry attempt is spent. A genuine located source
failure (no code) continues through the existing Developer correction path. Two real paths are driven:

* the legacy engine pipeline with the REAL ``PolymorphicValidator`` and Maven adapter, only the ``mvn`` process seam (``_run_maven_cmd``) scripted - so the adapter's classification,
  the gate and the controller's verdict are all production code;
* ``_run_executing_candidate_gates`` with the self-correction loop ENABLED and the loop seam recorded, so "never entered" is observed, not inferred.
"""
import sys
from pathlib import Path
from typing import Any, List

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402  # pylint: disable=wrong-import-position
import test_gate_orchestrator as tg  # noqa: E402  # pylint: disable=wrong-import-position

from kriya.tools.dependency_execution import (  # noqa: E402
    COMPILE_NO_VERDICT_CODES,
    MAVEN_COMPILE_NO_DIAGNOSTICS,
    MAVEN_COMPILE_OPERATOR_CONFIG_INVALID,
    MAVEN_COMPILE_RESOLUTION_UNVERIFIED,
    MAVEN_COMPILE_TERMINATED,
    MAVEN_COMPILE_TIMED_OUT,
)

LOCATED = {"returncode": 1, "timeout": False, "stderr": "",
           "stdout": f"[ERROR] COMPILATION ERROR :\n[ERROR] {tg.APP}:[5,73] cannot find symbol\n[INFO] BUILD FAILURE\n"}
OFFLINE_RESOLUTION = {"returncode": 1, "timeout": False, "stderr": "", "stdout": (
    "[ERROR] Failed to execute goal on project a: Could not resolve dependencies for project g:a:jar:1\n[ERROR] dependency: org.zzbad:nothing:jar:9.9.9 (compile)\n"
    "[ERROR] \tCannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact org.zzbad:nothing:jar:9.9.9 has not been downloaded from it before.\n")}

NO_VERDICTS = {
    "timeout": ({"returncode": -1, "stdout": "[INFO] Compiling", "stderr": "", "timeout": True}, MAVEN_COMPILE_TIMED_OUT),
    "contained_rc_137": ({"returncode": 137, "stdout": "", "stderr": "", "timeout": False}, MAVEN_COMPILE_TERMINATED),
    "host_signal": ({"returncode": -9, "stdout": "[INFO] Scanning", "stderr": "", "timeout": False}, MAVEN_COMPILE_TERMINATED),
    "offline_resolution_failure": (OFFLINE_RESOLUTION, MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    "empty_non_zero": ({"returncode": 1, "stdout": "", "stderr": "", "timeout": False}, MAVEN_COMPILE_NO_DIAGNOSTICS),
    "bare_summary": ({"returncode": 1, "stderr": "", "timeout": False, "stdout": "[ERROR] COMPILATION ERROR : \n[ERROR] Failed to execute goal ...: Compilation failure\n"},
                     MAVEN_COMPILE_NO_DIAGNOSTICS),
    "invalid_operator_settings": ({"returncode": 1, "stderr": "", "timeout": False, "stdout": "[ERROR] 1 problem was encountered while building the effective settings\n"
                                   "[FATAL] Non-parseable settings /root/.m2/settings.xml: Duplicated tag: 'servers' @ line 2\n"}, MAVEN_COMPILE_OPERATOR_CONFIG_INVALID),
}


class _Scripted(tg._MavenOracle):
    """The default Maven oracle, except that the n-th ``clean``/``compile`` answers from ``results`` (None: the oracle's own answer)."""

    def __init__(self, *results: Any):
        super().__init__()
        self.results: List[Any] = list(results)

    def __call__(self, validator, goals, cwd, *args, **kwargs):
        default = super().__call__(validator, goals, cwd, *args, **kwargs)
        if goals and goals[0] in ("clean", "compile") and self.results:
            answer = self.results.pop(0)
            return dict(answer) if answer is not None else default
        return default


def _engine(tmp_path, monkeypatch, oracle, *, autonomous=True):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)
    tmp_path.mkdir(exist_ok=True)
    run = tg._jvm_run(tmp_path, autonomous=autonomous, target="pom.xml", oracle=oracle)
    return run, (getattr(run["outcome"].result, "legacy_result", None) or {})


@pytest.mark.parametrize("autonomous", [True, False], ids=["immediate", "deferred"])
@pytest.mark.parametrize("name", sorted(NO_VERDICTS))
def test_a_no_verdict_stops_the_run_and_never_asks_the_developer_for_a_repair(tmp_path, monkeypatch, name, autonomous):
    answer, code = NO_VERDICTS[name]
    run, legacy = _engine(tmp_path / name, monkeypatch, _Scripted(answer, answer, answer), autonomous=autonomous)
    assert code in COMPILE_NO_VERDICT_CODES
    assert legacy["quality_gates_passed"] is False                                    # never a PASS
    assert legacy["failure_category"] == "environment_failure"                          # not a source/compile failure
    assert legacy["environment_failure"].startswith("VERIFICATION_INFRASTRUCTURE_FAILURE") and f"({code})" in legacy["environment_failure"]
    assert "COMPILATION FAILURE" not in str(legacy["environment_failure"])
    assert legacy["last_failure"]["type"] == "verification_infrastructure_failure"
    assert run["developer_calls"] == 1                                                  # the Developer was not asked again: no source repair, no retry
    assert [call["goals"][0] for call in run["maven"]].count("clean") == 1               # the compile gate ran once; no retry attempt was spent


def test_a_located_source_error_continues_through_the_developer_correction(tmp_path, monkeypatch):
    run, legacy = _engine(tmp_path / "located", monkeypatch, _Scripted(LOCATED, None))
    assert run["developer_calls"] > 1                                                   # the existing correction path re-asked the Developer (a no-verdict leaves exactly 1)
    assert [call["goals"][0] for call in run["maven"]].count("clean") == 2
    assert legacy["quality_gates_passed"] is True and legacy.get("environment_failure") in (None, "")


# -- the gate itself, with the self-correction loop enabled and recorded ------------------------------------------------------------------------
def _gate(tmp_path, compile_answer):
    script = tg._Script(stack="java", compile=[compile_answer])
    return tg.run_gates(tmp_path, tg._executing, script=script, cfg=tg._self_correcting())


@pytest.mark.parametrize("name", sorted(NO_VERDICTS))
def test_the_self_correction_loop_is_never_entered_for_a_no_verdict(tmp_path, name):
    _answer, code = NO_VERDICTS[name]
    result = _gate(tmp_path, {"success": False, "environment_reason_code": code, "output": f"Maven returned no trustworthy compilation verdict ({code})"})
    assert result["outcome"]["raised"] == "QualityGateFailure" and result["outcome"]["type"] == "verification_infrastructure_failure"
    assert result["outcome"]["diagnostics"]["reason_code"] == code and result["outcome"]["diagnostics"]["gate"] == "compile"
    assert not [seam for seam in result["seams"] if seam["seam"] == "run_self_correction_loop"]      # the Developer-repair loop was not entered
    assert [outcome["type"] for outcome in result["gate_outcomes"]] == ["verification_infrastructure_failure"]


def test_a_source_failure_still_enters_the_self_correction_loop(tmp_path):
    result = _gate(tmp_path, {"success": False, "output": f"Maven compilation failed:\n[ERROR] {tg.APP}:[5,73] cannot find symbol"})
    assert [seam["seam"] for seam in result["seams"]].count("run_self_correction_loop") == 1
    assert result["outcome"]["raised"] == "QualityGateFailure" and result["outcome"]["type"] != "verification_infrastructure_failure"
