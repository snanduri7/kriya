"""PRD-036 final release gate (deterministic parts).

- The release-candidate identity binds the release identity, the operator
  production config, every role's exact model runtime, settings and
  qualification, the execution environment and the case set. Any change is
  a new digest and is named.
- The release streak counts only consecutive complete 11/11 matrices on one
  candidate. A failure, an incomplete matrix, another identity or an
  identity change mid-trial resets it, and every trial is kept.
- The live harness takes its model bindings from the release config exactly
  as load_config merges them.
- The canary verdict and the final gate pass only when every criterion does.
"""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import _live_identity
import pytest
from _model_certification import CASES, build_report
from _strict_doubles import strict_config

from kriya.config.config import FallbackModelConfig, LLMConfig, resolve_config_state
from kriya.core import model_certification as mc
from kriya.core import release_candidate as rc
from kriya.core.release_identity import _digest

SCRIPTS = Path(__file__).parent.parent / "scripts"


def _script(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- the release-candidate identity ---------------------------------------------

@pytest.fixture
def source(tmp_path):
    root = tmp_path / "kriya-src"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"], cwd=root, check=True)
    return root


@pytest.fixture
def operator(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    path = tmp_path / "operator" / "production.yaml"
    path.parent.mkdir()
    rules = tmp_path / "operator" / "rules.yml"
    rules.write_text("rules: []\n")
    path.write_text("runtime_profile: production\nllm:\n  base_url: http://localhost:11434/v1\n"
                    "static_analysis:\n  providers:\n    semgrep:\n      rule_packs:\n"
                    f"        - {rules}\n")
    return path


class _Runtime(SimpleNamespace):
    pass


def _runtimes(**digests):
    def resolve(_cfg, model, fresh=True):
        assert fresh is True
        return _Runtime(digest=digests.get(model, f"rt-{model}"), exact=True, endpoint="http://localhost:11434/v1",
                        provider="ollama", provider_version="0.0.0")
    return resolve


@pytest.fixture
def qualified(monkeypatch):
    status = {"value": "QUALIFIED"}
    monkeypatch.setattr("kriya.core.model_qualification.assess",
                        lambda *_a, **_k: SimpleNamespace(status=status["value"]))
    monkeypatch.setattr("kriya.core.execution_environment.environment_for_fingerprint",
                        lambda _fp: SimpleNamespace(to_dict=lambda: {"digest": "sha256:env", "exact": True}))
    return status


def _cfg(temperature=0.7, fallback_temperature=0.7):
    return strict_config(
        llm={"model": "primary:1", "temperature": temperature},
        llm_chain=[{"model": "fallback:1", "base_url": "http://localhost:11434/v1", "temperature": fallback_temperature}],
    )


CASE_SET = rc.case_set_identity(mc.CASE_SET_VERSION, CASES)


def _candidate(source, operator, cfg=None, case_set=CASE_SET, **runtimes):
    return rc.release_candidate_identity(cfg or _cfg(), str(operator), case_set=case_set, source_root=str(source),
                                         resolve_runtime=_runtimes(**runtimes))


def test_an_unchanged_qualified_candidate_is_current_and_binds_every_component(source, operator, qualified):
    recorded = _candidate(source, operator)
    assert set(recorded) == {"version", "release", "production_config", "models", "case_set", "digest"}
    assert recorded["models"]["developer_primary"]["model"] == "primary:1"
    assert recorded["models"]["developer_fallback"]["model"] == "fallback:1"
    assert recorded["production_config"]["security_fields"] and recorded["case_set"]["case_ids"][0] == "C1"
    assert list(recorded["production_config"]["referenced_files"]) == [os.path.realpath(operator.parent / "rules.yml")]
    assert {b["role"] for b in recorded["models"]["bindings"]} >= {"developer", "planner", "reviewer"}
    assert rc.compare_release_candidate(recorded, _candidate(source, operator)) == {"status": rc.CURRENT, "changes": []}


def _commit(source):
    (source / "a.py").write_text("x = 2\n")
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "next"], cwd=source, check=True)


