"""W2-16a (Wave-2 Step 3, residuals): candidate Maven model text must not control a dependency acquisition or a conviction - modelVersion (decision A) and seven measured fields (B-prime).

MEASURED at ed34389 (real contained Maven 3.9.16, network denied; tests/fixtures/maven_validate/recorded_model_text_matrix.json = 12 positions x 4 variants, REAL Maven output with the
exact POM bytes that produced it; capture scripts, the pre-change table and the harness prototype in the WAVE2_STEP3_RESIDUALS_2026-10-29 evidence directory):

* a line break in the value of `packaging`, a `<module>`, the project `artifactId` / `groupId` or a dependency's `groupId` / `artifactId` / `scope` lets the rest of the value stand as a line
  of its own in Maven's output; a forged `Non-resolvable ... in offline mode` line then ran a registry acquisition, an offline retry and wrote `MAVEN_ACQUISITION_INCOMPLETE:` (3 mvn calls);
* the same for a non-numeric `modelVersion`, whose value also reached the raw-reading model-problem check (model-problem wording turned the benign stop into a conviction).

Decision A: the recognised `NumberFormatException` model-read abort opening is a typed STOP before any text is scanned. Decision B-prime: an exact, evidence-driven table of seven fields; when
the candidate's own POM set (root + declared modules) holds a line break in one of them (or cannot be read), Maven's combined text is unattributable and the text-derived acquisition and the
text-derived POM conviction are withheld. The guard is a precondition, not a verdict: raw output is never altered, a timeout / signal / invocation failure keeps its own code.

SYNTHETIC elements are labelled: the reconstructed malformed POM for the recorded `malformed_xml` output, and the POMs written by the guard unit tests (no Maven involved there).
"""
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from _dependency_scope import hold_dependency_scope

from kriya.config.config import AutonomyConfig
from kriya.tools import dependency_execution as de
from kriya.tools.containment import NetworkAuthority
from kriya.tools.dependency_execution import (
    MAVEN_NEWLINE_ECHO_FIELDS,
    MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED,
    MAVEN_VALIDATE_NO_DIAGNOSTICS,
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    MAVEN_VALIDATE_RESOLUTION_UNVERIFIED,
    MAVEN_VALIDATE_TERMINATED,
    MAVEN_VALIDATE_TIMED_OUT,
    OfflineFailureKind,
    classify_compile_failure,
    classify_maven_offline_failure_text,
    classify_maven_validate_failure,
    maven_model_read_abort,
    maven_model_text_assessment,
)
from kriya.tools.validate import PolymorphicValidator

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maven_validate"
MATRIX = json.loads((FIXTURES / "recorded_model_text_matrix.json").read_text(encoding="utf-8"))["cases"]
ECHO = json.loads((FIXTURES / "recorded_model_echo.json").read_text(encoding="utf-8"))["cases"]
RECORDED_VALIDATE = json.loads((FIXTURES / "recorded_mvn_validate.json").read_text(encoding="utf-8"))
MARKER = "MAVEN_ACQUISITION_INCOMPLETE:"
VARIANTS = ["benign", "offline", "newline_offline", "model_problem"]
# B-prime: matrix position -> the table field it exercises. The table is exact; every membership has a direct test below.
SEVEN = {"packaging": "packaging", "module_name": "module", "artifact_id": "artifactId", "project_group_id": "groupId", "dependency_group_id": "dependency.groupId",
         "dependency_artifact_id": "dependency.artifactId", "dependency_scope": "dependency.scope"}
NOT_ECHOED_IN_THE_DANGEROUS_FORM = ["unrecognised_tag_seen_snippet", "duplicated_tag_seen_snippet"]   # MEASURED: Maven does not print the payload line there
ALL_POSITIONS = [*SEVEN, "dependency_scope_self_reference", *NOT_ECHOED_IN_THE_DANGEROUS_FORM, "model_version", "parent_relative_path"]


