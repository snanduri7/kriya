"""Slice F (COMPILE-GATE-CONVICTION-WITHOUT-VERDICT-001; owner decisions 2026-10-28): source compilation is convicted only from positive, trustworthy, LOCATED compiler evidence.

MEASURED at 7839c0e (``WAVE2_SLICEF_CHARACTERIZATION_2026-10-28/matrix.py``, the real PolymorphicValidator and adapter chain with only the process seams injected): a Maven or
Gradle that ran, failed and left no located source diagnostic - a timeout, a signal (host rc -9, contained rc 137), an invalid operator settings file, a resolution /
acquisition / transfer failure, an empty or bare-summary failure - was returned as an ordinary ``Maven compilation failed`` with no ``environment_reason_code`` (16 of 24
cases), so ``gate_orchestrator`` handed the Developer a repair request for source no compiler had judged.

Real Maven 3.9.16 output is recorded in ``tests/fixtures/maven_compile/recorded_maven_compile.json``; the measurement showed that the final OFFLINE messages for a bad
coordinate and for a legitimate dependency that is merely absent from the cache are IDENTICAL, so ambiguous dependency / plugin resolution is a no-verdict and project
ownership is never inferred from free text. Gradle output below is the canonical javac form built by hand (no Gradle on the characterization host); the real contained Gradle run is
the confirmation. W2-16 (the POM-validation classifier) is separate and unchanged: ``classify_maven_validate_failure`` is pinned below."""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.config import AppConfig
from kriya.tools import dependency_execution as de
from kriya.tools.dependency_execution import (
    COMPILE_NO_VERDICT_CODES,
    GRADLE_COMPILE_NO_DIAGNOSTICS,
    GRADLE_COMPILE_RESOLUTION_UNVERIFIED,
    GRADLE_COMPILE_TERMINATED,
    GRADLE_COMPILE_TIMED_OUT,
    MAVEN_COMPILE_NO_DIAGNOSTICS,
    MAVEN_COMPILE_OPERATOR_CONFIG_INVALID,
    MAVEN_COMPILE_RESOLUTION_UNVERIFIED,
    MAVEN_COMPILE_TERMINATED,
    MAVEN_COMPILE_TIMED_OUT,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    classify_compile_failure,
    classify_maven_validate_failure,
)
from kriya.tools.validate import PolymorphicValidator

RECORDED = json.loads((Path(__file__).parent / "fixtures" / "maven_compile" / "recorded_maven_compile.json").read_text(encoding="utf-8"))
LOCATED_MAVEN = "[ERROR] /ws/src/main/java/App.java:[1,24] incompatible types: java.lang.String cannot be converted to int"
LOCATED_GRADLE = "/ws/src/main/java/App.java:1: error: incompatible types: String cannot be converted to int"
MAVEN_SUMMARY = ("[ERROR] COMPILATION ERROR : \n[INFO] BUILD FAILURE\n[ERROR] Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin:3.11.0:compile "
                 "(default-compile) on project a: Compilation failure")
GRADLE_SUMMARY = "FAILURE: Build failed with an exception.\n* What went wrong:\nExecution failed for task ':compileJava'.\n> Compilation failed; see the compiler error output for details."
GRADLE_RESOLVE = ("FAILURE: Build failed with an exception.\n* What went wrong:\nCould not resolve all files for configuration ':compileClasspath'.\n"
                  "> Could not resolve org.foo:bar:9.9.\n  > No cached version of org.foo:bar:9.9 available for offline mode.")


def _maven(output, rc=1, timed_out=False):
    return classify_compile_failure("maven", rc, timed_out, output)


def _gradle(output, rc=1, timed_out=False):
    return classify_compile_failure("gradle", rc, timed_out, output)


# -- real recorded Maven output ---------------------------------------------------------------------------------------------------------
def test_real_maven_located_compiler_error_is_a_source_failure():
    assert _maven(RECORDED["compile_error_located"]["output"]) is None
    assert _maven(RECORDED["contained_compile_error_located"]["output"]) is None          # the real OCI run: `[ERROR] /kriya/workspace/...App.java:[2,53] ...`