@pytest.mark.parametrize("change,expected", [
    (lambda s, o, q: _commit(s), "release.kriya.revision"),
    (lambda s, o, q: o.write_text(o.read_text() + "# edited\n"), "production_config.content_sha256"),
    (lambda s, o, q: o.write_text(o.read_text().replace("11434", "11435")), "production_config.security_field_set_digest"),
    (lambda s, o, q: (o.parent / "rules.yml").write_text("rules: [changed]\n"), "production_config.referenced_files"),
    (lambda s, o, q: {"primary:1": "rt-other"}, "models.developer_primary.runtime_digest"),
    (lambda s, o, q: {"cfg": _cfg(temperature=0.2)}, "models.developer_primary.inference_settings_digest"),
    (lambda s, o, q: {"cfg": _cfg(fallback_temperature=0.2)}, "models.developer_fallback.inference_settings_digest"),
    (lambda s, o, q: {"fallback:1": "rt-other"}, "models.developer_fallback.runtime_digest"),
    (lambda s, o, q: q.update(value="MISSING"), "models.bindings"),
    (lambda s, o, q: {"case_set": rc.case_set_identity(mc.CASE_SET_VERSION + 1, CASES)}, "case_set.version"),
    (lambda s, o, q: {"case_set": rc.case_set_identity(mc.CASE_SET_VERSION, CASES[:-1])}, "case_set.table_digest"),
])
def test_any_component_change_is_a_new_digest_and_named(source, operator, qualified, change, expected):
    recorded = _candidate(source, operator)
    effect = change(source, operator, qualified)
    effect = effect if isinstance(effect, dict) else {}
    runtimes = {k: v for k, v in effect.items() if k not in ("cfg", "case_set")}
    current = _candidate(source, operator, cfg=effect.get("cfg"), case_set=effect.get("case_set", CASE_SET), **runtimes)
    result = rc.compare_release_candidate(recorded, current)
    assert current["digest"] != recorded["digest"]
    assert result["status"] == rc.STALE and any(c.startswith(expected) for c in result["changes"]), result


def test_a_change_seen_only_in_a_nested_digest_is_still_stale(source, operator, qualified, monkeypatch):
    """The execution environment is compared by its own digest, which field
    comparison skips; the composite digest must still catch it."""
    recorded = _candidate(source, operator)
    monkeypatch.setattr("kriya.core.execution_environment.environment_for_fingerprint",
                        lambda _fp: SimpleNamespace(to_dict=lambda: {"digest": "sha256:other-host", "exact": True}))
    result = rc.compare_release_candidate(recorded, _candidate(source, operator))
    assert result == {"status": rc.STALE, "changes": ["digest.mismatch"]}


def test_an_unqualified_dirty_or_tampered_candidate_is_never_current(source, operator, qualified):
    qualified["value"] = "MISSING"
    unqualified = _candidate(source, operator)
    assert any(c.startswith("models.unqualified.developer.primary:1")
               for c in rc.compare_release_candidate(unqualified, unqualified)["changes"])
    qualified["value"] = "QUALIFIED"
    (source / "a.py").write_text("dirty\n")
    dirty = _candidate(source, operator)
    assert "release.kriya.unpinned" in rc.compare_release_candidate(dirty, dirty)["changes"]
    (source / "a.py").write_text("x = 1\n")
    recorded = _candidate(source, operator)
    forged = {**recorded, "case_set": {**recorded["case_set"], "version": 99}}
    assert not rc.candidate_digest_valid(forged)
    assert "digest" in rc.compare_release_candidate(forged, _candidate(source, operator))["changes"]


def test_the_security_identity_is_stable_and_never_stores_a_value(source, operator):
    first, second = rc.production_config_identity(str(operator)), rc.production_config_identity(str(operator))
    assert first == second and "11434" not in json.dumps(first)


# --- the release streak ----------------------------------------------------------

PRIMARY = {"model": "m:1", "runtime_digest": "rt", "inference_settings_digest": "set"}
FALLBACK = {"model": "f:1", "runtime_digest": "rt-f", "inference_settings_digest": "set-f"}


def _sealed(**overrides):
    material = {"version": 1, "release": {"kriya": {"revision": "a" * 40, "dirty": False}},
                "models": {"developer_primary": PRIMARY, "developer_fallback": FALLBACK,
                           "execution_environment": {"digest": "sha256:env"}},
                "case_set": CASE_SET, **overrides}
    return {**material, "digest": _digest(material)}


IDENTITY = {"model": "m:1", "runtime_fingerprint": "rt", "runtime_exact": "True", "qualification": "QUALIFIED",
            "inference_settings_digest": "set"}