class _Maven:
    """Scripted process runner for ``_run_cmd_with_timeout``: every call answers with the recorded raw output and is recorded with its network authority."""

    def __init__(self, case):
        self.case, self.calls = case, []

    def __call__(self, cmd, cwd, timeout=300, network=NetworkAuthority.DENIED, acquisition=False, **kwargs):  # pylint: disable=unused-argument
        self.calls.append({"cmd": cmd, "network": network, "acquisition": acquisition})
        return {"returncode": self.case["returncode"], "stdout": self.case["stdout"], "stderr": self.case["stderr"], "timeout": self.case.get("timeout", False)}

    @property
    def acquisitions(self):
        return [call for call in self.calls if call["acquisition"] or call["network"] is not NetworkAuthority.DENIED]


def _validator(tmp_path, pom):
    (tmp_path / "pom.xml").write_text(pom, encoding="utf-8")
    return hold_dependency_scope(PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True)))


def _gate(tmp_path, case, pom=None):
    validator = _validator(tmp_path, pom if pom is not None else case["pom"])
    maven = _Maven(case)
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        result = validator.run_pom_validate()
    return result, maven


def _sub(tmp_path, name):
    path = tmp_path / name
    path.mkdir()
    return path


def _convicted(result):
    return result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven POM validation failed:")


# --------------------------------------------------------------------------------------------------------------------------------------------- the fixture

def test_the_matrix_is_real_maven_for_every_position_and_variant():
    assert {case["position"] for case in MATRIX.values()} == set(ALL_POSITIONS)
    assert all(f"{position}:{variant}" in MATRIX for position in ALL_POSITIONS for variant in VARIANTS)
    assert all(case["returncode"] == 1 and not case["timeout"] for case in MATRIX.values())
    assert json.loads((FIXTURES / "recorded_model_text_matrix.json").read_text(encoding="utf-8"))["_provenance"]["mvn_version_output"][0].startswith("Apache Maven 3.9")


def test_the_allowlist_is_exactly_the_seven_measured_fields():
    assert sorted(MAVEN_NEWLINE_ECHO_FIELDS) == sorted(SEVEN.values()) and len(MAVEN_NEWLINE_ECHO_FIELDS) == 7


# -------------------------------------------------------------------------------- B-prime: the seven fields, end to end (real output, real POM bytes)

@pytest.mark.parametrize("variant", ["benign", "offline", "model_problem"])
@pytest.mark.parametrize("position", SEVEN)
def test_a_single_line_value_keeps_the_existing_conviction_with_no_acquisition(tmp_path, position, variant):
    result, maven = _gate(tmp_path, MATRIX[f"{position}:{variant}"])
    assert _convicted(result), (position, variant)
    assert len(maven.calls) == 1 and maven.acquisitions == []


@pytest.mark.parametrize("position", SEVEN)
def test_a_newline_plus_offline_wording_is_the_contamination_stop_with_zero_acquisition_retry_and_marker(tmp_path, position):
    case = MATRIX[f"{position}:newline_offline"]
    assert "Non-resolvable parent POM" in case["stdout"]                      # the real Maven output does carry the forged line ...
    result, maven = _gate(tmp_path, case)
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED   # ... and it decides nothing
    assert len(maven.calls) == 1 and maven.acquisitions == []                # no mvn retry, no registry-network call
    assert MARKER not in result["output"] and not _convicted(result)
    assert f"<{SEVEN[position]}>" in result["model_text_contamination"]
    assert case["stdout"] in result["output"] and case["stderr"] in result["output"]   # raw evidence preserved exactly


@pytest.mark.parametrize("position", SEVEN)
def test_the_contamination_stop_needs_the_candidate_pom_not_the_wording(tmp_path, position):
    """Same Maven text, a POM with no line break: the guard is not what the text said. (The text alone is not enough to trigger it, in either direction.)"""
    case = MATRIX[f"{position}:newline_offline"]
    clean_pom = MATRIX[f"{position}:benign"]["pom"]
    result, maven = _gate(tmp_path, case, pom=clean_pom)
    assert "model_text_contamination" not in result and result["environment_reason_code"] == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED   # the previous text-derived behaviour, exactly
    assert len(maven.acquisitions) == 1


# ---------------------------------------------------------------------------------------------------- decision A: modelVersion, all variants identical