RECORDED_GRADLE = json.loads((Path(__file__).parent / "fixtures" / "gradle_compile" / "recorded_gradle_compile.json").read_text(encoding="utf-8"))


def test_real_contained_gradle_output_is_classified_by_the_same_rules():
    """Real Gradle 8 output from the contained confirmation: the located form is `/kriya/workspace/<path>.java:<line>: error: ...` (an absolute path), and a dependency the
    offline run cannot find - with the acquisition-incomplete marker Kriya prepends - is the ambiguous-resolution no-verdict."""
    located = RECORDED_GRADLE["compile_error_located"]
    assert "/kriya/workspace/src/main/java/demo/App.java:2: error: incompatible types" in located["output"] and _gradle(located["output"], located["returncode"]) is None
    unresolved = RECORDED_GRADLE["bad_dependency_offline_after_acquisition"]
    assert "GRADLE_ACQUISITION_INCOMPLETE:" in unresolved["output"] and _gradle(unresolved["output"], unresolved["returncode"]) == GRADLE_COMPILE_RESOLUTION_UNVERIFIED


RESOLUTION_CASES = [name for name in RECORDED if name not in ("_provenance", "compile_error_located", "contained_compile_error_located")]


@pytest.mark.parametrize("name", RESOLUTION_CASES)
def test_every_real_maven_resolution_acquisition_or_transfer_failure_is_a_no_verdict(name):
    assert _maven(RECORDED[name]["output"], RECORDED[name]["returncode"]) == MAVEN_COMPILE_RESOLUTION_UNVERIFIED


def test_a_bad_coordinate_and_a_legitimate_but_uncached_dependency_get_the_same_safe_class():
    """The final offline messages differ only in the coordinate: ownership is not inferable from text, so both are the same no-verdict until an authoritative classifier exists."""
    bad = RECORDED["bad_dependency_offline_fresh_cache"]["output"]
    real = RECORDED["real_dependency_uncached_offline"]["output"]
    assert "org.zzbad:nothing" in bad and "com.google.guava:guava" in real and "in offline mode" in bad and "in offline mode" in real
    assert _maven(bad) == _maven(real) == MAVEN_COMPILE_RESOLUTION_UNVERIFIED


# -- the located-evidence rule ----------------------------------------------------------------------------------------------------------
def test_a_generic_summary_alone_never_convicts_the_source():
    assert _maven(MAVEN_SUMMARY) == MAVEN_COMPILE_NO_DIAGNOSTICS
    assert _gradle(GRADLE_SUMMARY) == GRADLE_COMPILE_NO_DIAGNOSTICS


def test_a_summary_supports_located_evidence_but_does_not_replace_it():
    assert _maven(f"{LOCATED_MAVEN}\n{MAVEN_SUMMARY}") is None
    assert _gradle(f"{LOCATED_GRADLE}\n1 error\n{GRADLE_SUMMARY}") is None


@pytest.mark.parametrize("text", ["", "   \n", "[INFO] Scanning for projects...\n[INFO] BUILD FAILURE", "Killed"])
def test_empty_or_non_diagnostic_non_zero_output_is_a_no_verdict(text):
    assert _maven(text) == MAVEN_COMPILE_NO_DIAGNOSTICS
    assert _gradle(text) == GRADLE_COMPILE_NO_DIAGNOSTICS


@pytest.mark.parametrize("line", [
    "[WARNING] /ws/App.java:[3,5] [rawtypes] found raw type: List",          # warning level: neither a conviction ...
    "      [ERROR] /ws/App.java:[1,24] incompatible types",                  # not at the start of a build-output line (an echo)
    "/ws/App.java:[1,24] incompatible types",                                 # no Maven prefix
    "[ERROR] App.java: incompatible types",                                   # no location
    "[ERROR] /ws/App.txt:[1,24] incompatible types",                          # not a Java source
    "[ERROR]  /ws/App.java:[1,24] incompatible types",                        # a space after the prefix: a continuation line of another message
])
def test_maven_text_that_is_not_an_anchored_located_error_does_not_convict(line):
    assert _maven(line) == MAVEN_COMPILE_NO_DIAGNOSTICS


