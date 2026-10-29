"""W2-16a (Wave-2 Step 3 completion): a candidate's `<modelVersion>` value must not drive a Maven resolution / acquisition decision.

MEASURED at 72e08a2 (real contained Maven 3.9.16, network denied; tests/fixtures/maven_validate/recorded_model_version.json, capture scripts and the pre-fix measurements in the
Step-3 completion evidence directory): a modelVersion that is not a number makes Maven's model validator throw `NumberFormatException: For input string: "<segment>"`, printed
three times (`[ERROR] Internal error: ...`, `org.apache.maven.InternalErrorException: Internal error: ...`, `Caused by: ...`). When the segment read as the OFFLINE wording
(`Cannot access central`, `in offline mode`) the acquisition decision ran a registry acquisition, an offline retry and wrote the Kriya-authored `MAVEN_ACQUISITION_INCOMPLETE:`
marker (3 mvn invocations, 1 acquisition); a transfer wording changed the validate code. The closed decision-view table now lists exactly that exception class + message template.

Every case in the fixture is REAL Maven output. The generic Internal-error negative controls below are SYNTHETIC: they exist only to prove that an Internal error of any other
exception, at another level or in another shape stays visible to the classifier; they are not Maven behaviour.

Two residuals of this table were fixed afterwards (Step-3 residuals, owner decision A): a recognised modelVersion abort opening is a typed STOP before any resolution / model-problem scan, so
a newline-forged resolution line starts no acquisition and model-problem wording in the value cannot become a conviction. The last two tests below pin that for the table-level fixture
(tests/test_w216a_model_text_guard.py proves it end to end for every position).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from _dependency_scope import hold_dependency_scope

from kriya.config.config import AutonomyConfig
from kriya.tools import dependency_execution as de
from kriya.tools.containment import NetworkAuthority
from kriya.tools.dependency_execution import (
    MAVEN_COMPILE_NO_DIAGNOSTICS,
    MAVEN_VALIDATE_NO_DIAGNOSTICS,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    MAVEN_VALIDATE_RESOLUTION_UNVERIFIED,
    OfflineFailureKind,
    classify_compile_failure,
    classify_maven_offline_failure_text,
    classify_maven_validate_failure,
    maven_resolution_decision_view,
)
from kriya.tools.validate import PolymorphicValidator

FIXTURE = json.loads((Path(__file__).resolve().parent / "fixtures" / "maven_validate" / "recorded_model_version.json").read_text(encoding="utf-8"))
CASES = FIXTURE["cases"]
MARKER = "MAVEN_ACQUISITION_INCOMPLETE:"
BENIGN = "mv_benign"
RESOLUTION_ECHOES = ["mv_echo_offline_recorded", "mv_echo_offline_dotfree", "mv_echo_offline_phrase_only", "mv_echo_after_numeric_prefix",
                     "mv_echo_transfer_recorded", "mv_echo_transfer_dotfree"]
OFFLINE_ECHOES = ["mv_echo_offline_recorded", "mv_echo_offline_dotfree", "mv_echo_offline_phrase_only", "mv_echo_after_numeric_prefix"]
MODEL_PROBLEM_ECHOES = ["mvb_echo_unknown_packaging", "mvb_echo_at_line_column", "mvb_echo_malformed_pom", "mvb_echo_child_module"]
NUMBER_FORMAT_CASES = [BENIGN, *RESOLUTION_ECHOES, "probe_mv_huge_number", *MODEL_PROBLEM_ECHOES]

# SYNTHETIC NEGATIVE CONTROLS (not Maven output): Internal-error lines that are NOT the measured modelVersion template. Each must stay in the decision view.
SYNTHETIC_GENERIC_INTERNAL_ERRORS = [
    "[ERROR] Internal error: java.lang.NullPointerException -> [Help 1]",
    "[ERROR] Internal error: java.lang.IllegalStateException: Cannot access central (https://repo.maven.apache.org/maven2) in offline mode -> [Help 1]",
    "[FATAL] Internal error: java.lang.IllegalStateException: Could not transfer artifact g:x:pom:1 from/to central -> [Help 1]",
    "org.apache.maven.InternalErrorException: Internal error: java.lang.IllegalStateException: Cannot access central in offline mode",
    "Caused by: java.lang.IllegalStateException: Cannot access central in offline mode",
    "Caused by: java.io.IOException: Could not transfer artifact g:x:pom:1 from/to central",
    # the right exception but not the measured template / level: not listed
    "[ERROR] Internal error: java.lang.NumberFormatException: null -> [Help 1]",
    "[ERROR] Internal error: java.lang.NumberFormatException: For input string: Cannot access central in offline mode -> [Help 1]",   # the template without the JDK's quote
    "[WARNING] Internal error: java.lang.NumberFormatException: For input string: \"Cannot access central in offline mode\"",
    "[INFO] Internal error: java.lang.NumberFormatException: For input string: \"Cannot access central in offline mode\"",
    "    Internal error: java.lang.NumberFormatException: For input string: \"Cannot access central in offline mode\"",
    "[ERROR] Internal error: java.lang.IllegalArgumentException: java.lang.NumberFormatException: For input string: \"Cannot access central in offline mode\"",
]


def _raw(name):
    case = CASES[name]
    return f"{case['stdout']}\n{case['stderr']}"


def _lines_with_value(name):
    return [line for line in _raw(name).splitlines() if "For input string" in line]


def _disposition(name):
    case = CASES[name]
    raw = _raw(name)
    return (classify_maven_validate_failure(case["returncode"], case["timeout"], raw), classify_maven_offline_failure_text(raw),
            classify_compile_failure("maven", case["returncode"], False, raw))


class _Maven:
    """Scripted process runner for ``_run_cmd_with_timeout``: every attempt answers with the recorded raw Maven output and is recorded with its network authority."""

    def __init__(self, case):
        self.case, self.calls = case, []

    def __call__(self, cmd, cwd, timeout=300, network=NetworkAuthority.DENIED, acquisition=False, **kwargs):  # pylint: disable=unused-argument
        self.calls.append({"cmd": cmd, "network": network, "acquisition": acquisition})
        return {"returncode": self.case["returncode"], "stdout": self.case["stdout"], "stderr": self.case["stderr"], "timeout": False}

    @property
    def acquisitions(self):
        return [call for call in self.calls if call["acquisition"] or call["network"] is not NetworkAuthority.DENIED]


def _gate(tmp_path, name):
    case = CASES[name]
    (tmp_path / "pom.xml").write_text(case["pom"], encoding="utf-8")
    validator = hold_dependency_scope(PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True)))
    maven = _Maven(case)
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        result = validator.run_pom_validate()
    return result, maven, case


def test_the_fixture_is_real_maven_with_provenance():
    provenance = FIXTURE["_provenance"]
    assert provenance["mvn_version_output"][0].startswith("Apache Maven 3.9") and provenance["kriya_head"] == "72e08a2"
    assert "none: every case here is REAL Maven output" in provenance["synthetic_cases"]
    assert all(case["returncode"] == 1 and not case["timeout"] for case in CASES.values())


# ----------------------------------------------------------------------------------------------- control 1 and 8: structure, benign, the view alone

def test_the_measured_structure_is_three_lines_quoting_the_candidate_value_in_the_jdk_template():
    for name in NUMBER_FORMAT_CASES:
        lines = _lines_with_value(name)
        assert len(lines) == 3, name
        assert lines[0].startswith("[ERROR] Internal error: java.lang.NumberFormatException: For input string: \"") and lines[0].endswith("\" -> [Help 1]"), name
        assert lines[1].startswith("org.apache.maven.InternalErrorException: Internal error: java.lang.NumberFormatException: For input string: \""), name
        assert lines[2].startswith("Caused by: java.lang.NumberFormatException: For input string: \""), name


@pytest.mark.parametrize("name", NUMBER_FORMAT_CASES)
def test_the_decision_view_alone_removes_exactly_the_three_candidate_value_lines(name):
    raw = _raw(name)
    before = raw
    view = maven_resolution_decision_view(raw)
    assert raw == before
    dropped = [line for line in raw.splitlines() if line not in set(view.splitlines())]
    assert dropped == _lines_with_value(name)
    assert "For input string" not in view and "Internal error" not in view


# ---------------------------------------------------------------------------------- controls 2-5: echo vs benign, zero acquisition, zero marker

@pytest.mark.parametrize("name", RESOLUTION_ECHOES)
def test_a_resolution_looking_model_version_echo_has_the_benign_disposition(name):
    assert _disposition(BENIGN) == (MAVEN_VALIDATE_NO_DIAGNOSTICS, OfflineFailureKind.ORDINARY_FAILURE, MAVEN_COMPILE_NO_DIAGNOSTICS)
    assert _disposition(name) == _disposition(BENIGN)
    assert MAVEN_VALIDATE_NO_DIAGNOSTICS in MAVEN_VALIDATE_NO_VERDICT_CODES


@pytest.mark.parametrize("name", RESOLUTION_ECHOES)
def test_a_model_version_echo_triggers_zero_acquisition_zero_retry_and_no_marker(tmp_path, name):
    result, maven, case = _gate(tmp_path, name)
    assert len(maven.calls) == 1 and maven.acquisitions == []          # one offline attempt: no registry acquisition, no offline retry
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_NO_DIAGNOSTICS
    assert MARKER not in result["output"]
    benign, benign_maven, _ = _gate(tmp_path, BENIGN)
    assert (result["success"], result.get("environment_reason_code"), len(maven.calls)) == (benign["success"], benign.get("environment_reason_code"), len(benign_maven.calls))
    assert case["stdout"] in result["output"]                         # control 7 at the gate: the captured evidence is in the result exactly, echo included


@pytest.mark.parametrize("name", OFFLINE_ECHOES)
def test_the_offline_wording_never_reaches_the_acquisition_text_decision(name):
    assert "in offline mode" in _raw(name).lower() or "cannot access central" in _raw(name).lower()   # the raw text does carry the wording ...
    assert classify_maven_offline_failure_text(_raw(name)) is OfflineFailureKind.ORDINARY_FAILURE      # ... and the decision does not read it


# ------------------------------------------------------------------------------------------------------------- control 7: raw evidence

@pytest.mark.parametrize("name", NUMBER_FORMAT_CASES)
def test_raw_evidence_keeps_the_original_model_version_line(name):
    """No production path hands the decision view on as evidence, so there is no substitution to mutate; this pins the invariant directly: the raw text, and the gate result built
    from it, still carry the candidate's original value."""
    raw = _raw(name)
    assert _lines_with_value(name) and all(line in raw for line in _lines_with_value(name))
    maven_resolution_decision_view(raw)
    assert raw == f"{CASES[name]['stdout']}\n{CASES[name]['stderr']}"