@pytest.mark.parametrize("variant", VARIANTS)
def test_model_version_every_variant_is_the_benign_stop(tmp_path, variant):
    benign, benign_maven = _gate(_sub(tmp_path, "b"), MATRIX["model_version:benign"])
    case = MATRIX[f"model_version:{variant}"]
    assert maven_model_read_abort(case["stdout"] + case["stderr"])
    result, maven = _gate(_sub(tmp_path, "r"), case)
    assert result["environment_reason_code"] == benign["environment_reason_code"] == MAVEN_VALIDATE_NO_DIAGNOSTICS
    assert result["success"] is False and not _convicted(result)
    assert len(maven.calls) == len(benign_maven.calls) == 1 and maven.acquisitions == []
    assert MARKER not in result["output"] and case["stdout"] in result["output"]


@pytest.mark.parametrize("variant", VARIANTS)
def test_model_version_text_classifiers_agree(variant):
    case = MATRIX[f"model_version:{variant}"]
    raw = f"{case['stdout']}\n{case['stderr']}"
    assert classify_maven_offline_failure_text(raw) is OfflineFailureKind.ORDINARY_FAILURE
    assert classify_maven_validate_failure(1, False, raw) == MAVEN_VALIDATE_NO_DIAGNOSTICS
    assert classify_compile_failure("maven", 1, False, raw) == de.MAVEN_COMPILE_NO_DIAGNOSTICS


def test_a_generic_internal_error_is_not_recognised_as_the_abort():
    for line in ("[ERROR] Internal error: java.lang.NullPointerException -> [Help 1]",
                 "[ERROR] Internal error: java.lang.IllegalStateException: Cannot access central in offline mode -> [Help 1]",
                 "[WARNING] Internal error: java.lang.NumberFormatException: For input string: \"x\"",
                 "[ERROR] Internal error: java.lang.NumberFormatException: For input string: x"):
        assert not maven_model_read_abort(line)
    assert classify_maven_offline_failure_text("[ERROR] Internal error: java.lang.IllegalStateException: Cannot access central in offline mode") is OfflineFailureKind.MISSING_DEPENDENCY


# -------------------------------------------------------------------------------------------------------------- measured non-fix cases stay as they were

@pytest.mark.parametrize("variant", VARIANTS)
@pytest.mark.parametrize("position", NOT_ECHOED_IN_THE_DANGEROUS_FORM)
def test_positions_where_maven_does_not_echo_the_payload_line_are_unchanged(tmp_path, position, variant):
    result, maven = _gate(tmp_path, MATRIX[f"{position}:{variant}"])
    assert _convicted(result) and len(maven.calls) == 1 and maven.acquisitions == []


@pytest.mark.parametrize("variant", ["benign", "offline", "model_problem"])
def test_the_self_referencing_dependency_scope_is_unchanged_for_single_line_values(tmp_path, variant):
    result, maven = _gate(tmp_path, MATRIX[f"dependency_scope_self_reference:{variant}"])
    assert _convicted(result) and len(maven.calls) == 1 and maven.acquisitions == []


def test_the_self_referencing_dependency_scope_with_a_line_break_is_stopped_by_the_field_table_not_by_a_special_case(tmp_path):
    """It shares the `dependency.scope` path with the measured field, so the field-based table stops it (the safe direction, a contrived input); no self-reference rule was added."""
    result, maven = _gate(tmp_path, MATRIX["dependency_scope_self_reference:newline_offline"])
    assert result["environment_reason_code"] == MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED and len(maven.calls) == 1 and maven.acquisitions == []


# ------------------------------------------------------------------------------------------------------------------------------------ genuine controls

@pytest.mark.parametrize("variant", VARIANTS)
def test_a_genuine_unresolved_parent_still_acquires_in_every_variant(tmp_path, variant):
    """`relativePath` is not in the table: the parent really cannot be resolved, whatever the value says."""
    result, maven = _gate(tmp_path, MATRIX[f"parent_relative_path:{variant}"])
    assert [call["acquisition"] for call in maven.calls] == [False, True, False]
    assert result["environment_reason_code"] == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED and MARKER in result["output"]


GENUINE = ["genuine_unresolvable_parent", "genuine_unresolvable_parent_plus_bad_packaging", "genuine_unresolvable_bom_import", "genuine_plugin_bound_to_validate", "genuine_build_extension"]


