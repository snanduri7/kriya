"""F7-A (Wave-1 final fix-forward) and F7-E: Maven's refusal of an OPERATOR document is not a verdict on the candidate POM, and a transfer failure stays an environment outcome
whatever its cause is called.

MEASURED at 5974544 with real Maven 3.9.16: a malformed ``~/.m2/settings.xml`` (or a global settings / toolchains file) prints
``[FATAL] Non-parseable settings <file>: Duplicated tag ... @ <file>, line 2, column 54``. ``Duplicated tag`` and ``@ ..., line N, column M`` are also how Maven reports a located
POM-model problem, so ``classify_maven_validate_failure`` returned None (a conviction) and the gate offered the candidate's POM to the Developer for repair. The recorded outputs are in
tests/fixtures/maven_validate/recorded_operator_config.json (``pom_duplicated_tag`` is the genuine POM-defect control with the same shape).
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools.dependency_execution import (
    MAVEN_VALIDATE_NO_VERDICT_CODES,
    MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID,
    MAVEN_VALIDATE_RESOLUTION_UNVERIFIED,
    classify_maven_validate_failure,
)
from kriya.tools.validate import PolymorphicValidator

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "maven_validate"
OPERATOR = json.loads((FIXTURES / "recorded_operator_config.json").read_text(encoding="utf-8"))
RECORDED = json.loads((FIXTURES / "recorded_mvn_validate.json").read_text(encoding="utf-8"))
POM = '<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion></project>\n'


def _validate(tmp_path, recorded):
    (tmp_path / "pom.xml").write_text(POM)
    maven = {"returncode": recorded["returncode"], "stdout": recorded["stdout"], "stderr": recorded["stderr"]}
    with patch.object(PolymorphicValidator, "_run_maven_cmd", return_value=maven):
        return PolymorphicValidator(str(tmp_path), autonomy_cfg=AutonomyConfig()).run_pom_validate()


def _combined(recorded):
    return f"{recorded['stdout']}\n{recorded['stderr']}"


# --------------------------------------------------------------------------------------------------------------- F7-A: operator documents

OPERATOR_FAILURES = ["settings_duplicated_tag", "settings_not_xml", "global_settings_duplicated_tag", "toolchains_duplicated_tag"]


@pytest.mark.parametrize("name", OPERATOR_FAILURES)
def test_a_malformed_operator_document_is_an_environment_outcome_never_a_pom_conviction(tmp_path, name):
    """REAL Maven output. The POM was never judged: the typed environment code is returned, the captured diagnostics are preserved and nothing is offered for repair."""
    recorded = OPERATOR[name]
    result = _validate(tmp_path, recorded)
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID, result
    assert MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID in MAVEN_VALIDATE_NO_VERDICT_CODES
    assert not result["output"].startswith("Maven POM validation failed:")
    for captured in (recorded["stdout"].strip(), recorded["stderr"].strip()):
        assert captured in result["output"], "the captured diagnostics are preserved"


def test_the_recorded_operator_failures_do_share_the_located_problem_shape_of_a_pom_defect():
    """The reason the attribution needs its own rule: without it the POM-model patterns match these outputs (this is what convicted the candidate at 5974544)."""
    from kriya.tools import dependency_execution as owner

    for name in ("settings_duplicated_tag", "global_settings_duplicated_tag", "toolchains_duplicated_tag"):    # (a non-XML settings file prints no located-problem phrase)
        assert owner._MAVEN_POM_MODEL_PROBLEM_RE.search(_combined(OPERATOR[name])), name  # pylint: disable=protected-access


@pytest.mark.parametrize("name", ["pom_duplicated_tag"])
def test_the_same_located_problem_shape_in_the_candidate_pom_still_convicts_it(tmp_path, name):
    """REAL Maven output for a pom.xml with a duplicated <version>: `Non-parseable POM <pom.xml>: Duplicated tag ... @ line 1, column 161` is the candidate's defect."""
    result = _validate(tmp_path, OPERATOR[name])
    assert result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven POM validation failed:")


@pytest.mark.parametrize("name", ["wrong_root_element", "malformed_xml", "missing_dependency_version"])
def test_every_recorded_genuine_pom_defect_still_convicts(tmp_path, name):
    assert classify_maven_validate_failure(RECORDED[name]["returncode"], False, _combined(RECORDED[name])) is None


def test_a_valid_pom_with_valid_operator_settings_passes(tmp_path):
    """REAL output of a Maven that read a well-formed ~/.m2/settings.xml: exit 0 is the success."""
    assert _validate(tmp_path, OPERATOR["valid_user_settings"])["success"] is True


def test_an_operator_document_failure_outranks_a_pom_message_printed_next_to_it():
    """Maven stops at the operator document, but if anything model-shaped follows it, the verdict is still untrustworthy (the cascade rule, as for an unresolved parent)."""
    text = _combined(OPERATOR["settings_duplicated_tag"]) + "\n[ERROR] Malformed POM pom.xml: Unrecognised tag: 'x' @ line 3, column 4"
    assert classify_maven_validate_failure(1, False, text) == MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID


