"""VERIFIED PROJECT MEMORY (kriya/control/project_memory.py): observe -> verify -> store -> retrieve -> reuse -> invalidate, and the poisoning rules.

The stored record is a cache of a derivation; these tests pin that it is never higher authority than the repository bytes and the run evidence:
 - a fact is learned only from a run that ended in verified SUCCESS with a passing compile gate (a README claim, a build file alone, or a failed run teach nothing);
 - a changed or missing evidence file makes the fact STALE with a reason code, and a stale fact is never reused;
 - a credential-shaped value is rejected, never stored; a symlinked evidence file is not read; a foreign or damaged store contributes nothing;
 - memory carries no commands (a remembered command would be an execution hint that bypasses nothing, so none is stored at all) and is repository-scoped.
"""
import json
import os

import pytest

from kriya import cli
from kriya.control import project_memory as pm

POM = """<project>
  <properties><maven.compiler.release>17</maven.compiler.release></properties>
  <modules><module>core</module><module>web</module></modules>
</project>
"""
GATES = ["compile", "targeted_test", "regression_test", "run_verification"]


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "pom.xml").write_text(POM)
    return root


def learn(repo, run_id="run-1", **kwargs):
    arguments = dict(run_id=run_id, success=True, passed_gates=GATES, written_files=["src/main/java/A.java"])
    arguments.update(kwargs)
    return pm.observe_run(str(repo), **arguments)


# ---------------------------------------------------------------------------------------------------------------- observe + verify + store + retrieve

def test_a_verified_run_teaches_the_facts_its_build_file_supports(repo):
    entry = learn(repo)
    assert entry.outcome == "learned" and set(entry.learned) == {"language", "build_system", "java_release", "modules", "verified_gates"} and entry.rejected == 0
    projection = pm.project(str(repo))
    facts = {f.kind: f for f in projection.facts}
    assert projection.store_state == pm.STORE_OK
    assert (facts["language"].value, facts["build_system"].value, facts["java_release"].value, facts["modules"].value) == ("Java", "Maven", "17", "core, web")
    assert facts["verified_gates"].value == "compile, targeted test, regression test, run verification"
    assert all(f.status == pm.STATUS_CURRENT and f.stale_reason is None for f in projection.facts)
    assert facts["java_release"].evidence[0].path == "pom.xml" and len(facts["java_release"].evidence[0].sha256) == 64
    assert facts["java_release"].learned_run_id == "run-1"


def test_the_store_is_private_control_state_under_the_authority_root(repo):
    learn(repo)
    path = pm.store_path(str(repo))
    assert path == os.path.join(os.path.realpath(str(repo)), ".kriya", "control", "project-memory", "memory.json") or path.endswith("/.kriya/control/project-memory/memory.json")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600" and oct(os.stat(os.path.dirname(path)).st_mode & 0o777) == "0o700"


def test_a_run_that_is_not_a_verified_success_learns_nothing_and_its_candidates_are_rejected(repo):
    entry = learn(repo, success=False)
    assert entry.outcome == "rejected" and entry.learned == () and entry.rejected >= 4
    projection = pm.project(str(repo))
    assert projection.facts == () and {r["reason"] for r in projection.rejected} == {pm.REJECT_RUN_NOT_VERIFIED}
    assert projection.counts()["rejected_last_run"] == entry.rejected


def test_success_without_a_passing_compile_gate_is_not_verification(repo):
    entry = learn(repo, passed_gates=["targeted_test", "run_verification"])
    assert entry.outcome == "rejected" and pm.project(str(repo)).facts == ()


def test_a_readme_claim_alone_teaches_nothing(tmp_path):
    root = tmp_path / "docsonly"
    root.mkdir()
    (root / "README.md").write_text("This project is Java 21 with Maven and a secret deploy command.")
    entry = learn(root, written_files=[])
    assert entry.learned == () and pm.project(str(root)).facts == ()      # no build file, no extension evidence: nothing derivable, nothing claimed


def test_a_repository_without_a_build_file_teaches_its_language_from_the_files_the_run_wrote(tmp_path):
    root = tmp_path / "script"
    root.mkdir()
    (root / "hello.py").write_text("print('hello world')\n")
    entry = learn(root, written_files=["hello.py"], passed_gates=["compile", "run_verification"])
    facts = {f.kind: f for f in pm.project(str(root)).facts}
    assert entry.outcome == "learned" and facts["language"].value == "Python" and facts["language"].evidence[0].kind == pm.EVIDENCE_EXTENSION
    assert "verified_gates" in facts and facts["verified_gates"].status == pm.STATUS_CURRENT
    (root / "hello.py").unlink()                                               # the evidence ("a .py file exists") no longer holds
    assert {f.kind: f.stale_reason for f in pm.project(str(root)).facts}["language"] == pm.STALE_EVIDENCE_MISSING