@pytest.mark.parametrize("name", GENUINE)
def test_every_genuine_resolution_fixture_keeps_its_typed_outcome_and_acquisition(tmp_path, name):
    result, maven = _gate(tmp_path, ECHO[name])
    assert [call["acquisition"] for call in maven.calls] == [False, True, False]
    assert result["environment_reason_code"] == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED and MARKER in result["output"]


# ------------------------------------------------------------------------------------------------- legitimate multi-line free text is NOT contamination

FREE_TEXT_LEAVES = {
    "description": "<description>line one\nline two</description>",
    "name": "<name>Demo\nProject</name>",
    "url": "<url>https://example.invalid/a\nb</url>",
    "inceptionYear": "<inceptionYear>20\n24</inceptionYear>",
    "properties": "<properties><note>a\nb</note></properties>",
    "license-comments": "<licenses><license><name>x</name><comments>c\nd</comments></license></licenses>",
    "plugin-configuration": "<build><plugins><plugin><groupId>org.x</groupId><artifactId>p</artifactId><configuration><arg>a\nb</arg></configuration></plugin></plugins></build>",
    "plugin-groupId": "<build><plugins><plugin><groupId>org.x\ny</groupId><artifactId>p</artifactId></plugin></plugins></build>",
    "dependency-version": "<dependencies><dependency><groupId>g</groupId><artifactId>b</artifactId><version>1\n2</version></dependency></dependencies>",
    "dependencyManagement-groupId": "<dependencyManagement><dependencies><dependency><groupId>g\nh</groupId><artifactId>b</artifactId></dependency></dependencies></dependencyManagement>",
    "parent-groupId": "<parent><groupId>g\nh</groupId><artifactId>p</artifactId><version>1</version></parent>",
    "profile-dependency-groupId": "<profiles><profile><id>x</id><dependencies><dependency><groupId>g\nh</groupId><artifactId>b</artifactId></dependency></dependencies></profile></profiles>",
}
BASE = '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version>{extra}</project>'


@pytest.mark.parametrize("leaf", FREE_TEXT_LEAVES)
def test_a_multiline_leaf_outside_the_allowlist_is_not_contamination(tmp_path, leaf):
    (tmp_path / "pom.xml").write_text(BASE.format(extra=FREE_TEXT_LEAVES[leaf]), encoding="utf-8")
    assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated is None and not assessment.malformed_poms and not assessment.withhold_acquisition


def test_a_genuine_missing_dependency_in_a_pom_with_multiline_descriptions_still_acquires(tmp_path):
    case = ECHO["genuine_unresolvable_parent"]
    pom = case["pom"].replace("</project>", "<description>first line\nsecond line</description><name>a\nb</name></project>")
    assert "\n" in pom.split("<description>")[1].split("</description>")[0]
    result, maven = _gate(tmp_path, case, pom=pom)
    assert [call["acquisition"] for call in maven.calls] == [False, True, False] and MARKER in result["output"]


# ------------------------------------------------------------------------------------------------- the guard itself (SYNTHETIC POMs, no Maven involved)

def _field_pom(field, value):
    snippets = {
        "packaging": f"<packaging>{value}</packaging>",
        "module": f"<packaging>pom</packaging><modules><module>{value}</module></modules>",
        "dependency.groupId": f"<dependencies><dependency><groupId>{value}</groupId><artifactId>b</artifactId></dependency></dependencies>",
        "dependency.artifactId": f"<dependencies><dependency><groupId>g</groupId><artifactId>{value}</artifactId></dependency></dependencies>",
        "dependency.scope": f"<dependencies><dependency><groupId>g</groupId><artifactId>b</artifactId><scope>{value}</scope></dependency></dependencies>",
    }
    if field == "artifactId":
        return BASE.replace("<artifactId>a</artifactId>", f"<artifactId>{value}</artifactId>").format(extra="")
    if field == "groupId":
        return BASE.replace("<groupId>g</groupId>", f"<groupId>{value}</groupId>").format(extra="")
    return BASE.format(extra=snippets[field])