ENVIRONMENT = {"digest": "sha256:env", "exact": True}
C6_ROLES = {"developer": {"runtime_digests": ["rt", "rt-f"], "settings_digests": ["set", "set-f"]}}


def _report(verdicts=None, identity=IDENTITY, environment=ENVIRONMENT, skip=(), roles=C6_ROLES):
    verdicts = verdicts or {}
    return build_report([{"case_id": case_id, "verdict": verdicts.get(case_id, "PASSED"),
                          "record": {"identity": identity, "environment": environment, "final_success": True,
                                     **({"roles": roles} if case_id == "C6" else {})}}
                         for case_id, _ in CASES if case_id not in skip])


def _resealed(**content):
    """A report whose content was changed and whose digest was recomputed
    (a well-formed report of a different matrix, not a tampered one)."""
    changed = {**_report()["content"], **content}
    return {"content": changed, "content_digest": mc._content_digest(changed)}


def _trial(candidate, report, tmp_path, n=0, after=None):
    evidence = tmp_path / f"trial-{n}"
    evidence.mkdir(exist_ok=True)
    return mc.record_trial(candidate, report, started="s", ended="e", evidence=str(evidence), candidate_after=after)


def test_three_consecutive_complete_matrices_certify_and_every_trial_is_kept(tmp_path):
    candidate = _sealed()
    streaks = [_trial(candidate, _report(), tmp_path, n)["streak_after"] for n in range(2)]
    assert mc.streak_status(candidate["digest"])["status"] == "IN_PROGRESS"
    streaks.append(_trial(candidate, _report(), tmp_path, 2)["streak_after"])
    status = mc.streak_status(candidate["digest"])
    assert streaks == [1, 2, 3] and status["status"] == mc.CERTIFIED and len(status["trials"]) == 3
    assert [t["trial"] for t in status["trials"]] == [1, 2, 3]
    assert all(len(t["cases"]) == len(CASES) for t in status["trials"])


def test_a_failure_resets_the_streak_without_dropping_any_trial(tmp_path):
    candidate = _sealed()
    _trial(candidate, _report(), tmp_path, 0)
    _trial(candidate, _report(), tmp_path, 1)
    failed = _trial(candidate, _report({"C8": "FAILED"}), tmp_path, 2)
    again = _trial(candidate, _report(), tmp_path, 3)
    status = mc.streak_status(candidate["digest"])
    assert failed["outcome"] == mc.FAILED and failed["streak_after"] == 0 and "case_C8_FAILED" in failed["reasons"]
    assert again["streak_after"] == 1 and status["status"] == "IN_PROGRESS" and len(status["trials"]) == 4


@pytest.mark.parametrize("report,after,reason", [
    (_report(skip=("C11",)), None, "case_C11_NOT_RUN"),
    (_resealed(cases=_report()["content"]["cases"][:-1]), None, "case_set_mismatch"),
    (_report(identity={**IDENTITY, "qualification": "NOT_QUALIFIED"}), None, "not_target_tier"),
    (_report(identity={**IDENTITY, "runtime_fingerprint": "other"}), None, "primary_identity_mismatch"),
    (_report(identity={**IDENTITY, "inference_settings_digest": "other"}), None, "primary_identity_mismatch"),
    (_report(environment={**ENVIRONMENT, "digest": "sha256:other"}), None, "environment_mismatch"),
    (_report(roles={"developer": {"runtime_digests": ["rt"], "settings_digests": ["set"]}}), None,
     "fallback_identity_mismatch"),
    ({**_report(), "content_digest": "forged"}, None, "report_digest_mismatch"),
    (_report(), _sealed(case_set={**CASE_SET, "version": 2}), "identity_changed_during_trial"),
])
def test_a_matrix_that_is_not_a_complete_pass_on_this_candidate_resets_the_streak(tmp_path, report, after, reason):
    candidate = _sealed()
    _trial(candidate, _report(), tmp_path, 0)
    entry = _trial(candidate, report, tmp_path, 1, after=after)
    assert entry["outcome"] == mc.FAILED and reason in entry["reasons"] and entry["streak_after"] == 0


def test_a_different_candidate_starts_its_own_streak_at_zero(tmp_path):
    first, second = _sealed(), _sealed(case_set={**CASE_SET, "table_digest": "other"})
    for n in range(2):
        _trial(first, _report(), tmp_path, n)
    assert mc.streak_status(second["digest"])["status"] == mc.MISSING
    assert _trial(second, _report(), tmp_path, 9)["streak_after"] == 1