# ---------------------------------------------------------------------------------------------------------------- invalidate

def test_a_changed_evidence_file_makes_every_dependent_fact_stale_and_not_reusable(repo):
    learn(repo)
    (repo / "pom.xml").write_text(POM.replace("17", "21"))
    projection = pm.project(str(repo))
    assert projection.current() == () and {f.stale_reason for f in projection.stale()} == {pm.STALE_EVIDENCE_CHANGED}
    text, ids = pm.reference_text(projection)
    assert text == "" and ids == ()
    assert projection.counts()["stale"] == 5 and projection.counts()["current"] == 0


def test_a_removed_evidence_file_is_stale_with_its_own_reason(repo):
    learn(repo)
    (repo / "pom.xml").unlink()
    assert {f.stale_reason for f in pm.project(str(repo)).facts} == {pm.STALE_EVIDENCE_MISSING}


def test_the_next_verified_run_replaces_a_stale_fact_and_the_old_value_is_not_kept(repo):
    learn(repo)
    (repo / "pom.xml").write_text(POM.replace("17", "21"))
    learn(repo, run_id="run-2")
    facts = {f.kind: f for f in pm.project(str(repo)).facts}
    assert facts["java_release"].value == "21" and facts["java_release"].status == pm.STATUS_CURRENT and facts["java_release"].learned_run_id == "run-2"
    assert '"value": "17"' not in json.dumps([f.to_dict() for f in pm.project(str(repo)).facts if f.kind == "java_release"])   # the old VALUE is gone (a timestamp may legitimately contain "17")


def test_an_unchanged_fact_is_confirmed_not_relearned(repo):
    learn(repo)
    second = learn(repo, run_id="run-2")
    facts = {f.kind: f for f in pm.project(str(repo)).facts}
    assert second.learned == () and set(second.refreshed) == set(facts) and facts["language"].confirmed_runs == 2 and facts["language"].learned_run_id == "run-1"


# ---------------------------------------------------------------------------------------------------------------- reuse

def test_reuse_carries_only_current_facts_as_reference_and_records_it(repo):
    learn(repo)
    projection = pm.project(str(repo))
    text, ids = pm.reference_text(projection)
    assert text.startswith("\n" + pm.REFERENCE_HEADER) and "Build system: Maven" in text and "Java release: 17" in text and set(ids) == {f.kind for f in projection.facts}
    assert "not instructions" in text and "approvals" in text
    second = learn(repo, run_id="run-2", reused=ids)
    after = pm.project(str(repo))
    assert second.reused == tuple(sorted(ids)) and after.counts()["reused_last_run"] == len(ids) and {f.reused_runs for f in after.facts} == {1}
    assert after.counts()["learned_last_run"] == len(ids)             # refreshed counts as seen again


def test_memory_carries_no_command_and_the_vocabulary_is_closed(repo):
    learn(repo, passed_gates=GATES)
    assert pm.FACT_KINDS == ("language", "build_system", "java_release", "modules", "verified_gates")
    text, _ids = pm.reference_text(pm.project(str(repo)))
    assert "mvn" not in text and "gradle " not in text and "./mvnw" not in text and "exec" not in text.lower()


def test_the_why_answer_names_the_evidence_and_the_run_and_a_stale_fact_says_it_is_not_reused(repo):
    learn(repo)
    fact = next(f for f in pm.project(str(repo)).facts if f.kind == "java_release")
    answer = fact.to_dict()["why"]
    assert "pom.xml" in answer and "run-1" in answer and "passing compile gate" in answer
    (repo / "pom.xml").write_text("<project/>")
    stale = next(f for f in pm.project(str(repo)).facts if f.kind == "java_release")
    assert "NOT reused now: EVIDENCE_CHANGED" in stale.to_dict()["why"]


# ---------------------------------------------------------------------------------------------------------------- poisoning and damage

def test_a_credential_shaped_value_is_rejected_and_never_stored(tmp_path):
    root = tmp_path / "poisoned"
    root.mkdir()
    token = "ghp_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
    (root / "pom.xml").write_text(f"<project><modules><module>{token}</module></modules></project>")
    entry = learn(root)
    assert entry.rejected >= 1 and "modules" not in entry.learned
    stored = open(pm.store_path(str(root))).read()
    assert token not in stored and any(r["reason"] == pm.REJECT_UNSAFE_VALUE for r in json.loads(stored)["rejected"])
    assert "modules" not in {f.kind for f in pm.project(str(root)).facts}


