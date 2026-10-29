"""W2-16a (Wave-2 Step 3, compile sibling): a newline in a measured Maven echo field must not forge a located source diagnostic that convicts the candidate's source.

MEASURED at 9c7fd73 (real contained Maven 3.9.16, the compile gate's own goals, network denied; tests/fixtures/maven_compile/recorded_model_text_compile_matrix.json): a value that holds a
line break followed by `[ERROR] /kriya/workspace/src/main/java/A.java:[1,1] cannot find symbol` in `packaging`, a `<module>`, the project `artifactId` / `groupId` or a dependency's `groupId` /
`artifactId` / `scope` leaves that line in Maven's output as a line of its own; `classify_compile_failure` then returned None - the candidate SOURCE was convicted from text the candidate wrote.
The benign value of the same field gives `MAVEN_COMPILE_NO_DIAGNOSTICS`.

The Maven compile call site now hands the classifier the SAME `maven_model_text_assessment` that guards the POM-validate gate and the acquisition decision (a contaminated POM set, or a POM that is
not well-formed XML, means Maven's text cannot be attributed): no source authority is drawn from it - the typed `MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED`, never a Developer repair, never a PASS. A
clean POM keeps the Slice-F rule (a positively located compiler diagnostic convicts the source). A timeout / signal / invocation failure keeps its own code.

SYNTHETIC pairings are labelled: the genuine located-error recording (real Maven output, tests/fixtures/maven_compile/recorded_maven_compile.json) is paired with POMs written here.
"""
import json
from pathlib import Path

import pytest
import test_gate_orchestrator as tg
from test_slice_f_gate_path import LOCATED, _engine, _Scripted

from kriya.capabilities.maven import MavenBuildAdapter
from kriya.config.config import AutonomyConfig
from kriya.tools.dependency_execution import (
    COMPILE_NO_VERDICT_CODES,
    MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED,
    MAVEN_COMPILE_NO_DIAGNOSTICS,
    MAVEN_COMPILE_TERMINATED,
    MAVEN_COMPILE_TIMED_OUT,
    classify_compile_failure,
    maven_model_text_assessment,
)
from kriya.tools.validate import PolymorphicValidator

FIXTURES = Path(__file__).resolve().parent / "fixtures"
MATRIX = json.loads((FIXTURES / "maven_compile" / "recorded_model_text_compile_matrix.json").read_text(encoding="utf-8"))["cases"]
GENUINE_RECORDED = json.loads((FIXTURES / "maven_compile" / "recorded_maven_compile.json").read_text(encoding="utf-8"))["contained_compile_error_located"]
FIELDS = ["packaging", "module", "artifactId", "groupId", "dependency.groupId", "dependency.artifactId", "dependency.scope"]
FORGED = "[ERROR] /kriya/workspace/src/main/java/A.java:[1,1] cannot find symbol"
CLEAN_POM = '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>g</groupId><artifactId>a</artifactId><version>1</version></project>'
GENUINE_CASE = {"returncode": GENUINE_RECORDED["returncode"], "stdout": GENUINE_RECORDED["output"], "stderr": "", "timeout": False, "java": "public class A { int x = ; }\n"}


def _adapter_result(tmp_path, case, pom, *, late_pom=None):
    """The production compile call site: a real validator and the real Maven adapter, only the mvn process seam answers (with recorded output)."""
    (tmp_path / "pom.xml").write_text(pom if late_pom is None else CLEAN_POM, encoding="utf-8")        # a malformed POM already fails closed in the toolchain resolver at construction
    (tmp_path / "src" / "main" / "java").mkdir(parents=True)
    (tmp_path / "src" / "main" / "java" / "A.java").write_text(case.get("java", "public class A { int x = 1; }\n"), encoding="utf-8")
    validator = PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig(contained_execution_required=True))
    if late_pom is not None:
        (tmp_path / "pom.xml").write_text(late_pom, encoding="utf-8")
    calls = []

    def mvn(goals, cwd, *args, **kwargs):  # pylint: disable=unused-argument
        calls.append(list(goals))
        return {"returncode": case["returncode"], "stdout": case["stdout"], "stderr": case["stderr"], "timeout": case["timeout"]}
    validator._run_maven_cmd = mvn                                                                      # pylint: disable=protected-access
    return MavenBuildAdapter().compile(validator, ["src/main/java/A.java"], deadline=None), calls