@pytest.mark.parametrize("name", ["pom_defect_with_phrase_in_comment", "pom_defect_with_phrase_in_url", "pom_defect_in_module_named_like_the_phrase"])
def test_a_genuine_pom_defect_whose_echoed_context_carries_the_operator_phrase_still_convicts(tmp_path, name):
    """Delta-review F-1. REAL Maven output for a duplicated tag after `<!-- while building the effective settings -->`, after a `<url>` full of `Non-parseable settings`, and for a
    malformed module POM in a directory NAMED `Non-parseable settings`: Maven echoes those candidate bytes / paths into its log, and the phrase must only count at the start of a log line."""
    recorded = OPERATOR[name]
    assert "Non-parseable settings" in _combined(recorded) or "effective settings" in _combined(recorded), "the control really carries the phrase"
    result = _validate(tmp_path, recorded)
    assert result["success"] is False and "environment_reason_code" not in result and result["output"].startswith("Maven POM validation failed:"), result


@pytest.mark.parametrize("text", ["[ERROR] a POM comment says [FATAL] Non-parseable settings and then the real defect",
                                  "[ERROR] the project description is [ERROR] 1 problem was encountered while building the effective settings",
                                  "Non-parseable settings at the start of a line with no Maven prefix",
                                  "while building the effective settings, with neither anchor nor prefix"],
                         ids=["phrase-mid-line-after-a-prefix", "count-phrase-mid-line", "bare-phrase-at-line-start", "bare-fragment"])
def test_the_operator_phrase_counts_only_as_a_maven_log_line_that_starts_with_its_level(text):
    """Delta re-review T-1: the anchor (`^`) and the required `[ERROR]` / `[FATAL]` prefix are each pinned by a case only they reject (real mvn echoes candidate bytes mid-line)."""
    assert classify_maven_validate_failure(1, False, text) != MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID


@pytest.mark.parametrize("text", ["[FATAL] Non-parseable settings /h/.m2/settings.xml: Duplicated tag: 'a' @ /h/.m2/settings.xml, line 2, column 3",
                                  "[FATAL] Non-parseable toolchains /h/tc.xml: Duplicated tag: 'type' @ line 1, column 46",
                                  "[FATAL] Non-readable settings /h/.m2/settings.xml: no more data available",
                                  "[FATAL] Non-readable toolchains /h/tc.xml: no more data available",
                                  "[ERROR] 1 problem was encountered while building the effective settings",
                                  "[ERROR] 2 problems were encountered while building the effective toolchains"],
                         ids=["parseable-settings", "parseable-toolchains", "readable-settings", "readable-toolchains", "effective-settings", "effective-toolchains"])
def test_each_operator_document_phrase_is_recognised_on_its_own(text):
    """Every alternative of the rule, alone (the real outputs carry two of them each, so a dropped alternative would otherwise go unseen)."""
    assert classify_maven_validate_failure(1, False, text) == MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID


@pytest.mark.parametrize("text", ["[ERROR] Non-parseable POM pom.xml: Duplicated tag: 'a' @ line 1, column 2",
                                  "[ERROR] Non-parseable settingsfoo",
                                  "[ERROR] the effective pom"],
                         ids=["pom-duplicated-tag", "word-boundary", "effective-pom-is-not-an-operator-document"])
def test_the_operator_rule_does_not_reach_pom_diagnostics(text):
    assert classify_maven_validate_failure(1, False, text) != MAVEN_VALIDATE_OPERATOR_CONFIG_INVALID


# --------------------------------------------------------------------------------------------------------------- F7-E: transfer failures


def test_a_real_transfer_failure_with_a_cause_no_pattern_lists_is_resolution_unverified(tmp_path):
    """REAL online Maven against a server that closes the body short: `Could not transfer artifact ... Premature end of Content-Length delimited message body`."""
    recorded = OPERATOR["transfer_cut_short"]
    assert "Premature end of Content-Length" in _combined(recorded)
    result = _validate(tmp_path, recorded)
    assert result["success"] is False and result["environment_reason_code"] == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED


@pytest.mark.parametrize("cause", ["Remote host terminated the handshake", "Premature end of Content-Length delimited message body", "peer not authenticated"])
def test_could_not_transfer_alone_is_an_environment_outcome_whatever_the_cause(cause):
    """`Could not transfer` is itself the marker: no other listed transport phrase is present, so only that alternative can classify it (the F7_14 survivor)."""
    text = f"[ERROR] Could not transfer artifact g:p:pom:1 from/to central (https://repo.example/maven2): {cause}"
    assert classify_maven_validate_failure(1, False, text) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED


def test_a_pom_model_message_next_to_a_transfer_failure_is_a_cascade_not_a_verdict():
    text = ("[ERROR] Malformed POM pom.xml: Unrecognised tag: 'x' @ line 3, column 4\n"
            "[ERROR] Could not transfer artifact g:p:pom:1 from/to central (https://repo.example/maven2): Remote host terminated the handshake")
    assert classify_maven_validate_failure(1, False, text) == MAVEN_VALIDATE_RESOLUTION_UNVERIFIED


def test_a_pom_model_message_without_any_transfer_evidence_is_the_candidate_defect():
    assert classify_maven_validate_failure(1, False, "[ERROR] Malformed POM pom.xml: Unrecognised tag: 'x' @ line 3, column 4") is None