@pytest.mark.parametrize("line", [
    "  /ws/App.java:1: error: incompatible types",                            # indented: a javac-echoed source line or a quotation
    "//ws/App.java:1: error: incompatible types",                             # a `//` comment line javac echoes from the candidate
    "/ws/App.java:1: warning: [rawtypes] found raw type",                     # a warning
    "App.java:1: error: incompatible types",                                  # not an absolute path
    "> /ws/App.java:1: error: incompatible types",                            # a failure-report quotation
    "/ws/App.txt:1: error: incompatible types",
])
def test_gradle_text_that_is_not_an_anchored_located_error_does_not_convict(line):
    assert _gradle(line) == GRADLE_COMPILE_NO_DIAGNOSTICS


def test_candidate_echoed_resolution_words_outside_error_lines_do_not_suppress_a_located_conviction():
    """Warning-level transfer / metadata noise, and resolution-looking words in a non-error line, never suppress trustworthy compiler evidence (owner rule 2)."""
    noise = ("[WARNING] Could not transfer metadata org.foo:bar/maven-metadata.xml from/to central (https://repo.maven.apache.org/maven2): Connect timed out\n"
             "[WARNING] The POM for org.foo:bar:jar:1 is missing, no dependency information available\n"
             "[INFO] Could not resolve nothing here\n")
    assert _maven(f"{noise}{LOCATED_MAVEN}\n{MAVEN_SUMMARY}") is None


def test_a_located_diagnostic_whose_own_message_quotes_resolution_words_still_convicts():
    quoted = "[ERROR] /ws/src/main/java/App.java:[7,9] cannot find symbol: class CouldNotResolve (Could not resolve dependencies is only a message)"
    assert _maven(quoted) is None


def test_error_level_resolution_failure_is_a_no_verdict_even_beside_a_located_line():
    """Maven resolves before it compiles, so the two never coexist in real output; the rule if they ever did is fail-closed."""
    assert _maven(f"{LOCATED_MAVEN}\n[ERROR] Could not transfer artifact org.foo:bar:pom:1 from/to central (https://x): Connect timed out") == MAVEN_COMPILE_RESOLUTION_UNVERIFIED


def test_the_acquisition_incomplete_marker_is_a_no_verdict():
    assert _maven("MAVEN_ACQUISITION_INCOMPLETE: offline execution for goals ['clean', 'compile'] reports a missing dependency") == MAVEN_COMPILE_RESOLUTION_UNVERIFIED
    assert _gradle("GRADLE_ACQUISITION_INCOMPLETE: offline execution for tasks ['compileJava'] reports a missing dependency") == GRADLE_COMPILE_RESOLUTION_UNVERIFIED


def test_invalid_operator_settings_are_a_no_verdict():
    text = "[ERROR] 1 problem was encountered while building the effective settings\n[FATAL] Non-parseable settings /root/.m2/settings.xml: Duplicated tag: 'servers' @ line 2, column 54"
    assert _maven(text) == MAVEN_COMPILE_OPERATOR_CONFIG_INVALID


@pytest.mark.parametrize("output", [GRADLE_RESOLVE, "Could not GET 'https://repo.maven.apache.org/x.pom'. Received status code 503",
                                    "> Could not HEAD 'https://x'. Connect timed out", "java.net.UnknownHostException: services.gradle.org",
                                    "Plugin [id: 'org.foo.plugin', version: '9.9'] was not found in any of the following sources:"])
def test_gradle_resolution_acquisition_and_transfer_failures_are_a_no_verdict(output):
    assert _gradle(output) == GRADLE_COMPILE_RESOLUTION_UNVERIFIED


def test_gradle_javac_warnings_beside_a_located_error_still_convict():
    assert _gradle(f"/ws/App.java:3: warning: [rawtypes] found raw type: List\n{LOCATED_GRADLE}\n1 error\n1 warning") is None


