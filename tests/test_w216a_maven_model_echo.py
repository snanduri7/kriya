"""W2-16a (Wave-2 Step 3): candidate-controlled Maven model-problem text must not masquerade as dependency-resolution evidence.

MEASURED at 9ec09d9 (real contained Maven 3.9.16, network denied; tests/fixtures/maven_validate/recorded_model_echo.json, capture script and the pre-fix table in the Step-3
evidence directory): Maven quotes the candidate's own values (a packaging, a module name, an id, a coordinate, a scope, the text around a parser fault) inside its model-problem
lines. Resolution-looking words in such a value (a) made `classify_maven_validate_failure` stop on a POM Maven had genuinely judged, and (b) when they read as the OFFLINE wording
(`in offline mode`), made `PolymorphicValidator._run_maven_cmd` run a registry-scoped acquisition, an offline retry and write the Kriya-authored
`MAVEN_ACQUISITION_INCOMPLETE:` marker, which the classifier then trusted. One shared decision view (`maven_resolution_decision_view`) now feeds both decisions (and the compile
classifier's resolution check); the captured output is never replaced.

Metamorphic rule: the same POM position with an ECHO value vs a BENIGN value (no resolution wording) must reach the same disposition. Two positions are NOT misclassifications and
are pinned as such: a parent `relativePath` (the parent really cannot be resolved, so every variant is no-verdict) and the historical Pass-C scope form (a self-referencing dependency,
for which Maven never evaluates the scope). `modelVersion` is an `Internal error:` line, deliberately outside the closed table: stop either way, the code name may differ."""
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from _dependency_scope import hold_dependency_scope

from kriya.config.config import AutonomyConfig
from kriya.tools.containment import NetworkAuthority
from kriya.tools.dependency_execution import (
    MAVEN_COMPILE_NO_DIAGNOSTICS,
    MAVEN_COMPILE_RESOLUTION_UNVERIFIED,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    MAVEN_VALIDATE_RESOLUTION_UNVERIFIED,
    OfflineFailureKind,
    classify_compile_failure,
    classify_maven_offline_failure_text,
    classify_maven_validate_failure,
    maven_resolution_decision_view,
)
from kriya.tools.validate import PolymorphicValidator

FIXTURES = Path(__file__).resolve().parent / "fixtures"
RECORDED = json.loads((FIXTURES / "maven_validate" / "recorded_model_echo.json").read_text(encoding="utf-8"))
CASES = RECORDED["cases"]
MARKER = "MAVEN_ACQUISITION_INCOMPLETE:"

# Positions where neither value creates a resolution failure: echo, echo (offline wording) and benign all reach the same disposition - Maven judged the POM (conviction, None).
CONVICTED_POSITIONS = ["packaging", "module_name", "artifact_id_invalid_id", "dependency_group_id", "dependency_scope", "unrecognised_tag_seen_snippet", "duplicated_tag_seen_snippet"]
# Maven never evaluates the scope here (the dependency references the project itself): not a resolution test, convicted in every variant.
NOT_A_RESOLUTION_TEST = ["dependency_scope_self_reference"]
GENUINE = sorted(name for name in CASES if name.startswith("genuine_"))


def _raw(case):
    """What `run_pom_validate` classifies: stdout and stderr exactly as captured."""
    return f"{case['stdout']}\n{case['stderr']}"


def _validate_code(name):
    case = CASES[name]
    return classify_maven_validate_failure(case["returncode"], case["timeout"], _raw(case))


def _offline_kind(name):
    return classify_maven_offline_failure_text(_raw(CASES[name]))


def test_the_fixture_is_real_maven_with_provenance():
    provenance = RECORDED["_provenance"]
    assert provenance["mvn_version_output"][0].startswith("Apache Maven 3.9") and provenance["kriya_head"] == "9ec09d9"
    assert all(case["returncode"] == 1 and not case["timeout"] for case in CASES.values())


# ------------------------------------------------------------------------------------------------------ metamorphic: echo vs benign

@pytest.mark.parametrize("position", CONVICTED_POSITIONS + NOT_A_RESOLUTION_TEST)
@pytest.mark.parametrize("kind", ["echo_transfer", "echo_offline"])
def test_resolution_wording_in_a_candidate_value_does_not_change_the_validate_disposition(position, kind):
    assert _validate_code(f"{position}:benign") is None  # the control: Maven judged the POM
    assert _validate_code(f"{position}:{kind}") is None
    assert _offline_kind(f"{position}:{kind}") is OfflineFailureKind.ORDINARY_FAILURE  # ... and the acquisition decision reads no missing dependency


