"""PRD-035 revision binding (scripts/verify_model_certification.py + tests/_model_certification.py::kriya_identity).

The model-certification record key (model, runtime digest, settings digest, environment digest, case-set version) never named the Kriya executable. The
report ``content`` is the existing content-digested evidence and the stored record seals it (``record.report_digest``), so the commit, tree, ``kriya/``
and ``plugins/`` subtrees, tracked-worktree-clean state and production-configuration digest are bound INTO the report content, and the verifier refuses
any mismatch. Each negative control changes exactly one thing, re-digests the report and re-seals the record, so only the binding check can catch it.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from _model_certification import CASES, build_report, kriya_identity

from kriya.core import model_certification as mc

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("verify_model_certification", ROOT / "scripts" / "verify_model_certification.py")
verifier = importlib.util.module_from_spec(_spec)
sys.modules["verify_model_certification"] = verifier
_spec.loader.exec_module(verifier)

IDENTITY = {"model": "m:1", "runtime_fingerprint": "rt", "runtime_exact": "True", "qualification": "QUALIFIED", "inference_settings_digest": "set"}
ENVIRONMENT = {"digest": "sha256:env", "exact": True, "accelerator_model": "M1 Max", "system_memory_class_gib": 64}
KEY = mc.CertificationKey(model="m:1", runtime_digest="rt", inference_settings_digest="set", environment_digest="sha256:env")


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", "-c", "maintenance.auto=false", "-c", "gc.auto=0", *args],
                          capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    (root / "kriya").mkdir(parents=True)
    (root / "plugins").mkdir()
    (root / "kriya" / "a.py").write_text("A = 1\n")
    (root / "plugins" / "b.py").write_text("B = 1\n")
    (root / "notes.txt").write_text("n\n")
    _git(root.parent, "init", "-q", str(root))
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "baseline")
    return root


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "production.yaml"
    path.write_text("llm:\n  model: m:1\n")
    return path


def _report(repo, config_file, *, mutate=None, end_mutate=None):
    start = kriya_identity(str(repo), str(config_file))
    end = dict(start)
    if mutate:
        mutate(start)
    if end_mutate:
        end_mutate(end)
    elif mutate:
        mutate(end)
    results = [{"case_id": case_id, "verdict": "PASSED", "record": {"identity": IDENTITY, "environment": ENVIRONMENT, "final_success": True}}
               for case_id, _ in CASES]
    return build_report(results, {"start": start, "end": end})


def _publish(tmp_path, report, *, store=True):
    path = tmp_path / "model-certification.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True))
    if store:
        mc.save_certification(KEY, report)
    return str(path)


def _verify(tmp_path, repo, config_file, report, *, store=True, **kwargs):
    path = _publish(tmp_path, report, store=store)
    return verifier.verify(path, str(repo), str(config_file), **kwargs)


def _redigest(report):
    return {"content": report["content"], "content_digest": mc._content_digest(report["content"])}


def test_the_identity_names_the_commit_the_trees_the_clean_state_and_the_configuration(repo, config_file):
    identity = kriya_identity(str(repo), str(config_file))
    assert identity["commit"] == _git(repo, "rev-parse", "HEAD") and identity["tree"] == _git(repo, "rev-parse", "HEAD^{tree}")
    assert identity["kriya_tree"] == _git(repo, "rev-parse", "HEAD:kriya") and identity["plugins_tree"] == _git(repo, "rev-parse", "HEAD:plugins")
    assert identity["tracked_worktree_clean"] is True and len(identity["config"]["sha256"]) == 64
    (repo / "notes.txt").write_text("changed\n")
    assert kriya_identity(str(repo), str(config_file))["tracked_worktree_clean"] is False
    (repo / "notes.txt").write_text("n\n")
    (repo / "untracked.txt").write_text("x\n")
    assert kriya_identity(str(repo), str(config_file))["tracked_worktree_clean"] is True  # untracked output (certification-out/) never blocks a run


def test_the_bound_report_and_its_record_verify(tmp_path, repo, config_file):
    verdict = _verify(tmp_path, repo, config_file, _report(repo, config_file))
    assert verdict["verdict"] == verifier.BOUND and verdict["problems"] == []
    assert verdict["bound_to"]["commit"] == _git(repo, "rev-parse", "HEAD")


def test_the_identity_is_part_of_the_content_digest():
    plain = build_report([])
    bound = build_report([], {"start": {"commit": "a"}, "end": {"commit": "a"}})
    assert "kriya_identity" not in plain["content"] and bound["content"]["kriya_identity"]["start"] == {"commit": "a"}
    assert bound["content_digest"] != plain["content_digest"]
    other = build_report([], {"start": {"commit": "b"}, "end": {"commit": "b"}})
    assert other["content_digest"] != bound["content_digest"]


def _swap_commit(repo):
    (repo / "plugins" / "b.py").write_text("B = 2\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.parametrize("name,mutate,expected", [
    ("only the recorded tree", lambda i: i.update(tree="0" * 40), "tree_mismatch"),
    ("only the recorded kriya/ tree", lambda i: i.update(kriya_tree="1" * 40), "kriya_tree_mismatch"),
    ("only the recorded plugins/ tree", lambda i: i.update(plugins_tree="2" * 40), "plugins_tree_mismatch"),
    ("a commit git has never seen", lambda i: i.update(commit="3" * 40), "recorded_commit_unknown_to_git"),
    ("a dirty tracked worktree at the start", lambda i: i.update(tracked_worktree_clean=False), "worktree_not_clean_at_start"),
    ("a changed production configuration digest", lambda i: i["config"].update(sha256="4" * 64), "production_config_digest_mismatch"),
    ("no configuration digest", lambda i: i.update(config=None), "production_config_digest_not_recorded"),
])
def test_each_single_field_negative_control_is_refused_even_when_the_report_is_redigested_and_the_record_resealed(
        tmp_path, repo, config_file, name, mutate, expected):
    verdict = _verify(tmp_path, repo, config_file, _redigest(_report(repo, config_file, mutate=mutate)))
    assert verdict["verdict"] == verifier.REFUSED and expected in verdict["problems"], (name, verdict)


def test_a_real_but_different_commit_with_the_original_trees_is_refused(tmp_path, repo, config_file):
    original = _report(repo, config_file)
    other = _swap_commit(repo)
    # the checkout moved on; the report still names the first commit: strict verification refuses, historical verification checks git objects only
    assert verifier.verify(_publish(tmp_path, original), str(repo), str(config_file))["problems"] == ["checkout_is_not_the_recorded_commit"]
    assert verifier.verify(_publish(tmp_path, original), str(repo), str(config_file), historical=True)["verdict"] == verifier.BOUND
    # ... and a report that swaps ONLY the commit to the real second commit (trees untouched) fails on the trees
    swapped = _redigest(_report(repo, config_file, mutate=lambda i: i.update(commit=other, tree=original["content"]["kriya_identity"]["start"]["tree"])))
    assert "tree_mismatch" in _verify(tmp_path, repo, config_file, swapped)["problems"]


def test_a_changed_head_alone_is_refused(tmp_path, repo, config_file):
    """The report of the first commit with ONLY its commit field pointing at a real, different commit (the checkout is on that commit)."""
    original = _report(repo, config_file)
    other = _swap_commit(repo)
    swapped = json.loads(json.dumps(original))
    for side in ("start", "end"):
        swapped["content"]["kriya_identity"][side]["commit"] = other
    verdict = _verify(tmp_path, repo, config_file, _redigest(swapped))
    assert verdict["verdict"] == verifier.REFUSED and {"tree_mismatch", "plugins_tree_mismatch"} <= set(verdict["problems"]), verdict


def test_an_identity_that_changed_during_the_matrix_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file, end_mutate=lambda i: i.update(commit="5" * 40))
    assert "identity_changed_during_the_matrix" in _verify(tmp_path, repo, config_file, _redigest(report))["problems"]


def test_a_report_without_the_identity_block_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    del report["content"]["kriya_identity"]
    assert _verify(tmp_path, repo, config_file, _redigest(report))["problems"] == ["kriya_identity_not_recorded"]


def test_an_edited_report_that_was_not_redigested_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    report["content"]["kriya_identity"]["start"]["commit"] = "6" * 40
    report["content"]["kriya_identity"]["end"]["commit"] = "6" * 40
    assert "report_digest_mismatch" in _verify(tmp_path, repo, config_file, report)["problems"]


def test_a_record_that_seals_another_report_or_is_missing_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    other = build_report([], {"start": {"commit": "x"}, "end": {"commit": "x"}})
    other["content"]["status"] = "CERTIFIED"
    mc.save_certification(KEY, {"content": {"status": "CERTIFIED", "cases": []}, "content_digest": other["content_digest"]})
    assert "record_does_not_seal_this_report" in _verify(tmp_path, repo, config_file, report, store=False)["problems"]


def test_a_missing_record_is_refused(tmp_path, repo, config_file):
    assert "record_missing_or_invalid" in _verify(tmp_path, repo, config_file, _report(repo, config_file), store=False)["problems"]


def test_the_wrong_or_missing_configuration_file_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    path = _publish(tmp_path, report)
    other = tmp_path / "other.yaml"
    other.write_text("llm:\n  model: other\n")
    assert "production_config_digest_mismatch" in verifier.verify(path, str(repo), str(other))["problems"]
    assert "production_config_not_supplied" in verifier.verify(path, str(repo), None)["problems"]


def test_a_dirty_worktree_now_or_a_wrong_case_set_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    path = _publish(tmp_path, report)
    (repo / "notes.txt").write_text("changed\n")
    assert "worktree_not_clean_now" in verifier.verify(path, str(repo), str(config_file))["problems"]
    bad = _report(repo, config_file)
    bad["content"]["case_set_version"] = 2
    assert "case_set_version_mismatch" in _verify(tmp_path, repo, config_file, _redigest(bad))["problems"]


def test_a_failed_case_or_a_non_target_tier_is_refused(tmp_path, repo, config_file):
    report = _report(repo, config_file)
    report["content"]["cases"][5]["verdict"] = "FAILED"
    problems = _verify(tmp_path, repo, config_file, _redigest(report))["problems"]
    assert "not_all_eleven_cases_passed" in problems


def test_the_cli_exits_nonzero_on_a_refusal_and_zero_on_a_bound_certification(tmp_path, repo, config_file, capsys):
    good = _publish(tmp_path, _report(repo, config_file))
    assert verifier.main(["--report", good, "--repo", str(repo), "--config", str(config_file), "--out", str(tmp_path / "v.json")]) == 0
    assert json.loads((tmp_path / "v.json").read_text())["verdict"] == verifier.BOUND
    assert verifier.main(["--report", good, "--repo", str(repo)]) == 1  # no configuration supplied: refused
    capsys.readouterr()


def test_the_session_hooks_stamp_the_identity_and_refuse_a_dirty_start(tmp_path, repo, config_file, monkeypatch):
    import conftest

    monkeypatch.setenv("KRIYA_RELEASE_CONFIG", str(config_file))
    session = SimpleNamespace(config=SimpleNamespace(getoption=lambda name: str(tmp_path / "out") if name == "--model-certification" else None,
                                                     rootpath=repo, stash=pytest.Stash()))
    conftest.pytest_sessionstart(session)
    start = session.config.stash[conftest._KRIYA_IDENTITY_START]  # noqa: SLF001
    assert start["commit"] == _git(repo, "rev-parse", "HEAD") and start["config"]["sha256"]
    (repo / "notes.txt").write_text("dirty\n")
    with pytest.raises(pytest.exit.Exception):
        conftest.pytest_sessionstart(SimpleNamespace(config=SimpleNamespace(
            getoption=lambda name: str(tmp_path / "out") if name == "--model-certification" else None, rootpath=repo, stash=pytest.Stash())))


def test_certify_model_sh_runs_the_verifier_and_fails_closed():
    script = (ROOT / "scripts" / "certify_model.sh").read_text()
    assert "scripts/verify_model_certification.py" in script and "|| status=1" in script and '--config "$KRIYA_RELEASE_CONFIG"' in script