# -- the exit-status rules -----------------------------------------------------------------------------------------------------------
def test_a_timeout_and_a_host_signal_are_no_verdicts_whatever_the_output_says():
    assert _maven(LOCATED_MAVEN, rc=-1, timed_out=True) == MAVEN_COMPILE_TIMED_OUT
    assert _gradle(LOCATED_GRADLE, rc=-1, timed_out=True) == GRADLE_COMPILE_TIMED_OUT
    assert _maven(LOCATED_MAVEN, rc=-9) == MAVEN_COMPILE_TERMINATED
    assert _gradle(LOCATED_GRADLE, rc=-9) == GRADLE_COMPILE_TERMINATED


@pytest.mark.parametrize("rc", [129, 130, 134, 137, 139, 143, 159])
def test_a_contained_signal_style_exit_with_no_located_diagnostic_is_terminated(rc):
    assert _maven("[INFO] Scanning...", rc=rc) == MAVEN_COMPILE_TERMINATED
    assert _gradle("> Task :compileJava", rc=rc) == GRADLE_COMPILE_TERMINATED
    assert _maven("", rc=rc) == MAVEN_COMPILE_TERMINATED


def test_a_contained_signal_style_exit_after_a_located_diagnostic_still_convicts_what_javac_judged():
    assert _maven(LOCATED_MAVEN, rc=137) is None and _gradle(LOCATED_GRADLE, rc=137) is None


@pytest.mark.parametrize("rc", [1, 2, 127, 128, 160, 255])
def test_an_ordinary_non_zero_exit_with_no_diagnostic_is_not_called_a_signal(rc):
    assert _maven("[INFO] x", rc=rc) == MAVEN_COMPILE_NO_DIAGNOSTICS


# -- the vocabulary and the W2-16 boundary ---------------------------------------------------------------------------------------------
def test_the_compile_codes_are_compile_specific_and_worded_as_no_verdict_by_the_gate():
    assert COMPILE_NO_VERDICT_CODES <= MAVEN_VALIDATE_NO_VERDICT_CODES
    assert all("_COMPILE_" in code for code in COMPILE_NO_VERDICT_CODES) and len(COMPILE_NO_VERDICT_CODES) == 13   # 10 Maven/Gradle (incl. MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED, Step-3 residuals) + the 3 raw-javac twins (COMPILE-GATE-RAW-JAVAC-CONVICTION-WITHOUT-VERDICT-001)
    validate_names = {value for name, value in vars(de).items() if name.startswith("MAVEN_VALIDATE_") and isinstance(value, str)
                      and name != "MAVEN_VALIDATE_NO_VERDICT_CODES"}
    assert not (COMPILE_NO_VERDICT_CODES & validate_names)     # no validation-specific code name is reused


def test_the_pom_validation_classifier_is_unchanged():
    """W2-16 is separate: Slice F did not touch classify_maven_validate_failure (a 137 with no output stays NO_DIAGNOSTICS there, a `< 0` status stays TERMINATED)."""
    assert classify_maven_validate_failure(137, False, "") == de.MAVEN_VALIDATE_NO_DIAGNOSTICS
    assert classify_maven_validate_failure(-9, False, "") == de.MAVEN_VALIDATE_TERMINATED
    assert classify_maven_validate_failure(-1, True, "") == de.MAVEN_VALIDATE_TIMED_OUT


# -- the real adapter chain ----------------------------------------------------------------------------------------------------------
POM = "<project><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version></project>"
CHANGED = ["src/main/java/App.java"]


def _validator(root, build):
    root.mkdir(parents=True, exist_ok=True)
    (root / ("pom.xml" if build == "maven" else "build.gradle")).write_text(POM if build == "maven" else "plugins { id 'java' }\n")
    source = root / "src" / "main" / "java" / "App.java"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text('class App { int x = "not an int"; }')
    return PolymorphicValidator(str(root), autonomy_cfg=AppConfig().autonomy)


def _run(rc, out="", err="", **extra):
    return {"returncode": rc, "stdout": out, "stderr": err, "timeout": False, **extra}