def test_a_tampered_log_is_invalid_and_never_overwritten(tmp_path):
    candidate = _sealed()
    _trial(candidate, _report(), tmp_path, 0)
    path = Path(mc.streak_path(candidate["digest"]))
    log = json.loads(path.read_text())
    log["trials"][0]["streak_after"] = 3
    path.write_text(json.dumps(log))
    assert mc.streak_status(candidate["digest"])["status"] == mc.INVALID
    with pytest.raises(mc.StreakRecordInvalid):
        _trial(candidate, _report(), tmp_path, 1)
    assert json.loads(path.read_text()) == log


def test_a_candidate_whose_digest_does_not_match_is_refused(tmp_path):
    with pytest.raises(ValueError):
        _trial({**_sealed(), "digest": "forged"}, _report(), tmp_path)


# --- the live harness takes the release bindings exactly as load_config merges ---

def test_the_harness_bindings_equal_the_release_configs_effective_bindings(tmp_path, monkeypatch):
    path = tmp_path / "release.yaml"
    path.write_text("llm:\n  model: rel:1\n  temperature: 0.3\n  extra_body:\n    options: {num_ctx: 8192}\n"
                    "llm_chain:\n- model: fb:1\n  base_url: http://localhost:11434/v1\n  temperature: 0.4\n")
    monkeypatch.setattr(_live_identity, "RELEASE_CONFIG", str(path))
    effective = resolve_config_state(str(path)).config_dict
    empty = tmp_path / "empty.yaml"
    empty.write_text("{}\n")
    packaged = LLMConfig(**resolve_config_state(str(empty)).config_dict["llm"])  # what the harness starts from
    assert _live_identity.release_primary_binding(packaged) == LLMConfig(**effective["llm"])
    assert _live_identity.release_fallback_binding() == FallbackModelConfig(**effective["llm_chain"][0])
    monkeypatch.setattr(_live_identity, "RELEASE_CONFIG", None)
    assert _live_identity.release_primary_binding(packaged) is None and _live_identity.release_fallback_binding() is None


# --- canary verdict ---------------------------------------------------------------

@pytest.fixture
def canary(tmp_path, monkeypatch):
    module = _script("prd036_canary")
    workspace = tmp_path / "ws"
    workspace.mkdir()
    monkeypatch.setattr(module, "assess_workspace_commit_state",
                        lambda _ws: SimpleNamespace(safe=True, reason_codes=[]))
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    record = Path(module.run_record_path(str(workspace), "run1"))
    record.parent.mkdir(parents=True)
    record.write_text(json.dumps({"lifecycle_state": "SUCCESS", "commits": [{"result": "committed"}],
                                  "verification_evidence_ids": ["gate:x", "static_analysis:abc"]}))
    snap = {"containers": ["c0"], "networks": ["n0"], "worktrees": ["worktree /ws"], "lock_free": True,
            "processes": [], "tracked_changes": [], "head": ["h"]}
    files = {
        "exit_code": "0", "post-run-compile.exit": "0", "post-run-test.exit": "0",
        "stdout.json": json.dumps({"status": "success", "quality_gates_passed": True, "run_id": "run1",
                                   "subtask_results": [{"subtask_id": "s1", "status": "completed"}],
                                   "commit_evidence": {"state": "committed"}}),
        "before.json": json.dumps(snap),
        "after.json": json.dumps({**snap, "tracked_changes": [" M src/A.java"]}),
        "release-candidate-check.json": json.dumps({"status": "CURRENT", "changes": []}),
        "doctor-before.json": json.dumps({"production_ready": True, "checks": []}),
        "doctor-after.json": json.dumps({"production_ready": True, "checks": []}),
    }
    for name, text in files.items():
        (evidence / name).write_text(text)
    return module, evidence, workspace


def _verdict(canary):
    module, evidence, workspace = canary
    return module.verdict(str(evidence), str(workspace), ["src/A.java"])


def test_a_clean_production_canary_passes(canary):
    report = _verdict(canary)
    assert report["verdict"] == "PASS", {k: c for k, c in report["checks"].items() if not c["passed"]}
    assert json.loads((canary[1] / "canary.json").read_text()) == report