# ------------------------------------------------------------------------- control 6: generic Internal errors stay visible (SYNTHETIC) and fail closed

@pytest.mark.parametrize("line", SYNTHETIC_GENERIC_INTERNAL_ERRORS)
def test_synthetic_generic_internal_errors_stay_in_the_decision_view(line):
    assert maven_resolution_decision_view(line) == line


@pytest.mark.parametrize("line", [line for line in SYNTHETIC_GENERIC_INTERNAL_ERRORS if "offline mode" in line or "Could not transfer" in line])
def test_synthetic_generic_internal_error_with_resolution_wording_is_still_read_as_resolution_evidence(line):
    """Fail closed: an Internal error that is not the measured template is not a Maven model problem, so its resolution wording is still evidence (a no-verdict stop)."""
    assert classify_maven_validate_failure(1, False, line) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED


def test_the_real_non_model_version_internal_error_forms_in_the_old_table_still_hold():
    """The earlier Step-3 table is unchanged: its openings are still dropped, a genuine resolution line is still kept."""
    assert maven_resolution_decision_view("[ERROR] Unknown packaging: in offline mode @ line 1, column 163") == ""
    genuine = "[ERROR] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode @ line 3, column 5"
    assert maven_resolution_decision_view(genuine) == genuine


# ------------------------------------------------------------------------------------------------------- the real modelVersion model-problem forms

