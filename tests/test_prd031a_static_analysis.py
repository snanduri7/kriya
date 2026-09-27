"""PRD-031A: the provider-neutral static-analysis gate (deterministic tier).

Driven by tests/_fake_static_analysis.py's FakeProvider, which scans the
real snapshot files the service builds, so PRE/POST, coverage, scope,
waivers, policy, evidence identity and the commit guard are exercised end
to end without a real scanner. The Semgrep adapter has its own suites
(tests/test_prd031a_semgrep_adapter.py, tests/test_prd031a_semgrep_live.py).
"""

from __future__ import annotations

import ast
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS, FAKE, FakeKnobs, FakeRegistration
from pydantic import ValidationError

from kriya.config.authority import FieldClassification, classify_field
from kriya.config.config import AppConfig, runtime_profile_preset_fields
from kriya.static_analysis import model
from kriya.static_analysis.model import (
    ACCEPTED_RISK_BANNER,
    GATE_EVENT_STATUS,
    LanguageSupport,
    Outcome,
    ScanScope,
    ScanStatus,
    TargetStatus,
    canonical_digest,
)
from kriya.static_analysis.service import (
    StaticAnalysisCandidate,
    StaticAnalysisRequest,
    StaticAnalysisService,
    banner,
    commit_guard,
    static_analysis_result_fields,
)
from kriya.static_analysis.waivers import (
    load_waivers,
    new_waiver,
    revoke_waiver,
    waiver_store_path,
    write_waiver,
)
from kriya.workflow.edit_safety import StagedFileWrite, content_revision
from kriya.workflow.terminal_commit import commit_terminal_candidate

REPO = Path(__file__).resolve().parent.parent
KRIYA = REPO / "kriya"


@pytest.fixture(autouse=True)
def _waiver_home(tmp_path, monkeypatch):
    monkeypatch.setenv("KRIYA_STATIC_ANALYSIS_HOME", str(tmp_path / "waiver-home"))


def _config(**static):
    autonomy = static.pop("autonomy", None)
    runtime = static.pop("runtime", None)
    data = {"static_analysis": {"enabled": True, "provider": FAKE, **static}}
    if autonomy:
        data["autonomy"] = autonomy
    if runtime:
        data.update(runtime)
    return AppConfig(**data)


def _workspace(tmp_path, files):
    root = tmp_path / "ws"
    root.mkdir(exist_ok=True)
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return str(root)


def _write(workspace, rel, new, *, base=None):
    target = os.path.join(workspace, rel)
    if new is None:
        return StagedFileWrite(target_path=target, content="", base_path=target,
                               expected_base_revision=content_revision(base or ""), delete=True,
                               expected_base_exists=True)
    return StagedFileWrite(target_path=target, content=new, base_path=target,
                           expected_base_revision=content_revision(base or ""),
                           expected_base_exists=base is not None, content_bytes=new.encode())


def _evaluate(cfg, workspace, writes, tmp_path, **request):
    service = StaticAnalysisService(cfg, evidence_root=str(tmp_path / "evidence"))
    return service.evaluate(StaticAnalysisRequest(
        writes=writes, workspace_path=workspace, run_id="run-1", unit_id="u1", **request,
    ))


BASE_A = "class A {\n  int x;\n}\n"


# --- Disabled / configuration ----------------------------------------------