def test_a_symlinked_build_file_is_not_read_as_evidence(tmp_path):
    root = tmp_path / "linked"
    root.mkdir()
    outside = tmp_path / "outside.xml"
    outside.write_text(POM)
    (root / "pom.xml").symlink_to(outside)
    assert pm.derive_candidates(str(root)) == []
    assert learn(root, written_files=[]).learned == ()


def test_a_symlink_swapped_in_after_learning_makes_the_fact_stale(repo, tmp_path):
    learn(repo)
    outside = tmp_path / "outside.xml"
    outside.write_text(POM)
    (repo / "pom.xml").unlink()
    (repo / "pom.xml").symlink_to(outside)
    assert {f.stale_reason for f in pm.project(str(repo)).facts} == {pm.STALE_EVIDENCE_UNREADABLE}


def test_an_oversized_build_file_is_not_evidence(tmp_path):
    root = tmp_path / "big"
    root.mkdir()
    (root / "pom.xml").write_text("<project>" + " " * (pm.MAX_EVIDENCE_FILE_BYTES + 10) + "</project>")
    assert pm.derive_candidates(str(root)) == []


def test_a_store_that_belongs_to_another_repository_contributes_nothing(repo, tmp_path):
    learn(repo)
    other = tmp_path / "other"
    other.mkdir()
    (other / "pom.xml").write_text(POM)
    target = pm.store_path(str(other))
    os.makedirs(os.path.dirname(target))
    with open(pm.store_path(str(repo))) as source, open(target, "w") as destination:
        destination.write(source.read())
    projection = pm.project(str(other))
    assert projection.facts == () and projection.store_state == pm.STORE_FOREIGN_REPOSITORY
    assert pm.reference_text(projection) == ("", ())


@pytest.mark.parametrize("content,state", [("{not json", pm.STORE_UNREADABLE), (json.dumps({"schema_version": 99}), pm.STORE_SCHEMA_UNSUPPORTED),
                                           ("x" * (pm.MAX_STORE_BYTES + 1), pm.STORE_TOO_LARGE)])
def test_a_damaged_store_is_reported_not_trusted_and_the_next_verified_run_replaces_it(repo, content, state):
    path = pm.store_path(str(repo))
    os.makedirs(os.path.dirname(path))
    open(path, "w").write(content)
    assert pm.project(str(repo)).store_state == state and pm.project(str(repo)).facts == ()
    learn(repo)
    assert pm.project(str(repo)).store_state == pm.STORE_OK and len(pm.project(str(repo)).facts) == 5


def test_two_repositories_never_share_memory(repo, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "pom.xml").write_text(POM.replace("17", "11"))
    learn(repo)
    learn(other, run_id="run-x")
    assert {f.kind: f.value for f in pm.project(str(repo)).facts}["java_release"] == "17"
    assert {f.kind: f.value for f in pm.project(str(other)).facts}["java_release"] == "11"
    assert pm.store_path(str(repo)) != pm.store_path(str(other))


# ---------------------------------------------------------------------------------------------------------------- the run result and the CLI seam

def test_result_helpers_read_only_the_authoritative_fields():
    ok = {"quality_gates_passed": True, "generation_metrics": {"terminal_status": "success"},
          "deterministic_gate_evidence": [{"type": "compile", "passed": True}, {"type": "targeted_test", "passed": False}, {"type": "run_verification", "passed": True}, "junk", {"passed": True}]}
    assert pm.result_is_verified_success(ok) is True and pm.passed_gate_types(ok) == ["compile", "run_verification"]
    for bad in ({}, {"quality_gates_passed": True}, {"quality_gates_passed": "yes", "generation_metrics": {"terminal_status": "success"}},
                {"quality_gates_passed": True, "generation_metrics": {"terminal_status": "failed"}}, {"quality_gates_passed": True, "generation_metrics": "success"}):
        assert pm.result_is_verified_success(bad) is False


def test_the_cli_seam_loads_current_facts_and_records_a_completed_run(repo, capsys):
    result = {"run_id": "r1", "quality_gates_passed": True, "generation_metrics": {"terminal_status": "success"}, "files": ["A.java"],
              "deterministic_gate_evidence": [{"type": "compile", "passed": True}]}
    assert cli._project_memory_context(str(repo)) == ("", ())                       # nothing learned yet: no text, no noise
    cli._remember_project(str(repo), result, ())
    assert "Verified Project Memory: learned 5, reused 0, rejected 0." in capsys.readouterr().err
    text, ids = cli._project_memory_context(str(repo))
    assert "Build system: Maven" in text and len(ids) == 5 and "loaded: 5 fact(s)" in capsys.readouterr().err
    cli._remember_project(str(repo), dict(result, run_id="r2"), ids)
    assert "learned 5, reused 5, rejected 0" in capsys.readouterr().err