LINE_BREAKS = ["\n", "\r", "&#10;", "&#13;", " ", "\x85"]


@pytest.mark.parametrize("line_break", LINE_BREAKS)
@pytest.mark.parametrize("field", sorted(MAVEN_NEWLINE_ECHO_FIELDS))
def test_each_allowlisted_field_with_each_line_break_is_contamination(tmp_path, field, line_break):
    for value in (f"x{line_break}y", f"{line_break}x", f"x{line_break}"):      # inside, leading, trailing: padding is not exempt (unstripped)
        (tmp_path / "pom.xml").write_text(_field_pom(field, value), encoding="utf-8")
        assessment = maven_model_text_assessment(str(tmp_path))
        assert assessment.contaminated and f"<{field}>" in assessment.contaminated and assessment.withhold_acquisition, (field, repr(value))


@pytest.mark.parametrize("field", sorted(MAVEN_NEWLINE_ECHO_FIELDS))
def test_each_allowlisted_field_with_a_single_line_value_is_clean(tmp_path, field):
    (tmp_path / "pom.xml").write_text(_field_pom(field, "zzz-benign-value"), encoding="utf-8")
    assert maven_model_text_assessment(str(tmp_path)).withhold_acquisition is False


def test_a_line_break_in_cdata_is_contamination(tmp_path):
    (tmp_path / "pom.xml").write_text(BASE.format(extra="<packaging><![CDATA[a\nb]]></packaging>"), encoding="utf-8")
    assert "<packaging>" in maven_model_text_assessment(str(tmp_path)).contaminated


def test_a_line_break_character_that_xml_forbids_is_a_malformed_pom_not_clean(tmp_path):
    (tmp_path / "pom.xml").write_text(BASE.format(extra="<packaging>a\x0bb</packaging>"), encoding="utf-8")
    assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated is None and assessment.malformed_poms == ("pom.xml",) and assessment.withhold_acquisition


def _write(root, rel, text):
    path = Path(root) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_the_pom_set_is_the_root_and_the_declared_modules_transitively(tmp_path):
    _write(tmp_path, "pom.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>m1</module><module>missing</module></modules>"))
    _write(tmp_path, "m1/pom.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>sub</module></modules>"))
    _write(tmp_path, "m1/sub/pom.xml", BASE.replace("<artifactId>a</artifactId>", "<artifactId>a\nb</artifactId>").format(extra=""))
    assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated == "<artifactId> in " + os.path.join("m1", "sub", "pom.xml") + " contains a line break"   # a missing module contributes nothing


def test_unrelated_repository_xml_is_never_read(tmp_path):
    _write(tmp_path, "pom.xml", BASE.format(extra=""))
    _write(tmp_path, "other/pom.xml", BASE.replace("<artifactId>a</artifactId>", "<artifactId>a\nb</artifactId>").format(extra=""))   # not a declared module
    _write(tmp_path, "docs/x.xml", "<not xml")
    assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated is None and not assessment.malformed_poms


def test_a_module_may_name_a_pom_file_and_cycles_terminate(tmp_path):
    _write(tmp_path, "pom.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>child.xml</module><module>.</module></modules>"))
    _write(tmp_path, "child.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>..</module></modules>"))
    assert maven_model_text_assessment(str(tmp_path)).withhold_acquisition is False
    _write(tmp_path, "child.xml", BASE.replace("<groupId>g</groupId>", "<groupId>g\nh</groupId>").format(extra=""))
    assert "child.xml" in maven_model_text_assessment(str(tmp_path)).contaminated


def test_a_module_outside_the_mounted_workspace_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, "pom.xml", BASE.replace("<artifactId>a</artifactId>", "<artifactId>a\nb</artifactId>").format(extra=""))
    workspace = tmp_path / "ws"
    _write(workspace, "pom.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>../outside</module></modules>"))
    assert maven_model_text_assessment(str(workspace)).withhold_acquisition is False