@pytest.mark.parametrize("kind", ["benign", "echo_transfer", "echo_offline"])
def test_a_parent_relative_path_is_a_genuine_unresolved_parent_in_every_variant(kind):
    assert _validate_code(f"parent_relative_path:{kind}") == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED
    assert _offline_kind(f"parent_relative_path:{kind}") is OfflineFailureKind.MISSING_DEPENDENCY


@pytest.mark.parametrize("kind", ["benign", "echo_transfer", "echo_offline"])
def test_a_model_version_internal_error_is_a_stop_in_every_variant(kind):
    """`Internal error:` is outside the closed table (owner decision): the code name may differ between variants, never the stop. KNOWN RESIDUAL: its offline-wording echo can still
    read as a missing dependency in the acquisition decision (stop -> stop; recorded in the Step-3 evidence)."""
    assert _validate_code(f"model_version:{kind}") in MAVEN_VALIDATE_NO_VERDICT_CODES


# ------------------------------------------------------------------------------------------------------------------ genuine resolution

@pytest.mark.parametrize("name", GENUINE)
def test_a_genuine_maven_resolution_failure_stays_a_no_verdict_resolution_outcome(name):
    assert _validate_code(name) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED
    assert _offline_kind(name) is OfflineFailureKind.MISSING_DEPENDENCY
    # the compile gate reads the same evidence with its own code
    case = CASES[name]
    assert classify_compile_failure("maven", case["returncode"], False, _raw(case)) == MAVEN_COMPILE_RESOLUTION_UNVERIFIED


def test_the_genuine_resolution_line_survives_in_the_decision_view():
    """The exclusion is by line opening, so a genuine cascade keeps its resolution line: the parent POM failure is present in the view of every genuine case."""
    for name in GENUINE:
        view = maven_resolution_decision_view(_raw(CASES[name]))
        assert any(opening in view for opening in ("Non-resolvable parent POM", "Non-resolvable import POM", "Unresolveable build extension", "could not be resolved")), name


# ------------------------------------------------------------------------------------------------- the decision view is a view only

def test_the_view_only_drops_lines_of_the_closed_table_and_never_changes_the_captured_text():
    for name, case in CASES.items():
        raw = _raw(case)
        before = raw
        view_lines = maven_resolution_decision_view(raw).splitlines()
        raw_lines = raw.splitlines()
        assert raw == before, name
        iterator = iter(raw_lines)
        assert all(line in iterator for line in view_lines), f"{name}: the view must be an in-order subsequence of the raw lines"
        dropped = [line for line in raw_lines if line not in set(view_lines)]
        assert all(line.lstrip().startswith("[") for line in dropped), name


@pytest.mark.parametrize("line", [
    "[ERROR] Unknown packaging: Could not transfer artifact x @ line 1, column 163",
    "[ERROR]     Unknown packaging: in offline mode @ line 1, column 163",
    "[ERROR] [ERROR] Unknown packaging: in offline mode",
    "[ERROR] Child module /kriya/workspace/in offline mode of /kriya/workspace/pom.xml does not exist @ ",
    "[ERROR] 'artifactId' with value 'Could not transfer' does not match a valid id pattern. @ line 1, column 118",
    "[FATAL] 'dependencies.dependency.version' for Could not transfer:b:jar is missing. @ line 1, column 178",
    "[WARNING] 'dependencies.dependency.scope' for g:b:jar must be one of [compile] but is 'in offline mode'. @ line 1, column 231",
    "[ERROR]   The project g:Could not transfer:1 (/kriya/workspace/pom.xml) has 1 error",
    "[ERROR]   The project  (/kriya/workspace/pom.xml) has 2 errors",
    "[ERROR] Malformed POM /kriya/workspace/pom.xml: Unrecognised tag: 'x' (position: START_TAG seen ...in offline mode--><x/>... @1:248)",
    "[ERROR]     Non-parseable POM /kriya/workspace/pom.xml: Duplicated tag: 'version' (position: seen ...in offline mode...",
])
def test_each_opening_of_the_closed_table_is_dropped_from_the_view(line):
    assert maven_resolution_decision_view(line) == ""