@pytest.mark.parametrize("name", ["probe_mv_4_0", "probe_mv_5_0_0", "probe_mv_4_0_0_1", "probe_mv_neg", "probe_mv_empty"])
def test_numeric_and_empty_model_version_faults_are_ordinary_model_problems_unchanged(name):
    """Real Maven prints these as located `'modelVersion' ...` model problems (no Internal error): the table change does not touch them."""
    assert _disposition(name)[0] is None and _disposition(name)[1] is OfflineFailureKind.ORDINARY_FAILURE


# ------------------------------------------------------------------------------------------------------------- the modelVersion abort (owner decision A)

def test_a_newline_in_the_model_version_value_is_the_benign_stop_and_runs_no_acquisition(tmp_path):
    """Was a KNOWN RESIDUAL (1 acquisition + marker, RESOLUTION_UNVERIFIED): the recognised abort opening stops before the forged line is read as resolution evidence."""
    name = "mv_newline_forged_resolution_line"
    assert _disposition(name) == _disposition(BENIGN)
    result, maven, _ = _gate(tmp_path, name)
    assert len(maven.calls) == 1 and maven.acquisitions == [] and MARKER not in result["output"]
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_NO_DIAGNOSTICS


@pytest.mark.parametrize("name", MODEL_PROBLEM_ECHOES)
def test_model_problem_wording_in_the_model_version_value_cannot_become_a_conviction(tmp_path, name):
    """Was a KNOWN RESIDUAL (the raw-reading model-problem check returned None = convicted): the benign STOP holds, and the gate result is the typed no-verdict, never a POM-repair offer."""
    assert _disposition(name) == _disposition(BENIGN)
    result, maven, case = _gate(tmp_path, name)
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_NO_DIAGNOSTICS and not result["output"].startswith("Maven POM validation failed:")
    assert len(maven.calls) == 1 and maven.acquisitions == [] and case["stdout"] in result["output"]


def test_the_module_table_is_the_one_the_view_reads():
    assert maven_resolution_decision_view.__module__ == de.__name__
    assert de._MAVEN_MODEL_PROBLEM_WRAPPER_RE.match(_lines_with_value(BENIGN)[0])   # pylint: disable=protected-access