def test_disabled_by_default_is_never_pass_and_never_touches_a_provider(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration() as fake:
        result = _evaluate(AppConfig(), workspace, [_write(workspace, "A.java", "x BAD_CRITICAL\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.DISABLED and result.permits_commit
    assert result.reason_codes == (model.STATIC_ANALYSIS_NOT_CONFIGURED,)
    assert result.gate_status == "disabled" and GATE_EVENT_STATUS[Outcome.DISABLED] != "passed"
    assert fake.instances == []
    assert static_analysis_result_fields(result)["static_analysis"]["outcome"] == "DISABLED"
    assert static_analysis_result_fields(result)["accepted_risk"] is False


def test_operator_disabled_is_distinguished_from_not_configured(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    result = _evaluate(AppConfig(static_analysis={"enabled": False}), workspace, [], tmp_path)
    assert result.reason_codes == (model.STATIC_ANALYSIS_DISABLED,)
    assert result.evidence["configured_by"] == "operator"


def test_required_but_disabled_is_rejected_at_config_load():
    with pytest.raises(ValidationError, match="requirement is 'required' but static_analysis.enabled is false"):
        AppConfig(static_analysis={"requirement": "required"})


def test_enabled_needs_a_registered_provider():
    with pytest.raises(ValidationError, match="requires static_analysis.provider"):
        AppConfig(static_analysis={"enabled": True})
    with pytest.raises(ValidationError, match="not a registered provider"):
        AppConfig(static_analysis={"enabled": True, "provider": "nope"})


def test_every_static_analysis_field_is_security_authority():
    for leaf in ("enabled", "provider", "requirement", "policy", "waivers", "providers", "exclusions", "scope"):
        assert classify_field("static_analysis", leaf) is FieldClassification.SECURITY_AUTHORITY


def _production_config(static):
    data = {"runtime_profile": "production"}
    for (top, leaf), value in runtime_profile_preset_fields("production").items():
        data.setdefault(top, {})[leaf] = value
    data["static_analysis"] = static
    return data


def test_production_does_not_force_enabled_but_seals_trustworthy_evidence_when_enabled():
    with FakeRegistration():
        assert AppConfig(**_production_config({"enabled": False})).static_analysis.enabled is False
        with pytest.raises(ValidationError, match="static_analysis.requirement must be 'required'"):
            AppConfig(**_production_config({"enabled": True, "provider": FAKE}))
        with pytest.raises(ValidationError, match="policy.analysis_errors must be 'block'"):
            AppConfig(**_production_config({"enabled": True, "provider": FAKE, "requirement": "required",
                                            "policy": {"analysis_errors": "warn"}}))
        sealed = AppConfig(**_production_config({"enabled": True, "provider": FAKE, "requirement": "required"}))
    assert sealed.static_analysis.requirement == "required"


# --- Provider availability, egress, scope ----------------------------------

@pytest.mark.parametrize("requirement,when_unavailable,permits", [
    ("optional", "warn", True), ("optional", "block", False), ("required", "warn", False),
])
def test_provider_unavailable_is_never_pass(tmp_path, requirement, when_unavailable, permits):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(probe_failure=model.PROVIDER_VERSION_MISMATCH)) as fake:
        cfg = _config(requirement=requirement, policy={"when_unavailable": when_unavailable})
        result = _evaluate(cfg, workspace, [_write(workspace, "A.java", "class A {}\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.UNAVAILABLE and result.permits_commit is permits
    assert result.reason_codes == (model.PROVIDER_VERSION_MISMATCH,)
    assert fake.scans == []


@pytest.mark.parametrize("knobs", [FakeKnobs(network_requirement="service"), FakeKnobs(source_upload=True)])
def test_local_only_refuses_network_or_source_upload_before_any_scan(tmp_path, knobs):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(knobs) as fake:
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.UNAVAILABLE and result.reason_codes == (model.EGRESS_NOT_PERMITTED,)
    assert fake.scans == [] and result.evidence["egress"]["admitted"] is False


def test_source_upload_is_refused_even_outside_local_only(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(source_upload=True)):
        result = _evaluate(_config(autonomy={"egress_policy": "unrestricted"}), workspace, [], tmp_path)
    assert result.reason_codes == (model.EGRESS_NOT_PERMITTED,)


def test_scope_narrower_than_the_provider_minimum_is_unavailable(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(cross_file=True, minimum_scope=ScanScope.MODULE)) as fake:
        result = _evaluate(_config(scope="changed_files"), workspace,
                           [_write(workspace, "A.java", "class A {}\n", base=BASE_A)], tmp_path)
    assert result.reason_codes == (model.SCOPE_BELOW_PROVIDER_MINIMUM,) and fake.scans == []


def test_auto_scope_is_the_provider_minimum_and_scans_the_whole_module(tmp_path):
    workspace = _workspace(tmp_path, {"pom.xml": "<p/>", "src/A.java": BASE_A, "src/B.java": "class B {}\n"})
    with FakeRegistration(FakeKnobs(cross_file=True, minimum_scope=ScanScope.MODULE)) as fake:
        result = _evaluate(_config(), workspace, [_write(workspace, "src/A.java", "class A { }\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.PASS
    assert result.evidence["scope"]["kind"] == "module"
    assert {tuple(sorted(s.targets)) for s in fake.scans} == {("src/A.java", "src/B.java")}


# --- Coverage ------------------------------------------------------------------

@pytest.mark.parametrize("action,outcome", [("warn", Outcome.PASS_WITH_WARNINGS), ("block", Outcome.BLOCKED)])
def test_java_supported_cpp_unsupported_is_partial_and_cpp_is_never_analyzed(tmp_path, action, outcome):
    workspace = _workspace(tmp_path, {"A.java": BASE_A, "n.cpp": "int f();\n"})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A),
              _write(workspace, "n.cpp", "int g();\n", base="int f();\n")]
    with FakeRegistration() as fake:
        result = _evaluate(_config(policy={"partial_coverage": action}), workspace, writes, tmp_path)
    assert result.outcome is outcome and model.COVERAGE_PARTIAL in result.reason_codes
    coverage = result.evidence["coverage"]
    assert coverage["status"] == "PARTIAL"
    assert {"path": "n.cpp", "status": "unsupported_language",
            "detail": "provider support for cpp: unsupported"} in coverage["uncovered"]
    assert all("n.cpp" not in s.targets for s in fake.scans)


def test_supported_language_without_rules_is_uncovered(tmp_path):
    workspace = _workspace(tmp_path, {"a.py": "x = 1\n"})
    knobs = FakeKnobs(languages={"python": LanguageSupport("ga", 0)})
    with FakeRegistration(knobs) as fake:
        result = _evaluate(_config(), workspace, [_write(workspace, "a.py", "x = 2\n", base="x = 1\n")], tmp_path)
    assert result.evidence["coverage"]["status"] == "UNSUPPORTED"
    assert result.evidence["coverage"]["uncovered"][0]["status"] == "no_rules"
    assert result.outcome is Outcome.PASS_WITH_WARNINGS and fake.scans == []


def test_missing_prerequisite_blocks_before_any_scan(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    knobs = FakeKnobs(prerequisites={"java": ("compiled_classes",)}, unmet_prerequisites=("compiled_classes",))
    with FakeRegistration(knobs) as fake:
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED and model.PREREQUISITES_MISSING in result.reason_codes
    assert fake.scans == [] and not result.permits_commit


@pytest.mark.parametrize("action,outcome", [("block", Outcome.UNKNOWN), ("warn", Outcome.PASS_WITH_WARNINGS)])
def test_a_target_the_scanner_did_not_confirm_is_never_pass(tmp_path, action, outcome):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(unconfirmed=("A.java",))):
        result = _evaluate(_config(policy={"analysis_errors": action}), workspace,
                           [_write(workspace, "A.java", "class A { }\n", base=BASE_A)], tmp_path)
    assert result.outcome is outcome and model.SCAN_INCOMPLETE in result.reason_codes
    assert result.evidence["coverage"]["uncovered"][0]["status"] == "not_analyzed"


@pytest.mark.parametrize("action,outcome", [("block", Outcome.UNKNOWN), ("warn", Outcome.PASS_WITH_WARNINGS)])
def test_oversized_target_is_recorded_never_submitted_and_never_pass(tmp_path, action, outcome):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    big = "class A {}\n" + "// padding\n" * 20
    with FakeRegistration() as fake:
        result = _evaluate(_config(max_target_bytes=64, policy={"analysis_errors": action}), workspace,
                           [_write(workspace, "A.java", big, base=BASE_A)], tmp_path)
    assert result.outcome is outcome and model.TARGET_OVERSIZED in result.reason_codes
    assert result.evidence["scope"]["oversized"] == [
        {"path": "A.java", "side": "post", "size": len(big), "limit": 64,
         "reason": "exceeds static_analysis.max_target_bytes"},
    ]
    assert all("A.java" not in s.targets for s in fake.scans if s.scan_id == "post")


def test_operator_exclusions_and_scanner_control_files_never_reach_the_provider(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A, "gen/G.java": "class G {}\n"})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A),
              _write(workspace, "gen/G.java", "x BAD_CRITICAL\n", base="class G {}\n"),
              _write(workspace, ".fakeignore", "A.java\n")]
    with FakeRegistration() as fake:
        result = _evaluate(_config(exclusions=["gen"]), workspace, writes, tmp_path)
    assert result.outcome is Outcome.PASS
    assert result.evidence["scope"]["excluded"] == [".fakeignore", "gen/G.java"]
    for provider in fake.instances:
        for tree in provider.trees.values():
            assert set(tree) == {"A.java"}


# --- PRE/POST classification -----------------------------------------------------

def test_candidate_introduced_finding_blocks_and_the_gap_carries_no_scanner_text(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED and not result.permits_commit
    assert result.reason_codes == (model.NEW_FINDING_BLOCKED,)
    [finding] = result.evidence["findings"]
    assert finding["classification"] == "introduced" and finding["decision"] == "block"
    assert finding["pre_state"] == "absent" and finding["post_state"] == "present"
    assert result.gap.startswith("STATIC_ANALYSIS_BLOCKED: ") and "fake:bad-high at A.java" in result.gap
    assert "SCANNER-TEXT" not in result.gap and "SCANNER-TEXT" not in banner(result)


def test_pre_existing_finding_survives_a_line_shift_as_unchanged(tmp_path):
    base = "class A {\n  x BAD_HIGH\n}\n"
    shifted = "class A {\n" + "  // new line\n" * 20 + "      x   BAD_HIGH\n}\n"
    workspace = _workspace(tmp_path, {"A.java": base})
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", shifted, base=base)], tmp_path)
    [finding] = result.evidence["findings"]
    assert finding["classification"] == "unchanged" and finding["decision"] == "warn"
    assert result.outcome is Outcome.PASS_WITH_WARNINGS


def test_resolved_finding_is_credit_not_a_block(tmp_path):
    base = "x BAD_CRITICAL\n"
    workspace = _workspace(tmp_path, {"A.java": base})
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "class A {}\n", base=base)], tmp_path)
    assert result.outcome is Outcome.PASS
    assert result.evidence["summary"]["resolved"] == 1
    assert result.evidence["findings"][0]["post_state"] == "absent"


def test_worsened_is_extra_occurrences_at_the_same_location(tmp_path):
    base = "x BAD_HIGH\n"
    workspace = _workspace(tmp_path, {"A.java": base})
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", base * 3, base=base)], tmp_path)
    summary = result.evidence["summary"]
    assert (summary["unchanged"], summary["worsened"]) == (1, 2)
    assert result.outcome is Outcome.BLOCKED and model.WORSENED_FINDING_BLOCKED in result.reason_codes


def test_added_file_is_post_only_and_deleted_file_is_pre_only(tmp_path):
    workspace = _workspace(tmp_path, {"Old.java": "x BAD_HIGH\n", "Keep.java": "class K {}\n"})
    writes = [_write(workspace, "Old.java", None, base="x BAD_HIGH\n"),
              _write(workspace, "New.java", "class N {}\n")]
    with FakeRegistration(FakeKnobs(minimum_scope=ScanScope.MODULE, cross_file=True)) as fake:
        result = _evaluate(_config(), workspace, writes, tmp_path)
    trees = fake.instances[0].trees
    assert set(trees["pre"]) == {"Old.java", "Keep.java"} and set(trees["post"]) == {"New.java", "Keep.java"}
    assert trees["pre"]["Keep.java"] == trees["post"]["Keep.java"]
    assert result.outcome is Outcome.PASS and result.evidence["summary"]["resolved"] == 1


def test_a_candidate_can_cause_a_finding_in_a_file_it_did_not_change(tmp_path):
    workspace = _workspace(tmp_path, {"pom.xml": "<p/>", "Caller.java": "CALLS_TAINTED\n", "Source.java": "class S {}\n"})
    with FakeRegistration(FakeKnobs(cross_file=True, minimum_scope=ScanScope.MODULE)):
        result = _evaluate(_config(), workspace,
                           [_write(workspace, "Source.java", "TAINT_SOURCE\n", base="class S {}\n")], tmp_path)
    [finding] = result.evidence["findings"]
    assert (finding["path"], finding["classification"]) == ("Caller.java", "introduced")
    assert result.outcome is Outcome.BLOCKED


@pytest.mark.parametrize("post_text,outcome", [("x BAD_HIGH\n", Outcome.UNKNOWN), ("class A {}\n", Outcome.PASS)])
def test_different_scanner_identity_between_pre_and_post_is_not_comparable(tmp_path, post_text, outcome):
    workspace = _workspace(tmp_path, {"A.java": "x BAD_HIGH\n"})
    with FakeRegistration(FakeKnobs(reported_version={"pre": "9.9.8"})):
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", post_text, base="x BAD_HIGH\n")], tmp_path)
    assert result.outcome is outcome and model.BASELINE_NOT_COMPARABLE in result.reason_codes
    assert result.evidence["scans"]["comparable"] is False


# --- Scanner failures ---------------------------------------------------------------

@pytest.mark.parametrize("status,reason,outcome", [
    (ScanStatus.FAILED, model.SCAN_FAILED, Outcome.UNKNOWN),
    (ScanStatus.TIMEOUT, model.SCAN_TIMEOUT, Outcome.UNKNOWN),
    (ScanStatus.MALFORMED_OUTPUT, model.SCAN_OUTPUT_MALFORMED, Outcome.UNKNOWN),
])
@pytest.mark.parametrize("requirement", ["optional", "required"])
def test_scanner_failure_is_never_pass_and_blocks_under_either_requirement(tmp_path, status, reason, outcome, requirement):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(status={"post": status})):
        result = _evaluate(_config(requirement=requirement), workspace,
                           [_write(workspace, "A.java", "class A { }\n", base=BASE_A)], tmp_path)
    assert result.outcome is outcome and result.reason_codes == (reason,) and not result.permits_commit


def test_scanner_config_error_is_unavailable(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(status={"pre": ScanStatus.CONFIG_ERROR})):
        result = _evaluate(_config(requirement="required"), workspace,
                           [_write(workspace, "A.java", "class A { }\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.UNAVAILABLE and not result.permits_commit


def test_an_internal_error_fails_closed_and_a_normal_run_is_not_an_internal_error(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    with FakeRegistration(FakeKnobs(raise_in_scan=True)):
        broken = _evaluate(_config(), workspace, writes, tmp_path)
    with FakeRegistration():
        normal = _evaluate(_config(), workspace, writes, tmp_path)
    assert broken.outcome is Outcome.UNKNOWN and broken.reason_codes == (model.STATIC_ANALYSIS_INTERNAL_ERROR,)
    assert normal.outcome is Outcome.PASS and normal.reason_codes == ()


def test_base_drift_before_the_scan_is_unknown(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    write = _write(workspace, "A.java", "class A { }\n", base=BASE_A)
    Path(workspace, "A.java").write_text("someone else edited this\n")
    with FakeRegistration() as fake:
        result = _evaluate(_config(), workspace, [write], tmp_path)
    assert result.outcome is Outcome.UNKNOWN and result.reason_codes == (model.BASELINE_IDENTITY_MISMATCH,)
    assert fake.scans == []


def test_an_in_place_candidate_has_no_pristine_base(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration():
        result = StaticAnalysisService(_config(), evidence_root=str(tmp_path / "e")).evaluate_candidate(
            StaticAnalysisCandidate(materialize=list, workspace_path=workspace, run_id="r", unit_id="u", in_place=True))
    assert result.outcome is Outcome.UNKNOWN and result.reason_codes == (model.BASELINE_UNAVAILABLE_IN_PLACE,)


# --- Waivers --------------------------------------------------------------------------

def _waive(workspace, **overrides):
    fields = dict(workspace_root=workspace, waiver_id="SAW-1", provider=FAKE, rule_id="fake:bad-high",
                  paths=["A.java"], reason="accepted legacy risk", owner="security", max_severity="high",
                  classifications=["introduced"])
    fields.update(overrides)
    record = new_waiver(**fields)
    write_waiver(waiver_store_path(None, workspace), workspace, record)
    return record


def test_valid_waiver_is_accepted_risk_never_pass(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace, expires_at=(datetime.now(timezone.utc) + timedelta(days=5)).isoformat())
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.ACCEPTED_RISK and result.permits_commit and result.accepted_risk
    assert result.waiver_ids == ("SAW-1",) and result.gap is None
    assert banner(result).startswith(ACCEPTED_RISK_BANNER) and ACCEPTED_RISK_BANNER == "ACCEPTED RISK — NOT A CLEAN PASS"
    fields = static_analysis_result_fields(result)
    assert fields["accepted_risk"] is True and fields["accepted_risks"] == ["SAW-1"]
    assert fields["static_analysis"]["outcome"] == "ACCEPTED_RISK" != "PASS"
    assert result.gate_status == "accepted_risk"
    [finding] = result.evidence["findings"]
    assert (finding["decision"], finding["basis"], finding["waiver_id"]) == ("accepted", "waiver", "SAW-1")


@pytest.mark.parametrize("overrides,reason", [
    ({"expires_at": "2000-01-01T00:00:00Z"}, model.WAIVER_EXPIRED),
    ({"paths": ["Other.java"]}, model.WAIVER_SCOPE_MISMATCH),
    ({"classifications": ["existing"]}, model.WAIVER_SCOPE_MISMATCH),
    ({"fingerprint": "not-this-finding"}, model.WAIVER_SCOPE_MISMATCH),
    ({"max_severity": "medium"}, model.WAIVER_SEVERITY_EXCEEDED),
    ({"rule_pack_digest": "another-pack"}, model.WAIVER_RULE_PACK_MISMATCH),
])
def test_mismatched_or_expired_waiver_never_applies_silently(tmp_path, overrides, reason):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace, **overrides)
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED
    assert [r["reason"] for r in result.evidence["waivers"]["rejected"]] == [reason]


def test_a_waiver_from_another_workspace_never_applies(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    other = str(tmp_path / "other")
    os.makedirs(other)
    record = new_waiver(workspace_root=other, waiver_id="SAW-9", provider=FAKE, rule_id="fake:bad-high",
                        paths=["A.java"], reason="r", owner="o", max_severity="high", classifications=["introduced"])
    write_waiver(waiver_store_path(None, workspace), workspace, record)
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED
    assert result.evidence["waivers"]["rejected"][0]["reason"] == model.WAIVER_WORKSPACE_MISMATCH


def test_a_tampered_store_is_invalid_and_nothing_from_it_applies(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace)
    path = waiver_store_path(None, workspace)
    payload = json.loads(Path(path).read_text())
    payload["waivers"][0]["max_severity"] = "critical"
    Path(path).write_text(json.dumps(payload))
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED and model.WAIVER_STORE_INVALID in result.reason_codes
    assert result.evidence["waivers"]["store_status"] == "invalid"
    assert "WAIVER_DIGEST_INVALID" in result.evidence["waivers"]["store_error"]


def test_coverage_gaps_and_scanner_failures_are_never_waivable(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace, rule_id="fake:bad-high")
    with FakeRegistration(FakeKnobs(status={"post": ScanStatus.FAILED})):
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.UNKNOWN and not result.waiver_ids


def test_the_waiver_store_can_never_live_inside_the_workspace(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with pytest.raises(ValueError, match="inside the workspace"):
        waiver_store_path(os.path.join(workspace, ".kriya", "waivers.json"), workspace)
    with FakeRegistration():
        result = _evaluate(_config(waivers={"store": os.path.join(workspace, "w.json")}), workspace,
                           [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], tmp_path)
    assert result.outcome is Outcome.BLOCKED and result.evidence["waivers"]["store_status"] == "invalid"


def test_a_waiver_shaped_file_written_by_the_candidate_has_no_effect(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    forged = {"schema_version": 1, "waivers": [{"waiver_id": "LLM-1", "rule_id": "fake:bad-high", "paths": ["*"]}]}
    writes = [_write(workspace, "A.java", "x BAD_HIGH  # risk accepted by owner\n", base=BASE_A),
              _write(workspace, ".kriya/static_analysis/waivers.json", json.dumps(forged))]
    with FakeRegistration():
        result = _evaluate(_config(), workspace, writes, tmp_path)
    assert result.outcome is Outcome.BLOCKED and result.waiver_ids == ()


def test_revoke_takes_effect_on_the_next_evaluation(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace)
    writes = [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)]
    with FakeRegistration():
        assert _evaluate(_config(), workspace, writes, tmp_path).outcome is Outcome.ACCEPTED_RISK
        assert revoke_waiver(waiver_store_path(None, workspace), workspace, "SAW-1") is True
        assert _evaluate(_config(), workspace, writes, tmp_path).outcome is Outcome.BLOCKED
    assert load_waivers(waiver_store_path(None, workspace), workspace).records == ()


# --- Evidence and the commit guard -------------------------------------------------------

def test_evidence_is_persisted_with_a_recomputable_digest(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration():
        result = _evaluate(_config(), workspace, [_write(workspace, "A.java", "x BAD_MEDIUM\n", base=BASE_A)], tmp_path)
    stored = json.loads(Path(result.evidence["evidence_path"]).read_text())
    digest = stored.pop("evidence_digest")
    stored.pop("evidence_path", None)
    assert digest == canonical_digest(stored) == result.evidence_digest
    assert Path(result.evidence["evidence_path"]).with_name("post.raw.json").exists()


def test_an_evidence_write_failure_is_recorded_and_never_changes_the_verdict(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    with FakeRegistration():
        result = StaticAnalysisService(_config(), evidence_root=str(blocker)).evaluate(StaticAnalysisRequest(
            writes=[_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)], workspace_path=workspace,
            run_id="r", unit_id="u"))
    assert result.outcome is Outcome.BLOCKED and result.evidence["evidence_path"] is None
    assert "evidence_persist_error" in result.evidence


def _commit(workspace, writes, guard):
    return commit_terminal_candidate(writes, workspace_path=workspace, transaction_id="tx", static_analysis=guard)


def test_fresh_evidence_commits_and_names_itself_in_the_commit_evidence(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    with FakeRegistration():
        cfg = _config()
        result = _evaluate(cfg, workspace, writes, tmp_path)
        guard = commit_guard(cfg, result)
        assert guard.evidence_id() == f"static_analysis:PASS:{result.evidence_digest}"
        outcome = _commit(workspace, writes, guard)
    assert outcome.committed and Path(workspace, "A.java").read_text() == "class A { }\n"


def _stale(tmp_path, mutate, *, module=False):
    files = {"A.java": BASE_A, "B.java": "class B {}\n"}
    workspace = _workspace(tmp_path, files)
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    knobs = FakeKnobs(minimum_scope=ScanScope.MODULE, cross_file=True) if module else FakeKnobs()
    with FakeRegistration(knobs) as fake:
        cfg = _config()
        result = _evaluate(cfg, workspace, writes, tmp_path)
        assert result.permits_commit
        cfg, writes = mutate(cfg, workspace, writes, fake)
        outcome = _commit(workspace, writes, commit_guard(cfg, result))
    assert not outcome.committed and outcome.workspace_state == "UNCHANGED"
    assert Path(workspace, "A.java").read_text() == BASE_A
    return outcome


def test_stale_candidate_bytes_are_refused(tmp_path):
    outcome = _stale(tmp_path, lambda cfg, ws, writes, fake: (cfg, [_write(ws, "A.java", "class A {  }\n", base=BASE_A)]))
    assert outcome.reason_code == model.STATIC_ANALYSIS_EVIDENCE_STALE and "candidate batch" in str(outcome.error)


def test_an_unchanged_in_scope_file_edited_after_the_scan_is_refused(tmp_path):
    def mutate(cfg, ws, writes, fake):
        Path(ws, "B.java").write_text("class B { int y; }\n")
        return cfg, writes
    outcome = _stale(tmp_path, mutate, module=True)
    assert "base scope" in str(outcome.error)


def test_a_file_added_to_the_scope_after_the_scan_is_refused(tmp_path):
    def mutate(cfg, ws, writes, fake):
        Path(ws, "C.java").write_text("class C {}\n")
        return cfg, writes
    outcome = _stale(tmp_path, mutate, module=True)
    # Membership is part of the base-scope identity (every member's path and digest).
    assert "base scope" in str(outcome.error)


def test_a_provider_runtime_or_rule_pack_change_is_refused(tmp_path):
    def mutate(cfg, ws, writes, fake):
        fake.knobs.rule_pack_digest = "pack-digest-2"
        return cfg, writes
    assert "provider runtime or rule packs" in str(_stale(tmp_path, mutate).error)


def test_an_effective_settings_change_is_refused(tmp_path):
    def mutate(cfg, ws, writes, fake):
        return _config(policy={"existing": {"high": "block"}}), writes
    assert "effective settings" in str(_stale(tmp_path, mutate).error)


def test_missing_or_non_permitting_evidence_is_refused_when_enabled(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    writes = [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A)]
    with FakeRegistration():
        cfg = _config()
        blocked = _evaluate(cfg, workspace, writes, tmp_path)
        missing = _commit(workspace, writes, commit_guard(cfg, None))
        refused = _commit(workspace, writes, commit_guard(cfg, blocked))
        passing_disabled = _commit(workspace, writes, commit_guard(cfg, _evaluate(AppConfig(), workspace, writes, tmp_path)))
    assert missing.reason_code == model.STATIC_ANALYSIS_EVIDENCE_MISSING
    assert refused.reason_code == model.STATIC_ANALYSIS_NOT_PERMITTED
    assert passing_disabled.reason_code == model.STATIC_ANALYSIS_EVIDENCE_MISSING
    assert Path(workspace, "A.java").read_text() == BASE_A


def test_disabling_analysis_after_the_scan_is_stale_and_disabled_config_commits(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A)]
    with FakeRegistration():
        result = _evaluate(_config(), workspace, writes, tmp_path)
    assert _commit(workspace, writes, commit_guard(AppConfig(), result)).reason_code == model.STATIC_ANALYSIS_EVIDENCE_STALE
    assert _commit(workspace, writes, DISABLED_STATIC_ANALYSIS).committed


def test_a_test_double_config_never_enables_analysis(tmp_path):
    """A truthy non-bool (a spec'd MagicMock attribute) must never switch the gate on."""
    from types import SimpleNamespace

    double = SimpleNamespace(static_analysis=SimpleNamespace(enabled=object()))
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    assert commit_guard(double, None).verify([], workspace) is None
    assert commit_guard(None, None).verify([], workspace) is None


# --- Outcome/reason vocabularies -----------------------------------------------------------

def test_reason_code_table_is_closed():
    constants = {v for k, v in vars(model).items() if re.fullmatch(r"[A-Z][A-Z_]+", k) and isinstance(v, str)
                 and k not in ("ACCEPTED_RISK_BANNER",)}
    assert constants == set(model.REASON_CODES)


def test_every_outcome_has_a_gate_status_and_disabled_is_never_passed():
    assert set(GATE_EVENT_STATUS) == set(Outcome)
    assert [o for o, status in GATE_EVENT_STATUS.items() if status == "passed"] == [Outcome.PASS]


def test_target_statuses_other_than_covered_are_never_reported_as_analyzed(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A, "n.cpp": "int f();\n"})
    writes = [_write(workspace, "A.java", "class A { }\n", base=BASE_A), _write(workspace, "n.cpp", "int g();\n", base="int f();\n")]
    with FakeRegistration():
        result = _evaluate(_config(), workspace, writes, tmp_path)
    coverage = result.evidence["coverage"]
    assert coverage["confirmed_analyzed"] == 1
    assert {u["path"] for u in coverage["uncovered"]} == {"n.cpp"}
    assert TargetStatus.COVERED.value not in {u["status"] for u in coverage["uncovered"]}


# --- Structure: layering and authority ---------------------------------------------------------

def _py_files(root):
    return [p for p in root.rglob("*.py") if "__pycache__" not in p.parts]


def test_no_provider_name_above_the_adapters():
    offenders = [
        str(p.relative_to(REPO)) for p in _py_files(KRIYA)
        if "adapters" not in p.parts and re.search("semgrep", p.read_text(), re.IGNORECASE)
    ]
    assert offenders == []


def _imports(path):
    tree = ast.parse(path.read_text())
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
    return names


def test_workflow_uses_only_the_service_and_model():
    for path in _py_files(KRIYA / "workflow"):
        used = {name for name in _imports(path) if name.startswith("kriya.static_analysis")}
        assert used <= {"kriya.static_analysis.service", "kriya.static_analysis.model"}, (path, used)


def test_static_analysis_never_imports_the_orchestrators_and_adapters_never_see_policy():
    forbidden = {"kriya.workflow.workflow", "kriya.workflow.workflow_controller", "kriya.workflow.attempt",
                 "kriya.workflow.retry_strategy"}
    for path in _py_files(KRIYA / "static_analysis"):
        assert not _imports(path) & forbidden, path
    for path in _py_files(KRIYA / "static_analysis" / "adapters"):
        assert not {n for n in _imports(path) if n.startswith(("kriya.static_analysis.policy",
                                                              "kriya.static_analysis.service",
                                                              "kriya.static_analysis.waivers"))}, path


def test_kriya_never_imports_the_test_fake():
    assert [p for p in _py_files(KRIYA) if "_fake_static_analysis" in p.read_text()] == []


def _calls(name):
    hits = []
    for path in _py_files(KRIYA):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call):
                func = node.func
                called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                if called == name:
                    hits.append((str(path.relative_to(REPO)), node))
    return hits


def test_only_the_operator_cli_writes_or_revokes_waivers():
    assert [p for p, _ in _calls("write_waiver")] == ["kriya/cli.py"]
    assert [p for p, _ in _calls("revoke_waiver")] == ["kriya/cli.py"]
    assert [p for p, _ in _calls("new_waiver")] == ["kriya/cli.py"]


def test_every_production_commit_passes_a_config_built_guard():
    sites = _calls("commit_terminal_candidate")
    assert sorted(p for p, _ in sites) == ["kriya/workflow/commit_service.py", "kriya/workflow/workflow.py"]
    for path, node in sites:
        [value] = [kw.value for kw in node.keywords if kw.arg == "static_analysis"]
        rendered = ast.unparse(value)
        assert rendered.startswith("commit_guard(") or rendered == "request.static_analysis", (path, rendered)
    [(_, request_site)] = [s for s in _calls("TerminalCommitRequest") if s[0] == "kriya/workflow/workflow_controller.py"]
    [guard] = [kw.value for kw in request_site.keywords if kw.arg == "static_analysis"]
    assert ast.unparse(guard).startswith("commit_guard(")
    assert [p for p, _ in _calls("StaticAnalysisCommitGuard")] == ["kriya/static_analysis/service.py"]


def test_both_commit_boundaries_use_the_one_service():
    """The enforce controller and the direct/milestone boundary construct
    the same service (the operator scan is the only other caller)."""
    assert sorted(p for p, _ in _calls("StaticAnalysisService")) == [
        "kriya/static_analysis/operator_scan.py", "kriya/workflow/workflow.py", "kriya/workflow/workflow_controller.py",
    ]


def test_a_static_analysis_stop_is_deterministic_never_a_developer_retry():
    source = (KRIYA / "workflow" / "retry_strategy.py").read_text()
    for failure_type in ("static_analysis_blocked", "static_analysis_unknown", "static_analysis_unavailable"):
        assert f'"{failure_type}"' in source


# --- Both commit boundaries ------------------------------------------------------------------

def test_a_configuration_without_the_section_is_not_configured_not_an_error(tmp_path):
    """Found by the enforce characterization suite: an engine whose config
    object has no static_analysis section must report DISABLED, never raise
    (a raise would fail the gate closed as a false failure)."""
    from types import SimpleNamespace

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    result = StaticAnalysisService(SimpleNamespace(), evidence_root=str(tmp_path / "e")).evaluate_candidate(
        StaticAnalysisCandidate(materialize=list, workspace_path=workspace, run_id="r", unit_id="u"))
    assert result.outcome is Outcome.DISABLED and result.reason_codes == (model.STATIC_ANALYSIS_NOT_CONFIGURED,)


def _direct_state(workspace, rel, original, candidate_root, new_text):
    from kriya.workflow.state import GenerationState

    state = GenerationState()
    state.all_original_contents[rel] = original
    state.all_files_written.add(rel)
    Path(candidate_root, rel).parent.mkdir(parents=True, exist_ok=True)
    Path(candidate_root, rel).write_text(new_text)
    return state


def test_direct_boundary_blocks_with_a_deterministic_stop(tmp_path):
    from kriya.workflow.failure import QualityGateFailure
    from kriya.workflow.workflow import _run_static_analysis_gate

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    state = _direct_state(workspace, "A.java", BASE_A, worktree, "x BAD_HIGH\n")
    with FakeRegistration():
        cfg = _config()
        with pytest.raises(QualityGateFailure) as raised:
            _run_static_analysis_gate(cfg, state, worktree_path=str(worktree), workspace_path=workspace,
                                      run_id="run", unit_id="direct-attempt0")
    failure = raised.value.failure
    assert failure.type == "static_analysis_blocked" and failure.source == "static_analysis_gate"
    assert state.environment_failure.startswith("STATIC_ANALYSIS_BLOCKED: ")
    assert "SCANNER-TEXT" not in state.environment_failure
    assert state.gate_outcomes[-1]["type"] == "static_analysis_blocked"
    assert state.static_analysis_result.outcome is Outcome.BLOCKED
    assert any(e.kind == "static_analysis.result" for e in state.run_events)


def test_direct_boundary_records_a_permitted_result_for_the_commit(tmp_path):
    from kriya.workflow.workflow import _run_static_analysis_gate

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    state = _direct_state(workspace, "A.java", BASE_A, worktree, "class A { }\n")
    with FakeRegistration():
        cfg = _config()
        _run_static_analysis_gate(cfg, state, worktree_path=str(worktree), workspace_path=workspace,
                                  run_id="run", unit_id="direct-attempt0")
        result = state.static_analysis_result
        assert result.outcome is Outcome.PASS and state.environment_failure is None
        from kriya.workflow.workflow import _direct_terminal_writes

        writes = _direct_terminal_writes(str(worktree), workspace, state)
        assert _commit(workspace, writes, commit_guard(cfg, result)).committed


@pytest.mark.parametrize("text,outcome,status", [
    ("x BAD_HIGH\n", Outcome.BLOCKED, "failed"), ("class A { }\n", Outcome.PASS, "passed"),
])
def test_enforce_gate_reports_the_real_outcome_and_blocks_the_commit(tmp_path, text, outcome, status):
    import asyncio

    from kriya.workflow.commit_service import plan_terminal_writes
    from kriya.workflow.migration import MigrationResolution, MigrationResolutionStatus
    from kriya.workflow.obligations import ObligationLedger
    from kriya.workflow.plan_schema import EngineeringPlan, ExecutionMethod, FileAction, PlannedFile, Subtask
    from kriya.workflow.requirements import derive_requirements
    from kriya.workflow.terminal_gate_service import TerminalGateRequest, TerminalGateService, TerminalGateValidators
    from kriya.workflow.triage import ChangeKind

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "A.java").write_text(text)
    plan = EngineeringPlan(plan_id="p", kind=ChangeKind.TASK, subtasks=[Subtask(
        id="s1", description="d", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path="A.java", action=FileAction.MODIFY)])])
    revisions = {"A.java": content_revision(BASE_A)}

    async def verify(*args, **kwargs):
        del args, kwargs
        return []

    events = []

    async def emit(gate, gate_status, reason):
        events.append((gate, gate_status, reason))

    with FakeRegistration():
        cfg = _config()
        validators = TerminalGateValidators(
            find_migration_incomplete=lambda *a, **k: None, validate_stack_contract_artifacts=lambda *a, **k: None,
            enforce_preserved_reference_terminal_integrity=lambda ledger, root: None,
            blocking_requirements=lambda *a, **k: [], verify_original_requirements=verify,
            evaluate_static_analysis=StaticAnalysisService(cfg, evidence_root=str(tmp_path / "ev")).evaluate_candidate,
        )
        request = TerminalGateRequest(
            plan=plan, goal="Update A.java.", candidate_root=str(candidate), workspace_path=workspace,
            migration_resolution=MigrationResolution(MigrationResolutionStatus.NOT_APPLICABLE),
            obligation_ledger=ObligationLedger(), requirement_set=derive_requirements("Update A.java."),
            autonomy=None, spec_compliance=None, milestone_id="m1",
            static_analysis_candidate=StaticAnalysisCandidate(
                materialize=lambda: plan_terminal_writes(plan, str(candidate), workspace, revisions),
                workspace_path=workspace, run_id="run", unit_id="m1"),
        )
        report = asyncio.run(TerminalGateService(validators).run(request, emit))
        writes = plan_terminal_writes(plan, str(candidate), workspace, revisions)
        commit = _commit(workspace, writes, commit_guard(cfg, report.static_analysis))
    assert report.static_analysis.outcome is outcome
    assert [e for e in events if e[0] == "static_analysis"][0][1] == status
    assert report.commit_eligible is (outcome is Outcome.PASS)
    assert dict(report.global_gaps())["global_static_analysis_gap"] == report.static_analysis.gap
    assert commit.committed is (outcome is Outcome.PASS)


def test_relative_rule_packs_and_waiver_store_resolve_against_the_config_directory(tmp_path, monkeypatch):
    from kriya.config.config import resolve_config_state

    config_dir = tmp_path / "operator"
    (config_dir / "rules").mkdir(parents=True)
    elsewhere = tmp_path / "cwd"
    elsewhere.mkdir()
    config_file = config_dir / "kriya.yaml"
    config_file.write_text(
        "static_analysis:\n"
        "  waivers: {store: waivers/w.json}\n"
        "  providers:\n"
        "    anyprovider:\n"
        "      rule_packs: [rules, {path: rules/x.yml, sha256: " + "a" * 64 + "}, /abs/pack.yml]\n"
    )
    monkeypatch.chdir(elsewhere)
    static = resolve_config_state(str(config_file)).config_dict["static_analysis"]
    real = os.path.realpath(str(config_dir))
    assert static["waivers"]["store"] == os.path.join(real, "waivers", "w.json")
    assert static["providers"]["anyprovider"]["rule_packs"] == [
        os.path.join(real, "rules"), {"path": os.path.join(real, "rules", "x.yml"), "sha256": "a" * 64},
        os.path.realpath("/abs/pack.yml"),
    ]


# --- Doctor ---------------------------------------------------------------------------------

def _doctor_rows(cfg, workspace):
    from kriya.production_doctor import _CHECKS, _Context, _run_check

    ctx = _Context(cfg=cfg, workspace=workspace)
    return {
        check_id: _run_check(ctx, check_id, required(cfg) if callable(required) else required, fn)
        for check_id, required, fn in _CHECKS if check_id.startswith("static_analysis.")
    }


def test_doctor_reports_disabled_as_not_applicable_the_established_way(tmp_path):
    from kriya.production_doctor import CheckStatus, _report

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    rows = _doctor_rows(AppConfig(), workspace)
    assert sorted(rows) == sorted(model_check_ids())
    for check in rows.values():
        assert check.status is CheckStatus.PASS and check.required is False
        assert check.evidence["status"] == "NOT_APPLICABLE"
    assert _report(rows.values()).schema_version == 1 and _report(rows.values()).production_ready


def model_check_ids():
    from kriya.static_analysis.doctor import CHECK_IDS

    return CHECK_IDS


def test_doctor_rows_for_an_enabled_required_provider(tmp_path):
    from kriya.production_doctor import CheckStatus

    workspace = _workspace(tmp_path, {"A.java": BASE_A, "n.cpp": "int f();\n"})
    _waive(workspace)  # no expiry -> WARN
    with FakeRegistration():
        rows = _doctor_rows(_config(requirement="required"), workspace)
    status = {k: v.status for k, v in rows.items()}
    assert status["static_analysis.configuration"] is CheckStatus.PASS
    assert status["static_analysis.provider"] is CheckStatus.PASS
    assert status["static_analysis.capability"] is CheckStatus.PASS
    assert status["static_analysis.coverage"] is CheckStatus.WARN
    assert rows["static_analysis.coverage"].evidence["uncovered"] == ["cpp"]
    assert rows["static_analysis.prerequisites"].evidence["status"] == "NOT_APPLICABLE"
    assert status["static_analysis.waivers"] is CheckStatus.WARN
    assert rows["static_analysis.waivers"].evidence["never_expiring"] == ["SAW-1"]
    assert status["static_analysis.egress"] is CheckStatus.WARN  # host mode: network not OS-enforced
    assert all(check.required for check in rows.values())


@pytest.mark.parametrize("requirement,expected", [("required", "FAIL"), ("optional", "WARN")])
def test_doctor_provider_unavailable_fails_only_when_required(tmp_path, requirement, expected):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    with FakeRegistration(FakeKnobs(probe_failure=model.PROVIDER_PROBE_FAILED)):
        rows = _doctor_rows(_config(requirement=requirement), workspace)
    assert rows["static_analysis.provider"].status.value == expected
    assert rows["static_analysis.egress"].status.value == expected


def test_doctor_fails_a_tampered_waiver_store_when_required(tmp_path):
    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    _waive(workspace)
    Path(waiver_store_path(None, workspace)).write_text("{not json")
    with FakeRegistration():
        rows = _doctor_rows(_config(requirement="required"), workspace)
    assert rows["static_analysis.waivers"].status.value == "FAIL"


# --- Operator CLI ---------------------------------------------------------------------------

def test_operator_cli_creates_lists_and_revokes_waivers(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main as cli_main

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    monkeypatch.chdir(workspace)
    runner = CliRunner()
    created = runner.invoke(cli_main, [
        "static-analysis", "waive", "--id", "SAW-7", "--provider", FAKE, "--rule", "fake:bad-high",
        "--path", "A.java", "--reason", "legacy", "--owner", "security", "--classification", "introduced",
        "--expires", "2099-01-01T00:00:00Z", "-y",
    ])
    assert created.exit_code == 0, created.output
    [record] = load_waivers(waiver_store_path(None, workspace), workspace).records
    assert (record.waiver_id, record.classifications, record.provenance["created_via"]) == (
        "SAW-7", ("introduced",), "cli:kriya static-analysis waive")
    listed = runner.invoke(cli_main, ["static-analysis", "waivers", "--json"])
    assert json.loads(listed.output)["waivers"][0]["waiver_id"] == "SAW-7"
    assert runner.invoke(cli_main, ["static-analysis", "revoke", "SAW-7"]).exit_code == 0
    assert runner.invoke(cli_main, ["static-analysis", "revoke", "SAW-7"]).exit_code == 1
    assert load_waivers(waiver_store_path(None, workspace), workspace).records == ()


def test_operator_cli_refuses_an_incomplete_waiver_and_asks_before_recording(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from kriya.cli import main as cli_main

    workspace = _workspace(tmp_path, {"A.java": BASE_A})
    monkeypatch.chdir(workspace)
    bad = CliRunner().invoke(cli_main, [
        "static-analysis", "waive", "--id", "SAW-8", "--provider", FAKE, "--rule", "fake:bad-high",
        "--path", "A.java", "--reason", " ", "--owner", "o", "-y",
    ])
    assert bad.exit_code == 2 and "reason and owner are required" in bad.output
    declined = CliRunner().invoke(cli_main, [
        "static-analysis", "waive", "--id", "SAW-8", "--provider", FAKE, "--rule", "fake:bad-high",
        "--path", "A.java", "--reason", "r", "--owner", "o",
    ], input="n\n")
    assert declined.exit_code == 1 and "never expires" in declined.output
    assert load_waivers(waiver_store_path(None, workspace), workspace).status == "absent"


def test_operator_scan_judges_working_tree_changes_against_a_git_base_read_only(tmp_path):
    import subprocess

    from kriya.static_analysis.operator_scan import run_operator_scan

    workspace = _workspace(tmp_path, {"A.java": "x BAD_HIGH\n", "B.java": "class B {}\n"})
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    Path(workspace, "A.java").write_text("class A {}\n")          # resolves the base finding
    Path(workspace, "C.java").write_text("y BAD_CRITICAL\n")      # untracked, introduces one
    os.remove(os.path.join(workspace, "B.java"))                   # deleted: PRE only
    _waive(workspace, waiver_id="SAW-OP", rule_id="fake:bad-critical", paths=["C.java"],
           max_severity="critical", expires_at="2099-01-01T00:00:00Z")
    with FakeRegistration() as fake:
        result = run_operator_scan(_config(), workspace, "HEAD")
    assert result.outcome is Outcome.ACCEPTED_RISK and result.waiver_ids == ("SAW-OP",)
    assert result.evidence["summary"]["resolved"] == 1 and result.evidence["summary"]["accepted"] == 1
    trees = fake.instances[0].trees
    assert set(trees["post"]) == {"A.java", "C.java"} and set(trees["pre"]) == {"A.java", "B.java"}
    assert Path(workspace, "C.java").read_text() == "y BAD_CRITICAL\n"  # nothing written back
    listed = subprocess.run(["git", "worktree", "list"], cwd=workspace, capture_output=True, text=True, check=True)
    assert len(listed.stdout.strip().splitlines()) == 1                  # base worktree removed


def test_a_workspace_reached_through_a_symlink_is_the_same_workspace(tmp_path):
    """Found by the operator-scan test: a symlinked workspace path (macOS
    /var -> /private/var) made every changed path look like it escaped the
    workspace. Real paths are compared."""
    real = _workspace(tmp_path, {"A.java": BASE_A})
    link = tmp_path / "linked-ws"
    link.symlink_to(real)
    writes = [_write(str(link), "A.java", "x BAD_HIGH\n", base=BASE_A)]
    with FakeRegistration():
        cfg = _config()
        result = _evaluate(cfg, str(link), writes, tmp_path)
        assert result.outcome is Outcome.BLOCKED and result.evidence["findings"][0]["path"] == "A.java"
        clean = [_write(str(link), "A.java", "class A { }\n", base=BASE_A)]
        passed = _evaluate(cfg, str(link), clean, tmp_path)
        assert _commit(str(link), clean, commit_guard(cfg, passed)).committed


def test_a_waiver_never_masks_another_blocking_finding(tmp_path):
    """One finding released by a waiver, another still blocking: BLOCKED,
    never ACCEPTED_RISK (BLOCKED outranks ACCEPTED_RISK)."""
    workspace = _workspace(tmp_path, {"A.java": BASE_A, "B.java": "class B {}\n"})
    _waive(workspace, paths=["A.java"])
    writes = [_write(workspace, "A.java", "x BAD_HIGH\n", base=BASE_A),
              _write(workspace, "B.java", "y BAD_HIGH\n", base="class B {}\n")]
    with FakeRegistration():
        result = _evaluate(_config(), workspace, writes, tmp_path)
    assert result.outcome is Outcome.BLOCKED and not result.permits_commit
    decisions = {f["path"]: f["decision"] for f in result.evidence["findings"]}
    assert decisions == {"A.java": "accepted", "B.java": "block"}
    assert set(result.reason_codes) >= {model.WAIVER_APPLIED, model.NEW_FINDING_BLOCKED}
