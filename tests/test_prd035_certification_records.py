"""PRD-035 (deterministic parts): the certification report is CERTIFIED only
for the qualified target identity on an exact environment with every case
PASSED; certification records are keyed by the exact identity and go STALE
when it changes; `kriya model certification` reports it.
"""
import json

import pytest
from _model_certification import CASES, TARGET, WIRING, build_report, render_markdown
from click.testing import CliRunner

from kriya.core import model_certification as mc

IDENTITY = {"model": "m:1", "runtime_fingerprint": "rt", "runtime_exact": "True", "qualification": "QUALIFIED",
            "inference_settings_digest": "set"}
ENVIRONMENT = {"digest": "sha256:env", "exact": True, "accelerator_model": "M1 Max", "system_memory_class_gib": 64}


def _results(verdicts=None, identity=IDENTITY, environment=ENVIRONMENT, skip=()):
    verdicts = verdicts or {}
    return [{"case_id": case_id, "verdict": verdicts.get(case_id, "PASSED"),
             "record": {"identity": identity, "environment": environment, "final_success": True}}
            for case_id, _ in CASES if case_id not in skip]


def test_every_case_passed_on_the_qualified_target_identity_certifies():
    report = build_report(_results())
    content = report["content"]
    assert content["status"] == "CERTIFIED" and content["tier"] == TARGET
    assert content["summary"]["PASSED"] == len(CASES)
    assert report["content_digest"] in render_markdown(report)


@pytest.mark.parametrize("results,reason", [
    (_results({"C6": "FAILED"}), "one failed case"),
    (_results(skip=("C11",)), "a case that never ran"),
    (_results(identity={**IDENTITY, "qualification": "NOT_QUALIFIED"}), "an unqualified identity"),
    (_results(environment={**ENVIRONMENT, "exact": False}), "an inexact environment"),
])
def test_anything_less_is_never_certified(results, reason):
    content = build_report(results)["content"]
    assert content["status"] == "FAILED", reason
    if reason == "a case that never ran":
        assert content["summary"]["NOT_RUN"] == 1


def test_mixed_identities_are_wiring_never_certification():
    results = _results()
    results[0]["record"]["identity"] = {**IDENTITY, "runtime_fingerprint": "other"}
    content = build_report(results)["content"]
    assert content["tier"] == WIRING and content["status"] == "FAILED" and not content["identity_consistent"]


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv(mc.CERTIFICATION_HOME_ENV, str(tmp_path / "certs"))
    return tmp_path / "certs"


KEY = mc.CertificationKey(model="m:1", runtime_digest="rt", inference_settings_digest="set",
                          environment_digest="sha256:env")


def test_a_certified_matrix_is_current_only_for_its_exact_key(home):
    assert mc.certification_status(KEY)["status"] == mc.MISSING
    mc.save_certification(KEY, build_report(_results()))
    assert mc.certification_status(KEY)["status"] == mc.CURRENT
    changed = mc.CertificationKey(model="m:1", runtime_digest="rt", inference_settings_digest="other-settings",
                                  environment_digest="sha256:env")
    stale = mc.certification_status(changed)
    assert stale["status"] == mc.STALE and stale["changed"] == ["inference_settings_digest"]
    newer_cases = mc.CertificationKey(**{**KEY.__dict__, "case_set_version": mc.CASE_SET_VERSION + 1})
    assert mc.certification_status(newer_cases)["changed"] == ["case_set_version"]


def test_a_failed_matrix_is_stored_failed_never_current(home):
    mc.save_certification(KEY, build_report(_results({"C3": "FAILED"})))
    assert mc.certification_status(KEY)["status"] == mc.FAILED


def test_a_tampered_record_is_invalid(home):
    path = mc.save_certification(KEY, build_report(_results({"C3": "FAILED"})))
    record = json.loads(open(path).read())
    record["status"] = mc.CERTIFIED
    open(path, "w").write(json.dumps(record))
    assert mc.certification_status(KEY)["status"] == mc.INVALID


def test_the_cli_reports_missing_then_current(home, tmp_path, monkeypatch):
    from kriya.cli import main
    from kriya.core.execution_environment import environment_for_fingerprint
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.model_runtime import resolve_configured_model_runtime

    monkeypatch.chdir(tmp_path)
    missing = CliRunner().invoke(main, ["model", "certification", "--json"])
    assert missing.exit_code == 1 and json.loads(missing.output)["status"] == mc.MISSING
    from kriya.config import load_config

    cfg = load_config()
    runtime = resolve_configured_model_runtime(cfg, cfg.llm.model, fresh=True)
    key = mc.CertificationKey(model=cfg.llm.model, runtime_digest=runtime.digest,
                              inference_settings_digest=role_inference_settings(cfg, "developer", cfg.llm.model).digest,
                              environment_digest=environment_for_fingerprint(runtime).digest)
    mc.save_certification(key, build_report(_results()))
    current = CliRunner().invoke(main, ["model", "certification", "--json"])
    assert current.exit_code == 0 and json.loads(current.output)["status"] == mc.CURRENT