# (label, tool result, expected code or None for a source failure)
MAVEN_MATRIX = [
    ("M1 located compiler error", _run(1, LOCATED_MAVEN + "\n" + MAVEN_SUMMARY), None),
    ("M1r real recorded located error", _run(1, RECORDED["compile_error_located"]["output"]), None),
    ("M2 timeout", _run(-1, "[INFO] Compiling", "", timeout=True), MAVEN_COMPILE_TIMED_OUT),
    ("M3 host signal", _run(-9, "[INFO] Scanning"), MAVEN_COMPILE_TERMINATED),
    ("M3b contained rc 137", _run(137, "[INFO] Scanning", "Killed"), MAVEN_COMPILE_TERMINATED),
    ("M4 offline resolution", _run(1, RECORDED["bad_dependency_offline_fresh_cache"]["output"]), MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    ("M4b acquisition marker", _run(1, "", "MAVEN_ACQUISITION_INCOMPLETE: offline execution still lacks a dependency\n[ERROR] x"), MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    ("M5 transfer failure", _run(1, RECORDED["registry_unreachable_transfer_failure"]["output"]), MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    ("M5b bad plugin", _run(1, RECORDED["bad_plugin_version_registry_not_found"]["output"]), MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    ("M5c uncached legitimate dependency", _run(1, RECORDED["real_dependency_uncached_offline"]["output"]), MAVEN_COMPILE_RESOLUTION_UNVERIFIED),
    ("M7 empty non-zero", _run(1), MAVEN_COMPILE_NO_DIAGNOSTICS),
    ("M7b non-diagnostic non-zero", _run(1, "[INFO] BUILD FAILURE"), MAVEN_COMPILE_NO_DIAGNOSTICS),
    ("M9 invalid operator settings", _run(1, "[ERROR] 1 problem was encountered while building the effective settings\n[FATAL] Non-parseable settings /x/settings.xml: Duplicated tag"),
     MAVEN_COMPILE_OPERATOR_CONFIG_INVALID),
    ("M10 warning noise + located error", _run(1, "[WARNING] Could not transfer metadata g:a/maven-metadata.xml from/to central: Connect timed out\n" + LOCATED_MAVEN), None),
]
GRADLE_MATRIX = [
    ("G1 located compiler error", _run(1, LOCATED_GRADLE + "\n1 error\n" + GRADLE_SUMMARY), None),
    ("G2 timeout", _run(-1, "> Task :compileJava", "", timeout=True), GRADLE_COMPILE_TIMED_OUT),
    ("G3 host signal", _run(-9, "> Task :compileJava"), GRADLE_COMPILE_TERMINATED),
    ("G3b contained rc 137", _run(137, "", "Killed"), GRADLE_COMPILE_TERMINATED),
    ("G4 offline resolution", _run(1, GRADLE_RESOLVE), GRADLE_COMPILE_RESOLUTION_UNVERIFIED),
    ("G5 transfer failure", _run(1, "", "> Could not GET 'https://repo.maven.apache.org/x.pom'. Connect timed out"), GRADLE_COMPILE_RESOLUTION_UNVERIFIED),
    ("G7 empty non-zero", _run(1), GRADLE_COMPILE_NO_DIAGNOSTICS),
    ("G7b bare summary", _run(1, GRADLE_SUMMARY), GRADLE_COMPILE_NO_DIAGNOSTICS),
    ("G10 javac warning + located error", _run(1, "/ws/App.java:3: warning: [rawtypes] found raw type\n" + LOCATED_GRADLE), None),
]


def _javac_sentinel(calls):
    def seam(_validator_self, cmd, cwd=None, **_kwargs):
        calls.append(list(cmd))
        return {"returncode": 1, "stdout": "", "stderr": "/ws/App.java:1: error: javac fallback consulted"}
    return seam


@pytest.mark.parametrize("label,result,code", MAVEN_MATRIX, ids=[m[0] for m in MAVEN_MATRIX])
def test_maven_adapter_matrix(tmp_path, label, result, code):
    calls = []
    validator = _validator(tmp_path, "maven")
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=dict(result)), \
            patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=_javac_sentinel(calls)):
        got = validator.run_compile_check(CHANGED)
    assert calls == []                                    # the downstream javac fallback is never consulted after Maven answered
    assert got["success"] is False
    if code is None:
        assert "environment_reason_code" not in got and got["output"].startswith("Maven compilation failed:")
    else:
        assert got["environment_reason_code"] == code
        assert "no trustworthy compilation verdict" in got["output"] and "Maven compilation failed" not in got["output"]
        assert got["returncode"] == result["returncode"]


@pytest.mark.parametrize("label,result,code", GRADLE_MATRIX, ids=[m[0] for m in GRADLE_MATRIX])
def test_gradle_adapter_matrix_through_the_real_gradle_runner(tmp_path, label, result, code, monkeypatch):
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    calls = []
    validator = _validator(tmp_path / "ws", "gradle")

    def seam(_self, cmd, cwd=None, **_kwargs):
        calls.append(list(cmd))
        return dict(result)
    with patch.object(PolymorphicValidator, "_run_cmd_with_timeout", new=seam):
        got = validator.run_compile_check(CHANGED)
    assert calls and all("javac" not in call[0] for call in calls)
    assert got["success"] is False
    if code is None:
        assert "environment_reason_code" not in got and got["output"].startswith("Gradle compilation failed:")
    else:
        assert got["environment_reason_code"] == code
        assert "no trustworthy compilation verdict" in got["output"] and "Gradle compilation failed" not in got["output"]


def test_the_acquisition_evidence_and_the_exit_status_travel_with_a_no_verdict(tmp_path):
    evidence = {"argv": ["mvn", "-B", "clean", "compile"], "invoked": True, "returncode": 1, "timeout": False,
                "stdout_tail": "", "stderr_tail": "[ERROR] Could not find artifact org.zzbad:nothing:jar:9.9.9 in central (https://repo.maven.apache.org/maven2)"}
    validator = _validator(tmp_path, "maven")
    with patch.object(PolymorphicValidator, "_run_maven_cmd",
                      return_value=_run(1, RECORDED["bad_dependency_offline_fresh_cache"]["output"], acquisition_evidence=evidence)):
        got = validator.run_compile_check(CHANGED)
    assert got["environment_reason_code"] == MAVEN_COMPILE_RESOLUTION_UNVERIFIED and got["acquisition_evidence"] == evidence and got["returncode"] == 1
    validator = _validator(tmp_path / "timed", "maven")
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_run(-1, "[INFO] Compiling", "", timeout=True)):
        timed = validator.run_compile_check(CHANGED)
    assert timed["timed_out"] is True and "acquisition_evidence" not in timed


def test_a_source_failure_keeps_its_enrichment_and_execution_evidence_path(tmp_path):
    validator = _validator(tmp_path, "maven")
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_run(1, LOCATED_MAVEN, toolchain_identity={"image": "maven:3.9"})):
        got = validator.run_compile_check(CHANGED)
    assert got["success"] is False and "environment_reason_code" not in got and "acquisition_evidence" not in got
    assert got.get("toolchain_identity") == {"image": "maven:3.9"} and LOCATED_MAVEN in got["output"]