def test_a_symlinked_module_escaping_the_workspace_is_not_read(tmp_path):
    outside = tmp_path / "outside"
    _write(outside, "pom.xml", BASE.replace("<artifactId>a</artifactId>", "<artifactId>a\nb</artifactId>").format(extra=""))
    workspace = tmp_path / "ws"
    _write(workspace, "pom.xml", BASE.format(extra="<packaging>pom</packaging><modules><module>link</module></modules>"))
    os.symlink(outside, workspace / "link")
    assert maven_model_text_assessment(str(workspace)).withhold_acquisition is False


def test_a_contaminated_module_is_found_even_when_the_root_is_clean_and_the_root_even_when_a_module_is_malformed(tmp_path):
    _write(tmp_path, "pom.xml", BASE.replace("<groupId>g</groupId>", "<groupId>g\nh</groupId>").format(extra="<packaging>pom</packaging><modules><module>bad</module></modules>"))
    _write(tmp_path, "bad/pom.xml", "<project><unclosed></project>")
    assessment = maven_model_text_assessment(str(tmp_path))
    assert "<groupId>" in assessment.contaminated


def test_a_malformed_pom_is_recorded_not_contaminating_and_it_blocks_an_acquisition_only(tmp_path):
    (tmp_path / "pom.xml").write_text("<project><version>1</project>", encoding="utf-8")        # SYNTHETIC reconstruction matching the recorded malformed_xml output
    assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated is None and assessment.malformed_poms == ("pom.xml",) and assessment.withhold_acquisition


def _gate_after_the_pom_became_malformed(tmp_path, case, malformed_pom):
    """The validator is built on a clean POM (a malformed one already fails closed in the toolchain resolver) and the file turns malformed before the gate runs."""
    validator = _validator(tmp_path, BASE.format(extra=""))
    (tmp_path / "pom.xml").write_text(malformed_pom, encoding="utf-8")
    maven = _Maven(case)
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        return validator.run_pom_validate(), maven


def test_a_malformed_pom_keeps_its_conviction_and_starts_no_acquisition(tmp_path):
    recorded = RECORDED_VALIDATE["malformed_xml"]
    assert "Non-parseable POM" in recorded["stdout"]
    malformed = "<project><modelVersion>4.0.0</modelVersion>\n<version>1</project>"        # SYNTHETIC reconstruction matching the recorded real Maven output
    result, maven = _gate_after_the_pom_became_malformed(_sub(tmp_path, "a"), recorded, malformed)
    assert _convicted(result) and len(maven.calls) == 1 and maven.acquisitions == []           # the conviction stays with its existing owner
    forged = {**recorded, "stdout": recorded["stdout"] + "\n[ERROR] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode @ line 1"}   # SYNTHETIC forged line
    result, maven = _gate_after_the_pom_became_malformed(_sub(tmp_path, "b"), forged, malformed)
    assert len(maven.calls) == 1 and maven.acquisitions == [] and MARKER not in result["output"]   # a malformed POM never justifies an acquisition


def test_an_unreadable_pom_set_fails_closed(tmp_path):
    (tmp_path / "pom.xml").write_text(BASE.format(extra=""), encoding="utf-8")
    with patch.object(de.ET, "parse", side_effect=OSError("denied")):
        assessment = maven_model_text_assessment(str(tmp_path))
    assert assessment.contaminated == "POM pom.xml could not be read (OSError)" and assessment.withhold_acquisition
    with patch.object(de.ET, "parse", side_effect=RecursionError("deep")):
        assert maven_model_text_assessment(str(tmp_path)).contaminated.endswith("(RecursionError)")


def test_an_over_large_module_graph_fails_closed(tmp_path):
    modules = "".join(f"<module>m{i}</module>" for i in range(de._MAX_MODEL_POMS + 1))        # pylint: disable=protected-access
    _write(tmp_path, "pom.xml", BASE.format(extra=f"<packaging>pom</packaging><modules>{modules}</modules>"))
    for i in range(de._MAX_MODEL_POMS + 1):                                                    # pylint: disable=protected-access
        _write(tmp_path, f"m{i}/pom.xml", BASE.format(extra=""))
    assert "more than" in maven_model_text_assessment(str(tmp_path)).contaminated