def _edit(evidence, name, **changes):
    path = evidence / name
    path.write_text(json.dumps({**json.loads(path.read_text()), **changes}))


@pytest.mark.parametrize("mutate,check", [
    (lambda e: (e / "exit_code").write_text("1"), "cli_exit_zero"),
    (lambda e: _edit(e, "stdout.json", status="failure"), "run_success"),
    (lambda e: _edit(e, "stdout.json", subtask_results=[]), "enforce_controller_exercised"),
    (lambda e: _edit(e, "stdout.json", commit_evidence={"state": "in_progress"}), "commit_committed"),
    (lambda e: _edit(e, "after.json", containers=["c0", "c1"]), "no_leaked_containers"),
    (lambda e: _edit(e, "after.json", networks=["n0", "n1"]), "no_leaked_networks"),
    (lambda e: _edit(e, "after.json", worktrees=["worktree /ws", "worktree /ws/.kriya/w"]), "no_leaked_worktrees"),
    (lambda e: _edit(e, "after.json", processes=["42 docker run"]), "no_leaked_processes"),
    (lambda e: _edit(e, "after.json", lock_free=False), "workspace_lock_released"),
    (lambda e: _edit(e, "after.json", tracked_changes=[" M src/A.java", " M pom.xml"]), "only_authorized_workspace_writes"),
    (lambda e: _edit(e, "after.json", head=["h2"]), "head_unchanged"),
    (lambda e: (e / "post-run-test.exit").write_text("1"), "independent_post_run_test"),
    (lambda e: _edit(e, "release-candidate-check.json", status="STALE"), "release_candidate_current"),
    (lambda e: _edit(e, "doctor-after.json", production_ready=False), "production_ready_after"),
])
def test_any_failed_canary_check_fails_the_canary(canary, mutate, check):
    mutate(canary[1])
    report = _verdict(canary)
    assert report["verdict"] == "FAIL" and not report["checks"][check]["passed"]


def test_a_canary_without_bound_static_analysis_evidence_or_a_settled_record_fails(canary, monkeypatch):
    module, _evidence, workspace = canary
    record = Path(module.run_record_path(str(workspace), "run1"))
    record.write_text(json.dumps({"lifecycle_state": "UNCERTAIN", "commits": [], "verification_evidence_ids": ["gate:x"]}))
    monkeypatch.setattr(module, "assess_workspace_commit_state",
                        lambda _ws: SimpleNamespace(safe=False, reason_codes=["UNCERTAIN_COMMIT_STATE"]))
    checks = _verdict(canary)["checks"]
    assert not checks["static_analysis_evidence_bound"]["passed"] and not checks["run_record_success"]["passed"]
    assert not checks["no_uncertain_commit_state"]["passed"]


# --- final gate ---------------------------------------------------------------------

@pytest.fixture
def gate():
    return _script("prd036_gate")


def _gate_inputs(gate):
    candidate = _sealed()
    trials = [{"trial": n, "outcome": mc.CERTIFIED, "reasons": [], "streak_after": n,
               "candidate_digest": candidate["digest"], "cases": [{"case_id": c, "verdict": "PASSED"} for c, _ in CASES]}
              for n in (1, 2, 3)]
    job = lambda name: {"name": name, "conclusion": "success"}  # noqa: E731 - a one-line fixture builder
    return {
        "candidate": {**candidate, "release": {**candidate["release"], "digest": "rel"}},
        "certification": {"status": "CERTIFIED", "problems": [], "release_identity": {"digest": "rel"},
                          "stages": {"release": {"status": "PASS"}},
                          "tiers": {"pytest": {"junit": {"failures": 0, "errors": 0, "passed": 7000}, "unexpected_skips": 0},
                                    "scanner": {"junit": {"failures": 0, "errors": 0, "passed": 27}}}},
        "canary": {"verdict": "PASS", "checks": {"production_ready_before": {"passed": True},
                                                  "production_ready_after": {"passed": True}}},
        "canary_candidate_digest": candidate["digest"],
        "streak": {"status": mc.CERTIFIED, "streak": 3, "trials": trials},
        "trial_evidence_present": {1: True, 2: True, 3: True},
        "hosted": [{"databaseId": 1, "headSha": "a" * 40,
                    "jobs": [job(n) for n in gate.REQUIRED_HOSTED_JOBS] + [{"name": "Nightly", "conclusion": "skipped"}]}],
        "open_blocking": [], "tracked_changes": [],
        "changed_since_candidate": ["handover/PRD-036_FINAL_CLOSURE.md", "evidence/PRD-036/gate.json",
                                    "handover/BACKLOG_REGISTRY.csv"],
    }