@pytest.mark.parametrize("line", [
    "[ERROR] Non-resolvable parent POM for g:a:1: Could not transfer artifact g:p:pom:1 from/to central @ line 3, column 5",
    "[FATAL] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode",
    "[ERROR] Non-resolvable import POM: The following artifacts could not be resolved: g:bom:pom:1 (absent)",
    "[ERROR] Unresolveable build extension: Plugin g:x:1 or one of its dependencies could not be resolved:",
    "[ERROR] Plugin g:x:1 or one of its dependencies could not be resolved:",
    "[ERROR] \tCannot access central (https://repo.maven.apache.org/maven2) in offline mode and the artifact g:x:jar:1 has not been downloaded",
    "[ERROR] Failed to execute goal g:p:1:x on project a: Unknown packaging: x Could not transfer artifact g:y:pom:1",
    "[ERROR] Internal error: java.lang.NumberFormatException: For input string: \"Cannot access central (https://repo\" -> [Help 1]",
    "[ERROR] Some other error mentioning Unknown packaging: in offline mode",
    "[INFO] Unknown packaging: in offline mode",
    "Unknown packaging: in offline mode",
    "\tThe following artifacts could not be resolved: g:x:pom:1 (absent): Could not transfer artifact g:x:pom:1 from/to central",
    "MAVEN_ACQUISITION_INCOMPLETE: offline execution for goals ['validate'] still lacks",
    "[ERROR] 'artifactId' without the closed continuation: in offline mode",
])
def test_everything_outside_the_closed_table_stays_in_the_view(line):
    """No arbitrary `[ERROR]`/`[FATAL]` suppression: genuine resolution diagnostics, `Internal error:`, an unprefixed line and the Kriya marker are all kept."""
    assert maven_resolution_decision_view(line) == line


def test_the_view_is_idempotent():
    for case in CASES.values():
        once = maven_resolution_decision_view(_raw(case))
        assert maven_resolution_decision_view(once) == once


def test_a_newline_in_a_candidate_value_can_only_forge_resolution_evidence_never_hide_it():
    """KNOWN RESIDUAL (fail closed): a value containing a newline can place a forged resolution line in the view - a stop - but a genuine resolution line is never dropped."""
    forged = "[ERROR] Unknown packaging: x\n[ERROR] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode @ line 1"
    assert classify_maven_validate_failure(1, False, forged) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED
    assert classify_maven_offline_failure_text(forged) is OfflineFailureKind.MISSING_DEPENDENCY


# ------------------------------------------------------------------------------------------- the acquisition decision (second producer)

class _Maven:
    """Scripted process runner for ``_run_cmd_with_timeout``: every attempt answers with the recorded raw Maven output; every call is recorded with its network authority."""

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


@pytest.mark.parametrize("position", CONVICTED_POSITIONS)
@pytest.mark.parametrize("kind", ["echo_transfer", "echo_offline"])
def test_candidate_text_never_triggers_acquisition_a_retry_or_the_marker(tmp_path, position, kind):
    result, maven, case = _gate(tmp_path, f"{position}:{kind}")
    assert len(maven.calls) == 1 and maven.acquisitions == []  # one offline attempt: no registry acquisition, no offline retry
    assert result["success"] is False and "environment_reason_code" not in result  # Maven's judgement stands: the POM is offered for repair
    assert result["output"].startswith("Maven POM validation failed:")
    assert MARKER not in result["output"]
    assert case["stdout"] in result["output"] and case["stderr"] in result["output"]  # the captured evidence is preserved exactly, echo included


@pytest.mark.parametrize("position", CONVICTED_POSITIONS)
def test_the_echo_and_benign_variants_are_indistinguishable_at_the_gate(tmp_path, position):
    echo, echo_maven, _ = _gate(tmp_path, f"{position}:echo_offline")
    benign, benign_maven, _ = _gate(tmp_path, f"{position}:benign")
    assert echo["success"] == benign["success"] is False
    assert ("environment_reason_code" in echo) == ("environment_reason_code" in benign) is False
    assert len(echo_maven.calls) == len(benign_maven.calls) == 1


@pytest.mark.parametrize("name", ["genuine_unresolvable_parent", "genuine_unresolvable_parent_plus_bad_packaging", "genuine_unresolvable_bom_import",
                                  "genuine_plugin_bound_to_validate", "genuine_build_extension"])
def test_a_genuine_resolution_failure_still_acquires_once_and_ends_as_the_marked_no_verdict(tmp_path, name):
    result, maven, _ = _gate(tmp_path, name)
    assert [call["acquisition"] for call in maven.calls] == [False, True, False]  # offline, ONE bounded acquisition, offline retry
    assert result["environment_reason_code"] == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED and MARKER in result["output"]


# ------------------------------------------------------------------------------------------------------------------ compile classifier

@pytest.mark.parametrize("kind", ["echo_transfer", "echo_offline", "benign"])
def test_the_compile_gate_reads_model_echo_like_the_benign_variant(kind):
    """The compile gate never convicts without a located SOURCE diagnostic, so this is no-verdict either way; only the attribution must not follow the candidate's text."""
    case = CASES[f"packaging:{kind}"]
    assert classify_compile_failure("maven", case["returncode"], False, _raw(case)) == MAVEN_COMPILE_NO_DIAGNOSTICS