def _source_conviction(result):
    return result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven compilation failed:")


# --------------------------------------------------------------------------------------------------------------------------------------------- the fixture

def test_the_matrix_is_real_maven_and_carries_the_forged_located_line_where_reachable():
    assert all(case["returncode"] == 1 and not case["timeout"] for case in MATRIX.values())
    for field in FIELDS:
        assert FORGED in MATRIX[f"{field}:forged_located"]["stdout"], field                         # MEASURED reachable: Maven printed the candidate's line as a line of its own
        assert FORGED not in MATRIX[f"{field}:benign"]["stdout"], field
    assert "Compilation failure" not in MATRIX["packaging:benign"]["stdout"] and "Unknown packaging" in MATRIX["packaging:benign"]["stdout"]


@pytest.mark.parametrize("field", FIELDS)
def test_the_classifier_alone_is_text_derived_and_convicts_the_forged_line(field):
    """Why the call-site assessment is needed: on the text alone the forged located line is a source conviction (None) and the benign variant is not."""
    forged, benign = MATRIX[f"{field}:forged_located"], MATRIX[f"{field}:benign"]
    assert classify_compile_failure("maven", 1, False, f"{forged['stdout']}\n{forged['stderr']}") is None
    assert classify_compile_failure("maven", 1, False, f"{benign['stdout']}\n{benign['stderr']}") == MAVEN_COMPILE_NO_DIAGNOSTICS


# ------------------------------------------------------------------------------------------------------------ the call site, end to end (real output, real POM)

@pytest.mark.parametrize("field", FIELDS)
def test_a_forged_located_line_in_a_measured_field_is_no_verdict_never_a_source_conviction(tmp_path, field):
    case = MATRIX[f"{field}:forged_located"]
    result, calls = _adapter_result(tmp_path, case, case["pom"])
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED
    assert not _source_conviction(result) and len(calls) == 1
    assert MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED in COMPILE_NO_VERDICT_CODES
    assert case["stdout"] in result["output"]                                                       # raw evidence preserved exactly
    assert f"<{field}>" in result["output"]                                                         # and the reason names the field


@pytest.mark.parametrize("field", FIELDS)
def test_the_benign_value_keeps_the_existing_no_diagnostics_behaviour(tmp_path, field):
    case = MATRIX[f"{field}:benign"]
    result, _ = _adapter_result(tmp_path, case, case["pom"])
    assert result["environment_reason_code"] == MAVEN_COMPILE_NO_DIAGNOSTICS and not _source_conviction(result)


@pytest.mark.parametrize("variant", ["benign", "forged_located"])
def test_model_version_is_owned_by_the_abort_rule_in_the_compile_classifier(tmp_path, variant):
    case = MATRIX[f"modelVersion:{variant}"]
    result, _ = _adapter_result(tmp_path, case, case["pom"])
    assert result["environment_reason_code"] == MAVEN_COMPILE_NO_DIAGNOSTICS and not _source_conviction(result)


# -------------------------------------------------------------------------------------------------------------------------------------------- genuine controls

def test_a_clean_pom_with_a_genuine_located_compiler_error_is_still_a_source_failure(tmp_path):
    """The real recorded contained Maven output with a real located javac error, paired with a clean POM (SYNTHETIC pairing)."""
    result, _ = _adapter_result(tmp_path, GENUINE_CASE, CLEAN_POM)
    assert _source_conviction(result)


FREE_TEXT = ("<description>first line\nsecond line</description><name>a\nb</name><properties><note>x\ny</note></properties>"
             "<build><plugins><plugin><groupId>org.x</groupId><artifactId>p</artifactId><configuration><arg>a\nb</arg></configuration></plugin></plugins></build>")


def test_a_broad_rule_would_suppress_this_a_clean_pom_with_multiline_free_text_still_convicts_a_real_source_error(tmp_path):
    pom = CLEAN_POM.replace("</project>", FREE_TEXT + "</project>")
    result, _ = _adapter_result(tmp_path, GENUINE_CASE, pom)
    assert maven_model_text_assessment(str(tmp_path)).text_untrustworthy is None                    # the multi-line description / name / properties / plugin configuration are not contamination
    assert _source_conviction(result)