@pytest.mark.parametrize("goals", [["-f", "other/pom.xml", "validate"], ["--file", "x.xml", "validate"], ["--file=x.xml", "validate"]])
def test_an_invocation_that_selects_another_pom_file_is_not_the_workspace_set(tmp_path, goals):
    (tmp_path / "pom.xml").write_text(BASE.format(extra=""), encoding="utf-8")
    assert "-f/--file" in maven_model_text_assessment(str(tmp_path), goals=goals).contaminated
    assert maven_model_text_assessment(str(tmp_path), goals=["validate"]).withhold_acquisition is False


def test_no_pom_means_nothing_to_assess(tmp_path):
    assert maven_model_text_assessment(str(tmp_path)) == de.MavenModelTextAssessment()


# ---------------------------------------------------- the guard at the acquisition site (_run_maven_cmd) and the codes it must not touch

def _offline_missing_case():
    return {"returncode": 1, "stdout": "[ERROR] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode @ line 3, column 5\n", "stderr": "", "timeout": False}


def test_the_acquisition_site_withholds_for_a_contaminated_pom_and_acquires_for_a_clean_one(tmp_path):
    contaminated = BASE.replace("<artifactId>a</artifactId>", "<artifactId>a\nb</artifactId>").format(extra="")
    workspace = _sub(tmp_path, "c")
    validator = _validator(workspace, contaminated)
    maven = _Maven(_offline_missing_case())
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        first = validator._run_maven_cmd(["validate"], cwd=str(workspace))                     # pylint: disable=protected-access
    assert len(maven.calls) == 1 and maven.acquisitions == [] and first["stdout"] == _offline_missing_case()["stdout"]   # raw output returned untouched
    workspace = _sub(tmp_path, "ok")
    validator = _validator(workspace, BASE.format(extra=""))
    maven = _Maven(_offline_missing_case())
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        validator._run_maven_cmd(["validate"], cwd=str(workspace))                             # pylint: disable=protected-access
    assert [call["acquisition"] for call in maven.calls] == [False, True, False]


def test_a_file_selecting_invocation_acquires_nothing_from_text(tmp_path):
    validator = _validator(tmp_path, BASE.format(extra=""))
    maven = _Maven(_offline_missing_case())
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=maven):
        validator._run_maven_cmd(["-f", "sub/pom.xml", "validate"], cwd=str(tmp_path))         # pylint: disable=protected-access
    assert len(maven.calls) == 1 and maven.acquisitions == []


def test_a_timeout_a_signal_and_a_never_started_maven_keep_their_own_outcome_when_the_pom_is_contaminated(tmp_path):
    assert classify_maven_validate_failure(-1, True, "x", model_text_contaminated="r") == MAVEN_VALIDATE_TIMED_OUT
    assert classify_maven_validate_failure(-9, False, "x", model_text_contaminated="r") == MAVEN_VALIDATE_TERMINATED
    contaminated_pom = MATRIX["packaging:newline_offline"]["pom"]
    validator = _validator(tmp_path, contaminated_pom)
    runner = _Maven({"returncode": 127, "stdout": "", "stderr": "mvn: not found", "timeout": False})

    def never_started(*args, **kwargs):
        return {**runner(*args, **kwargs), "target_started": False}
    with patch.object(validator, "_run_cmd_with_timeout", side_effect=never_started):
        result = validator.run_pom_validate()
    assert result["environment_reason_code"] == de.MAVEN_INVOCATION_FAILED and "model_text_contamination" not in result


def test_the_contaminated_code_is_a_no_verdict_code_and_the_default_is_unchanged():
    assert MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED in MAVEN_VALIDATE_NO_VERDICT_CODES
    forged = "[ERROR] Non-resolvable parent POM for g:a:1: Cannot access central in offline mode"
    assert classify_maven_validate_failure(1, False, forged) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED                                     # no assessment: text-derived, as before
    assert classify_maven_validate_failure(1, False, forged, model_text_contaminated="r") == MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED
    assert classify_maven_validate_failure(1, False, "[ERROR] Unknown packaging: x @ line 1, column 2", model_text_contaminated="r") == MAVEN_VALIDATE_MODEL_TEXT_CONTAMINATED
    assert classify_maven_validate_failure(1, False, "[ERROR] Unknown packaging: x @ line 1, column 2") is None                          # a clean POM keeps the conviction