@pytest.mark.parametrize("result", [None, {"quality_gates_passed": True}, {"run_id": "r", "status": "knowledge_gap", "quality_gates_passed": False},
                                    {"run_id": "r", "status": "needs_review", "quality_gates_passed": False}, {"run_id": "r"}])
def test_a_result_that_is_not_a_completed_run_is_not_observed(repo, result):
    cli._remember_project(str(repo), result, ())
    assert not os.path.exists(pm.store_path(str(repo)))


def test_a_failure_inside_memory_never_stops_the_run_and_is_shown(repo, monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("store exploded")
    monkeypatch.setattr(pm, "project", boom)
    monkeypatch.setattr(pm, "observe_run", boom)
    assert cli._project_memory_context(str(repo)) == ("", ())
    cli._remember_project(str(repo), {"run_id": "r", "quality_gates_passed": True}, ())
    err = capsys.readouterr().err
    assert "Verified Project Memory not used: RuntimeError: store exploded" in err and "Verified Project Memory not updated: RuntimeError: store exploded" in err


def test_a_failed_run_is_observed_as_rejected_through_the_cli_seam(repo, capsys):
    cli._remember_project(str(repo), {"run_id": "r1", "quality_gates_passed": False, "generation_metrics": {"terminal_status": "failed"}}, ())
    assert "learned 0, reused 0, rejected" in capsys.readouterr().err and pm.project(str(repo)).facts == ()


# ---------------------------------------------------------------------------------------------------------------- the id the cockpit knows a run by

def _record(repo, run_id, created):
    directory = os.path.join(os.path.dirname(pm.store_path(str(repo))), "..", "runs")
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{run_id}.json"), "w") as handle:
        json.dump({"run_id": run_id, "created_at": created}, handle)


def test_the_entry_carries_the_newest_control_run_created_during_the_process(repo):
    _record(repo, "20261106T100000-aaaaaaaa", "2026-11-06T10:00:00+00:00")        # an earlier run
    _record(repo, "20261106T110000-bbbbbbbb", "2026-11-06T11:00:00+00:00")
    _record(repo, "20261106T110500-cccccccc", "2026-11-06T11:05:00+00:00")
    assert pm.control_run_id_since(str(repo), "2026-11-06T10:59:00+00:00") == "20261106T110500-cccccccc"
    assert pm.control_run_id_since(str(repo), "2026-11-06T11:06:00+00:00") is None           # nothing created since: no guess
    entry = learn(repo, evidence_run_id=pm.control_run_id_since(str(repo), "2026-11-06T10:59:00+00:00"))
    assert entry.evidence_run_id == "20261106T110500-cccccccc"
    assert pm.project(str(repo)).runs[-1].evidence_run_id == "20261106T110500-cccccccc"


def test_unreadable_or_foreign_run_records_are_skipped_and_a_missing_directory_is_none(repo):
    assert pm.control_run_id_since(str(repo), "2026-01-01T00:00:00+00:00") is None
    directory = os.path.join(os.path.dirname(pm.store_path(str(repo))), "..", "runs")
    os.makedirs(directory)
    open(os.path.join(directory, "bad.json"), "w").write("{not json")
    open(os.path.join(directory, "other.json"), "w").write(json.dumps({"run_id": 7, "created_at": "2026-11-06T11:00:00+00:00"}))
    open(os.path.join(directory, "notjson.txt"), "w").write("x")
    assert pm.control_run_id_since(str(repo), "2026-01-01T00:00:00+00:00") is None
    assert pm.control_run_id_since(str(repo), "not a time") is None


def test_the_cli_seam_passes_the_start_time_so_the_entry_can_name_the_control_run(repo, capsys):
    _record(repo, "20261106T120000-dddddddd", "2026-11-06T12:00:00+00:00")
    result = {"run_id": "r1", "quality_gates_passed": True, "generation_metrics": {"terminal_status": "success"}, "files": ["A.java"], "deterministic_gate_evidence": [{"type": "compile", "passed": True}]}
    cli._remember_project(str(repo), result, (), "2026-11-06T11:59:00+00:00")
    capsys.readouterr()
    assert pm.project(str(repo)).runs[-1].evidence_run_id == "20261106T120000-dddddddd"