def test_the_gate_is_verified_only_with_every_criterion(gate):
    report = gate.evaluate(_gate_inputs(gate))
    assert report["result"] == "VERIFIED", {k: c for k, c in report["criteria"].items() if not c["passed"]}


def _set(inputs, key, value):
    inputs[key] = value


@pytest.mark.parametrize("mutate,criterion", [
    (lambda i: i["certification"].update(status="FAILED"), "deterministic_certification"),
    (lambda i: i["certification"]["tiers"]["pytest"].update(unexpected_skips=1), "deterministic_certification"),
    (lambda i: i["certification"]["tiers"]["scanner"]["junit"].update(passed=0), "scanner_executed"),
    (lambda i: i["certification"]["stages"]["release"].update(status="FAIL"), "release_stage"),
    (lambda i: i["certification"]["release_identity"].update(digest="other"), "certified_candidate_identity"),
    (lambda i: i["canary"].update(verdict="FAIL"), "canary"),
    (lambda i: _set(i, "canary_candidate_digest", "other"), "canary"),
    (lambda i: i["canary"]["checks"]["production_ready_after"].update(passed=False), "production_doctor_ready"),
    (lambda i: i["streak"].update(status="IN_PROGRESS"), "live_streak"),
    (lambda i: i["streak"]["trials"][1].update(outcome=mc.FAILED), "live_streak"),
    (lambda i: i["streak"]["trials"][0].update(candidate_digest="other"), "live_streak"),
    (lambda i: i["streak"]["trials"][2]["cases"].pop(), "live_streak"),
    (lambda i: i["streak"]["trials"][2]["cases"][0].update(verdict="FAILED"), "live_streak"),
    (lambda i: i["trial_evidence_present"].update({2: False}), "live_streak"),
    (lambda i: i["hosted"][0].update(headSha="b" * 40), "hosted_ci_at_candidate"),
    (lambda i: i["hosted"][0]["jobs"][1].update(conclusion="failure"), "hosted_ci_at_candidate"),
    (lambda i: _set(i, "hosted", []), "hosted_ci_at_candidate"),
    (lambda i: _set(i, "open_blocking", ["X-001"]), "no_open_p0_p1"),
    (lambda i: _set(i, "tracked_changes", [" M kriya/cli.py"]), "tracked_tree_clean"),
    (lambda i: i["changed_since_candidate"].append("kriya/cli.py"), "evidence_only_since_candidate"),
])
def test_any_failed_criterion_blocks_release(gate, mutate, criterion):
    inputs = _gate_inputs(gate)
    mutate(inputs)
    report = gate.evaluate(inputs)
    assert report["result"] == "NOT_VERIFIED" and not report["criteria"][criterion]["passed"]


@pytest.mark.parametrize("path,evidence", [
    ("handover/PRD-036_FINAL_CLOSURE.md", True), ("evidence/PRD-036/matrix/trial-1/junit.xml", True),
    ("docs/design.md", True), ("README.md", True), ("handover/BACKLOG_REGISTRY.csv", True),
    ("kriya/cli.py", False), ("scripts/certify.sh", False), ("tests/conftest.py", False), ("pyproject.toml", False),
    (".github/workflows/ci.yml", False), ("plugins/core_tools/__init__.py", False), ("kriya/README.md", False),
    ("Makefile", False),
])
def test_only_evidence_paths_may_change_after_the_candidate(gate, path, evidence):
    assert (gate.evidence_only([path]) == []) is evidence


def test_open_p0_p1_are_any_not_closed_blocking_priority(gate):
    rows = [{"id": "A", "priority": "P0", "status": "OPEN"}, {"id": "B", "priority": "P1", "status": "DEFERRED"},
            {"id": "C", "priority": "P1", "status": "CLOSED"}, {"id": "D", "priority": "P2", "status": "OPEN"}]
    assert gate.open_blocking_items(rows) == ["A", "B"]


def test_the_scripts_are_executable():
    for name in ("release_candidate.py", "prd036_canary.py", "prd036_canary.sh", "prd036_gate.py"):
        assert os.access(SCRIPTS / name, os.X_OK), name