def test_f9_protections_are_intact(tmp_path):
    """A Maven that never started, an OSError and an uninterpretable result stay typed no-verdicts; a successful Maven stays a PASS; a PASS is never produced by the new rules."""
    from kriya.tools.dependency_execution import MAVEN_COMPILE_INDETERMINATE, MAVEN_INVOCATION_FAILED

    for name, kwargs, code in (("never-started", dict(return_value=_run(127, "", "exec failed", target_started=False)), MAVEN_INVOCATION_FAILED),
                               ("oserror", dict(side_effect=FileNotFoundError("mvn")), MAVEN_INVOCATION_FAILED),
                               ("uninterpretable", dict(return_value={"stdout": ""}), MAVEN_COMPILE_INDETERMINATE)):
        validator = _validator(tmp_path / name, "maven")
        with patch.object(PolymorphicValidator, "_run_maven_cmd", **kwargs):
            got = validator.run_compile_check(["pom.xml"])
        assert got["success"] is False and got["environment_reason_code"] == code, name
    validator = _validator(tmp_path / "ok", "maven")
    classes = tmp_path / "ok" / "target" / "classes"
    classes.mkdir(parents=True)
    (classes / "App.class").write_bytes(b"\xca\xfe\xba\xbe")
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=_run(0, "[INFO] BUILD SUCCESS")):
        assert validator.run_compile_check(CHANGED)["success"] is True
    assert os.path.exists(classes / "App.class")