def test_a_contaminated_pom_with_a_real_compiler_error_is_no_verdict_conservatively(tmp_path):
    """SYNTHETIC pairing: a padded `groupId` (a trailing line break Maven trims) with a real located javac error. The text cannot tell the real diagnostic from a forged one, so no conviction is
    guessed; the owner's policy accepts NO_VERDICT here."""
    pom = CLEAN_POM.replace("<groupId>g</groupId>", "<groupId>g\n</groupId>")
    result, _ = _adapter_result(tmp_path, GENUINE_CASE, pom)
    assert result["environment_reason_code"] == MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED and not _source_conviction(result)


def test_a_malformed_pom_draws_no_source_authority_from_maven_text(tmp_path):
    """SYNTHETIC: the POM is not well-formed XML (the XML defect stays with its existing owner), so a located diagnostic in Maven's output cannot be Maven's."""
    result, _ = _adapter_result(tmp_path, GENUINE_CASE, CLEAN_POM, late_pom="<project><version>1</project>")
    assert result["environment_reason_code"] == MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED and "not well-formed XML" in result["output"]


def test_an_external_module_reference_that_exists_outside_the_workspace_is_no_verdict(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "pom.xml").write_text(CLEAN_POM, encoding="utf-8")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    pom = CLEAN_POM.replace("</project>", "<packaging>pom</packaging><modules><module>../outside</module></modules></project>")
    result, _ = _adapter_result(workspace, GENUINE_CASE, pom)
    assert result["environment_reason_code"] == MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED and "outside the workspace" in result["output"]


def test_independent_process_facts_keep_their_own_codes_when_the_text_is_untrustworthy():
    assert classify_compile_failure("maven", -1, True, FORGED, model_text_untrustworthy="r") == MAVEN_COMPILE_TIMED_OUT
    assert classify_compile_failure("maven", -9, False, FORGED, model_text_untrustworthy="r") == MAVEN_COMPILE_TERMINATED
    assert classify_compile_failure("maven", 1, False, FORGED, model_text_untrustworthy="r") == MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED
    assert classify_compile_failure("maven", 1, False, FORGED) is None                              # no assessment: the Slice-F rule, unchanged
    gradle = "/w/A.java:3: error: cannot find symbol"
    assert classify_compile_failure("gradle", 1, False, gradle, model_text_untrustworthy="r") is None   # Maven POM text says nothing about a Gradle build


# ------------------------------------------------------------------------------------------------------------------------ the Developer path at the real gate

def _contaminate_the_candidate_pom(monkeypatch):
    """In this scenario the Developer's one answer IS the project POM (POM_NAMED); it now carries a line break in `packaging`, so the compile gate builds a contaminated POM set."""
    monkeypatch.setattr(tg, "POM_NAMED", tg.POM_NAMED.replace("</project>", "  <packaging>jar\nzzz</packaging>\n</project>"))


@pytest.mark.parametrize("autonomous", [True, False], ids=["immediate", "deferred"])
def test_a_forged_located_diagnostic_never_enters_developer_repair(tmp_path, monkeypatch, autonomous):
    _contaminate_the_candidate_pom(monkeypatch)
    run, legacy = _engine(tmp_path / "forged", monkeypatch, _Scripted(LOCATED, LOCATED, LOCATED), autonomous=autonomous)   # the located line a contaminated Maven text could carry
    assert legacy["quality_gates_passed"] is False
    assert legacy["failure_category"] == "environment_failure"
    assert legacy["environment_failure"].startswith("VERIFICATION_INFRASTRUCTURE_FAILURE") and MAVEN_COMPILE_MODEL_TEXT_CONTAMINATED in legacy["environment_failure"]
    assert legacy["last_failure"]["type"] == "verification_infrastructure_failure"
    assert run["developer_calls"] == 1                                                              # the Developer was not asked to repair source
    assert [call["goals"][0] for call in run["maven"]].count("clean") == 1                          # no retry attempt was spent


def test_a_genuine_located_diagnostic_on_a_clean_pom_still_enters_developer_repair(tmp_path, monkeypatch):
    run, legacy = _engine(tmp_path / "clean", monkeypatch, _Scripted(LOCATED, None))
    assert run["developer_calls"] > 1 and [call["goals"][0] for call in run["maven"]].count("clean") == 2
    assert legacy["quality_gates_passed"] is True and legacy.get("environment_failure") in (None, "")
