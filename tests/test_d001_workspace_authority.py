"""D-001 (owner decision 2026-10-23): a workspace's AUTHORITY is its canonical repository ROOT, not the directory a command was invoked from.

MEASURED before the fix (RDCG-D-001, reproduced on canonical, two runs): a second mutating run took a second lock from ``<repo>/sub`` while ``<repo>`` held one;
after a crash mid-commit in ``<repo>/sub``, ``kriya runs status --workspace <repo>`` printed CLEAN and a new run started in ``<repo>``. Every workspace-scoped
authority (the run lock, the identity, the RunRecords, launches, commit evidence, checkpoints, milestone sidecars) keyed on the path AS GIVEN.

The rule now: ``<repo>`` and ``<repo>/sub`` (and every alias of either) share ONE lock, ONE identity and ONE set of control stores; a genuinely nested repository
or linked work tree resolves to itself; Kriya-owned directories (``.kriya``, gate views) are never searched through. What is NOT changed: the OPERATING root
(the directory whose files a run reads and writes, its sandbox and its scratch) stays the one that was invoked."""
import json
import os
import subprocess
import sys

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.context.workspace_identity import (
    authority_root,
    canonical_workspace,
    find_repository_root,
    workspace_identities,
    workspace_identity,
)
from kriya.control.commit_state import REASON_NESTED_WORKSPACE_STATE, assess_workspace_commit_state
from kriya.control.persistence import list_run_records, nested_control_state, save_run_record
from kriya.control.run_ownership import WorkspaceLockHeldError, acquire_run_lock, probe_run_lock
from kriya.control.run_record import RunLifecycle, RunRecord


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def world(tmp_path):
    """One repository with a sub-directory, a nested non-repository directory, a genuine nested repository, a linked work tree, and symlink aliases."""
    base = tmp_path.resolve()
    repo = base / "repo"
    (repo / "sub" / "inner").mkdir(parents=True)
    (repo / "nested").mkdir()
    (repo / "a.txt").write_text("x")
    (repo / "sub" / "b.txt").write_text("y")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "i")
    (repo / "nested" / "n.txt").write_text("n")
    _git(repo / "nested", "init", "-q")
    _git(repo / "nested", "add", "-A")
    _git(repo / "nested", "commit", "-qm", "i")
    os.symlink(repo, base / "repo_link")
    os.symlink(repo / "sub", base / "sub_link")
    _git(repo, "worktree", "add", "-q", "--detach", str(base / "wt"))
    _git(repo, "worktree", "add", "-q", "--detach", str(repo / "inner_wt"))   # a linked work tree INSIDE the repository's own tree: its .git is a FILE
    return {
        "base": base, "repo": repo, "sub": repo / "sub", "inner": repo / "sub" / "inner", "nested": repo / "nested", "wt": base / "wt", "inner_wt": repo / "inner_wt",
        "lexical": repo / "." / "sub" / "..", "repo_link": base / "repo_link", "sub_link": base / "sub_link",
    }


SAME_AUTHORITY = ["repo", "lexical", "repo_link", "sub", "sub_link", "inner"]
OTHER_AUTHORITY = ["nested", "wt", "inner_wt"]


# --- the resolver ----------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("name", SAME_AUTHORITY)
def test_every_path_inside_one_repository_resolves_to_its_root(world, name):
    assert authority_root(str(world[name])) == canonical_workspace(str(world["repo"]))


@pytest.mark.parametrize("name", OTHER_AUTHORITY)
def test_a_genuine_nested_repository_and_a_linked_work_tree_resolve_to_themselves(world, name):
    assert authority_root(str(world[name])) == canonical_workspace(str(world[name])) != canonical_workspace(str(world["repo"]))


def test_a_path_in_no_repository_is_its_own_root(tmp_path):
    plain = tmp_path / "plain" / "deeper"
    plain.mkdir(parents=True)
    assert authority_root(str(plain)) == canonical_workspace(str(plain))


def test_identity_is_the_authoritys_and_only_the_operating_root_differs(world):
    ids = {name: workspace_identity(str(world[name])) for name in SAME_AUTHORITY}
    assert len(set(ids.values())) == 1
    assert workspace_identity(str(world["nested"])) != ids["repo"] != workspace_identity(str(world["wt"]))
    assert canonical_workspace(str(world["sub"])) != canonical_workspace(str(world["repo"]))  # the OPERATING root is untouched
    assert ids["repo"] in workspace_identities(str(world["sub"]))


def test_a_state_written_under_the_repository_root_keeps_its_identity(world):
    """The root's identity is the one it always had, so existing root state needs no migration."""
    import hashlib

    assert workspace_identity(str(world["repo"])) == hashlib.sha256(canonical_workspace(str(world["repo"])).encode("utf-8")).hexdigest()


# --- Kriya-owned directories are never searched through ------------------------------------------------------------------------------

def test_a_directory_beneath_dot_kriya_never_takes_the_repositorys_identity(world):
    candidate = world["repo"] / ".kriya" / "candidates" / "candidate-x"
    candidate.mkdir(parents=True)
    assert authority_root(str(candidate)) == canonical_workspace(str(candidate)) != canonical_workspace(str(world["repo"]))
    assert find_repository_root(canonical_workspace(str(candidate))) is None


def test_a_managed_worktree_and_a_scoped_snapshot_with_their_own_git_marker_are_their_own_roots(world):
    managed = world["repo"] / ".kriya" / "worktree"
    managed.mkdir(parents=True)
    (managed / ".git").write_text("gitdir: /elsewhere\n")          # a linked work tree's .git FILE
    snapshot = world["repo"] / ".kriya" / "scoped-worktree"
    (snapshot / ".git" / "objects").mkdir(parents=True)             # the scoped snapshot's real discovery boundary (a .git directory)
    for sandbox in (managed, snapshot):
        assert authority_root(str(sandbox)) == canonical_workspace(str(sandbox))
        assert workspace_identity(str(sandbox)) != workspace_identity(str(world["repo"]))


def test_a_gate_view_never_takes_the_identity_of_a_repository_the_state_root_sits_in(world):
    view = world["repo"] / "state" / "gate-views" / "view-0123456789abcdef"
    view.mkdir(parents=True)
    assert authority_root(str(view)) == canonical_workspace(str(view)) != canonical_workspace(str(world["repo"]))


def test_the_gate_view_walker_and_the_identity_walker_are_one_function(world):
    from kriya.tools import gate_view

    assert gate_view.is_git_work_tree(str(world["sub"])) is True
    assert gate_view.is_git_work_tree(str(world["repo"] / ".kriya")) is False  # Kriya-owned: not a work tree to be a view of


# --- locks: one authority, one lock ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("held", ["repo", "sub", "inner", "repo_link"])
def test_while_one_path_of_a_repository_holds_the_lock_no_other_path_of_it_can_take_a_second(world, held):
    with acquire_run_lock(str(world[held])):
        for contender in SAME_AUTHORITY:
            with pytest.raises(WorkspaceLockHeldError):
                with acquire_run_lock(str(world[contender]), "contender"):
                    pass
        assert probe_run_lock(str(world["sub"])) is not None and probe_run_lock(str(world["repo"])) is not None  # and each sees the holder
        for other in OTHER_AUTHORITY:                                    # a different authority is not blocked by it
            with acquire_run_lock(str(world[other]), "independent"):
                pass
    assert probe_run_lock(str(world["sub"])) is None and probe_run_lock(str(world["repo"])) is None


def test_there_is_exactly_one_lock_file_for_the_repository(world):
    for name in ("sub", "inner", "repo", "repo_link"):
        with acquire_run_lock(str(world[name])):
            pass
    found = subprocess.run(["find", str(world["repo"]), "-name", "run.lock", "-not", "-path", "*/nested/*"], capture_output=True, text=True).stdout.split()
    assert found == [str(world["repo"] / ".kriya" / "run.lock")]


def test_two_real_processes_from_the_root_and_a_subdirectory_cannot_both_own_the_repository(world):
    """The RDCG-D-001 p1 reproducer, with two real processes: A owns ``<repo>``; B from ``<repo>/sub`` must be refused."""
    child = ("import sys, time\nfrom kriya.control.run_coordinator import begin_mutating_run\n"
             "with begin_mutating_run(sys.argv[1]) as ctx:\n    print('ACQUIRED', flush=True)\n    sys.stdin.readline()\n")
    holder = subprocess.Popen([sys.executable, "-c", child, str(world["repo"])], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "ACQUIRED"
        second = subprocess.run([sys.executable, "-c", child, str(world["sub"])], capture_output=True, text=True, input="\n", timeout=60)
        assert second.returncode != 0 and "WorkspaceLockHeldError" in second.stderr and "ACQUIRED" not in second.stdout
    finally:
        holder.stdin.write("\n")
        holder.stdin.close()
        holder.wait(timeout=30)


# --- one set of control stores ---------------------------------------------------------------------------------------------------------

def test_a_run_started_from_a_subdirectory_leaves_all_control_state_at_the_repository_root(world):
    from kriya.control.run_coordinator import begin_mutating_run

    with begin_mutating_run(str(world["sub"])) as context:
        run_id = context.run_id
        assert context.workspace_id == workspace_identity(str(world["repo"]))
    kriya_dirs = subprocess.run(["find", str(world["repo"]), "-name", ".kriya", "-not", "-path", "*/nested/*"], capture_output=True, text=True).stdout.split()
    assert kriya_dirs == [str(world["repo"] / ".kriya")]              # nothing under sub/.kriya, nothing under inner/.kriya
    for name in SAME_AUTHORITY:                                        # and every path of the repository reads the same record
        assert [record.run_id for record in list_run_records(str(world[name]))] == [run_id]
    assert list_run_records(str(world["nested"])) == [] and list_run_records(str(world["wt"])) == []


def _crash_mid_commit(workspace):
    script = ("import os, sys\nfrom kriya.control.run_coordinator import begin_mutating_run, transition_mutating_run, begin_run_commit\n"
              "from kriya.control.run_record import RunLifecycle\n"
              "with begin_mutating_run(sys.argv[1]) as ctx:\n    transition_mutating_run(ctx, RunLifecycle.RUNNING)\n"
              "    begin_run_commit(sys.argv[1], 'tx-crash-1', candidate_hash='0' * 64)\n    os._exit(9)\n")
    assert subprocess.run([sys.executable, "-c", script, str(workspace)], capture_output=True).returncode == 9


@pytest.mark.parametrize("crashed_in,observed_from", [("sub", "repo"), ("repo", "sub"), ("inner", "repo"), ("sub", "sub_link"), ("repo", "repo_link")])
def test_a_commit_left_unsettled_by_a_crash_blocks_every_path_of_the_repository(world, crashed_in, observed_from):
    """The RDCG-D-001 p2 reproducer: the crash in one directory used to leave the repository CLEAN and startable from another."""
    from kriya.control.commit_state import UncertainWorkspaceStateError
    from kriya.control.run_coordinator import begin_mutating_run

    _crash_mid_commit(world[crashed_in])
    assessment = assess_workspace_commit_state(str(world[observed_from]))
    assert not assessment.safe and assessment.uncertain_run_ids
    with pytest.raises(UncertainWorkspaceStateError):
        with begin_mutating_run(str(world[observed_from])):
            pytest.fail("a run started in a workspace with an unsettled commit")
    status = CliRunner().invoke(main, ["runs", "status", "--workspace", str(world[observed_from]), "--json"])
    assert status.exit_code == 1 and json.loads(status.output)["status"] != "CLEAN" and json.loads(status.output)["records"]


def test_a_nested_repository_does_not_inherit_the_enclosing_repositorys_unsettled_state(world):
    _crash_mid_commit(world["repo"])
    assert not assess_workspace_commit_state(str(world["repo"])).safe
    assert assess_workspace_commit_state(str(world["nested"])).safe and assess_workspace_commit_state(str(world["wt"])).safe


def test_the_enclosing_repository_does_not_scan_into_a_nested_repositorys_own_state(world):
    _crash_mid_commit(world["nested"])
    assert assess_workspace_commit_state(str(world["repo"])).safe
    assert not assess_workspace_commit_state(str(world["nested"])).safe


# --- state a build from before the rule left under a sub-directory must not make the repository look CLEAN ------------------------------

def _save_in_the_old_layout(monkeypatch, directory, steps):
    """Persist a RunRecord exactly where the earlier build did: under the invoked directory's own .kriya/control (its key was the path as given)."""
    import kriya.control.persistence as persistence

    with monkeypatch.context() as old_layout:
        old_layout.setattr(persistence, "authority_root", canonical_workspace)
        record = None
        for step in steps:
            record, previous = step(record), record
            save_run_record(str(directory), record, expected_revision=None if previous is None else previous.revision)
    return record


def _plant_pre_rule_record(world, monkeypatch, directory, run_id="20260101T000000-deadbeef"):
    _save_in_the_old_layout(monkeypatch, directory, [
        lambda _r: RunRecord.new(run_id, workspace_identity(str(directory)), "0" * 40, "1" * 40),
        lambda r: r.transition(RunLifecycle.RUNNING),
        lambda r: r.begin_commit("tx-old", intent="APPLY_VERIFIED_CANDIDATE", candidate_hash="0" * 64),
    ])
    assert (directory / ".kriya" / "control" / "runs" / f"{run_id}.json").is_file()
    return run_id


def test_unsettled_state_an_earlier_build_left_under_a_subdirectory_blocks_the_repository(world, monkeypatch):
    _plant_pre_rule_record(world, monkeypatch, world["sub"])
    assert list_run_records(str(world["repo"])) == []                                       # the authority's own stores do not hold it
    for name in ("repo", "sub", "inner"):
        assessment = assess_workspace_commit_state(str(world[name]))
        assert not assessment.safe and REASON_NESTED_WORKSPACE_STATE in assessment.reason_codes
        assert [directory for directory, _reason in assessment.nested_workspace_state] == [str(world["sub"])]
        assert "earlier Kriya" in assessment.describe() and str(world["sub"]) in assessment.describe()
    payload = assess_workspace_commit_state(str(world["repo"])).to_payload("r1")
    assert payload["status"] == "needs_review" and payload["nested_workspace_state"][0]["directory"] == str(world["sub"])
    status = CliRunner().invoke(main, ["runs", "status", "--workspace", str(world["repo"]), "--json"])
    assert status.exit_code == 1


def test_settled_history_under_a_subdirectory_is_not_a_block_and_a_clean_repository_scans_complete(world, monkeypatch):
    run_id = "20260101T000000-cafe0001"
    _save_in_the_old_layout(monkeypatch, world["sub"], [
        lambda _r: RunRecord.new(run_id, workspace_identity(str(world["sub"])), "0" * 40, "1" * 40),
        lambda r: r.transition(RunLifecycle.RUNNING),
        lambda r: r.transition(RunLifecycle.FAILURE),
    ])
    assert (world["sub"] / ".kriya" / "control" / "runs" / f"{run_id}.json").is_file()
    assessment = assess_workspace_commit_state(str(world["repo"]))
    assert assessment.safe and assessment.nested_workspace_state == () and assessment.nested_scan_complete is True


def test_an_unreadable_record_under_a_subdirectory_fails_closed(world):
    runs = world["inner"] / ".kriya" / "control" / "runs"
    runs.mkdir(parents=True)
    (runs / "20260101T000000-badbad00.json").write_text("{ not json")
    found, complete = nested_control_state(str(world["repo"]))
    assert complete and found and "unreadable" in found[0][1]
    assert not assess_workspace_commit_state(str(world["repo"])).safe


def _plant_pre_rule_commit_evidence(world, state, transaction_id="tx-old"):
    from kriya.context.workspace_identity import ownership_metadata
    from kriya.repository.commit import CommitEvidence

    commits = world["sub"] / ".kriya" / "control" / "commits"
    commits.mkdir(parents=True)
    payload = CommitEvidence(2, transaction_id, state, 1.0, 2.0, (), {}).to_dict()
    payload["_workspace"] = ownership_metadata(str(world["repo"]))
    (commits / f"{transaction_id}.json").write_text(json.dumps(payload))


@pytest.mark.parametrize("state_name", ["IN_PROGRESS", "UNCERTAIN"])
def test_readable_unsettled_commit_evidence_under_a_subdirectory_is_reported_with_its_state_not_as_unreadable(world, state_name):
    """Rule-4 normal output of the legacy scan's broad catch (nested_control_state): a READABLE document is reported as what it is."""
    from kriya.repository.commit import CommitState

    _plant_pre_rule_commit_evidence(world, CommitState[state_name])
    found, complete = nested_control_state(str(world["repo"]))
    assert complete and [directory for directory, _reason in found] == [str(world["sub"])]
    reason = found[0][1]
    assert f"tx-old.json: commit {state_name.lower()}" in reason and "unreadable" not in reason
    assert not assess_workspace_commit_state(str(world["repo"])).safe


def test_settled_commit_evidence_under_a_subdirectory_is_not_a_block(world):
    from kriya.repository.commit import CommitState

    _plant_pre_rule_commit_evidence(world, CommitState.COMMITTED)
    found, complete = nested_control_state(str(world["repo"]))
    assert complete and found == ()
    assert assess_workspace_commit_state(str(world["repo"])).safe


def test_the_legacy_scan_is_bounded_and_says_when_it_did_not_finish(world, monkeypatch):
    import kriya.control.persistence as persistence

    monkeypatch.setattr(persistence, "LEGACY_SCAN_DIRECTORY_BUDGET", 1)
    found, complete = nested_control_state(str(world["repo"]))
    assert complete is False and found == ()
    assert assess_workspace_commit_state(str(world["repo"])).nested_scan_complete is False


# --- the other authority stores follow the same root --------------------------------------------------------------------------------------

def test_checkpoints_and_milestone_sidecars_and_commit_evidence_are_the_authoritys(world):
    from kriya.control.checkpoint import checkpoint_path
    from kriya.repository.commit import commit_evidence_dir
    from kriya.workflow.milestones import _sidecar_path

    root = world["repo"].resolve()
    for name in ("sub", "inner", "sub_link"):
        assert checkpoint_path(str(world[name]), "run1") == str(root / ".kriya" / "checkpoints" / "run1.json")
        assert _sidecar_path(str(world[name]), "g1") == str(root / ".kriya" / "milestones" / "g1.json")
        assert commit_evidence_dir(str(world[name])) == str(root / ".kriya" / "control" / "commits")
    assert checkpoint_path(str(world["nested"]), "run1").startswith(str(world["nested"].resolve()))


def test_the_control_store_writer_accepts_the_authority_and_the_operating_kriya_directory_and_nothing_else(world):
    from kriya.control.control_store import ControlStorePathError, write_control_file
    from kriya.repository.snapshot import raw_digest

    absent = raw_digest(b"")
    sub = str(world["sub"])
    authority_file = world["repo"] / ".kriya" / "control" / "x.json"
    operating_file = world["sub"] / ".kriya" / "milestones" / "x.json"
    for path in (authority_file, operating_file):
        path.parent.mkdir(parents=True, exist_ok=True)
        write_control_file(sub, str(path), "{}", expected_revision=absent)
        assert path.read_text() == "{}"
    for outside in (world["repo"] / "a.txt", world["sub"] / "b.txt", world["nested"] / ".kriya" / "control" / "x.json"):
        with pytest.raises(ControlStorePathError):
            write_control_file(sub, str(outside), "{}", expected_revision=raw_digest(outside.read_bytes()) if outside.exists() else absent)


# --- the operating root is untouched -------------------------------------------------------------------------------------------------------

def test_the_operating_directory_of_a_subdirectory_run_is_still_the_subdirectory(world):
    from kriya.control.run_coordinator import begin_mutating_run, owning_run

    with begin_mutating_run(str(world["sub"])) as context:
        assert context.workspace_path == canonical_workspace(str(world["sub"]))      # what the run reads and writes
        assert owning_run(str(world["sub"])) is context
        assert owning_run(str(world["repo"])) is None                                  # a different OPERATING root is not this run's workspace


def test_a_real_engine_run_invoked_at_a_subdirectory_keeps_every_authority_artifact_at_the_repository_root(tmp_path):
    """The discriminating measurement: the real direct pipeline, invoked at ``<repo>/sub``. It still operates on the subdirectory (its files are
    sub-relative) but the lock, the RunRecord, the commit evidence and the checkpoints land at the repository root, and nothing authoritative is left
    under ``sub/.kriya``."""
    from _chaos_harness import (
        CALC,
        CALC_WITH_SUB,
        TEST_SUB,
        ChaosRuntime,
        RuntimeRegistration,
        benign_roles,
        chaos_config,
        chaos_engine,
        git_workspace,
        run_direct,
    )

    repo = git_workspace(tmp_path, {"README.md": "x\n", "sub/calc.py": CALC, "sub/test_calc.py": TEST_SUB})
    sub = repo / "sub"
    runtime = ChaosRuntime(lambda role, request: CALC_WITH_SUB if role == "developer" else benign_roles(role, request))
    with RuntimeRegistration(runtime):
        result = run_direct(chaos_engine(chaos_config()), "add sub to calc.py", sub)
    assert result["quality_gates_passed"] is True and result["files"] == ["calc.py"]            # the operating root is still the subdirectory
    root_kriya = repo / ".kriya"
    assert (root_kriya / "run.lock").is_file() and list((root_kriya / "control" / "runs").glob("*.json")) and list((root_kriya / "control" / "commits").glob("*.json"))
    leftovers = sorted(path.name for path in (sub / ".kriya").iterdir()) if (sub / ".kriya").exists() else []
    assert not {"control", "run.lock", "checkpoints", "milestones"} & set(leftovers), leftovers
    assert [record.run_id for record in list_run_records(str(repo))] == [record.run_id for record in list_run_records(str(sub))] and list_run_records(str(sub))


# --- configuration: ONE repository, ONE configuration (owner decision 2026-10-23) ----------------------------------------------------
# MEASURED before the fix (closure/d001/config_probe_run2.txt): with ``<repo>/kriya.yaml`` setting ``autonomy.sandbox_cpu_seconds: 60``, a resolution at
# ``<repo>`` read that file and one at ``<repo>/sub`` read NONE (the bound silently went back to the default), while sharing one lock and one RunRecord.

_REPOSITORY_CONFIG = "autonomy:\n  sandbox_cpu_seconds: 60\n"


def _resolve_at(directory, monkeypatch, config_path=None):
    from kriya.config.resolution import resolve_config_state

    monkeypatch.chdir(directory)
    return resolve_config_state(config_path)


@pytest.mark.parametrize("name", SAME_AUTHORITY)
def test_every_path_of_a_repository_consumes_the_repository_roots_configuration(world, monkeypatch, name):
    (world["repo"] / "kriya.yaml").write_text(_REPOSITORY_CONFIG)
    state = _resolve_at(world[name], monkeypatch)
    assert state.config_path == str(world["repo"] / "kriya.yaml")
    assert state.config_dict["autonomy"]["sandbox_cpu_seconds"] == 60
    assert state.workspace_root == canonical_workspace(str(world["repo"]))      # SEC-009's workspace is the authority, whichever path was invoked


@pytest.mark.parametrize("name", OTHER_AUTHORITY)
def test_a_nested_repository_and_a_linked_work_tree_do_not_inherit_the_enclosing_repositorys_configuration(world, monkeypatch, name):
    (world["repo"] / "kriya.yaml").write_text(_REPOSITORY_CONFIG)
    state = _resolve_at(world[name], monkeypatch)
    assert state.config_path != str(world["repo"] / "kriya.yaml")
    assert state.config_dict["autonomy"].get("sandbox_cpu_seconds") != 60
    assert state.workspace_root == canonical_workspace(str(world[name]))


@pytest.mark.parametrize("name", ["sub", "inner", "sub_link"])
def test_a_configuration_file_beside_a_subdirectory_run_is_refused_not_silently_ignored(world, monkeypatch, name):
    """The repository root's file is the repository's configuration. A second one the run would no longer read must not be dropped silently (it may carry
    the tighter bound): the run stops with a typed error naming it."""
    from kriya.config.resolution import ShadowedConfigurationError

    (world["repo"] / "kriya.yaml").write_text(_REPOSITORY_CONFIG)
    (world["sub"] / "kriya.yml").write_text("autonomy:\n  sandbox_cpu_seconds: 30\n")
    with pytest.raises(ShadowedConfigurationError, match="kriya.yml"):
        _resolve_at(world[name], monkeypatch)
    assert _resolve_at(world["repo"], monkeypatch).config_dict["autonomy"]["sandbox_cpu_seconds"] == 60      # the root itself is unaffected


def test_an_explicit_config_path_is_not_a_discovery_and_is_never_shadow_checked(world, monkeypatch):
    (world["sub"] / "kriya.yaml").write_text("autonomy:\n  sandbox_cpu_seconds: 30\n")
    state = _resolve_at(world["sub"], monkeypatch, config_path=str(world["sub"] / "kriya.yaml"))
    assert state.config_dict["autonomy"]["sandbox_cpu_seconds"] == 30


def test_a_trust_file_inside_the_repository_is_refused_from_a_subdirectory_as_it_is_from_the_root(world, monkeypatch):
    """Repository-controlled trust must not become acceptable because the run started deeper than the file."""
    from kriya.config.authority_approval import TrustPathInsideWorkspaceError, validate_trust_path_outside_workspace

    trust = str(world["repo"] / "trust" / "approval.json")
    for name in ("repo", "sub"):
        state = _resolve_at(world[name], monkeypatch)
        with pytest.raises(TrustPathInsideWorkspaceError):
            validate_trust_path_outside_workspace(trust, state.workspace_root)


def test_paths_state_is_judged_against_the_authority_root_from_a_subdirectory(world, monkeypatch):
    from kriya.config.resolution import StateDirectoryError

    (world["repo"] / "kriya.yaml").write_text("paths:\n  state: ./.kriya/state\n")
    assert _resolve_at(world["sub"], monkeypatch).config_dict["paths"]["state"] == str(world["repo"] / ".kriya" / "state")
    (world["repo"] / "kriya.yaml").write_text("paths:\n  state: ./state\n")
    for name in ("repo", "sub"):
        with pytest.raises(StateDirectoryError):
            _resolve_at(world[name], monkeypatch)


def test_the_authority_commands_report_a_shadowing_configuration_file_typed_not_as_a_traceback(world, monkeypatch):
    (world["repo"] / "kriya.yaml").write_text(_REPOSITORY_CONFIG)
    (world["sub"] / "kriya.yaml").write_text("autonomy:\n  sandbox_cpu_seconds: 30\n")
    monkeypatch.chdir(world["sub"])
    result = CliRunner().invoke(main, ["authority", "inspect"])
    assert result.exit_code == 1 and "not this repository's configuration" in result.output and result.exception is not None and isinstance(result.exception, SystemExit)


def test_state_written_under_the_repository_roots_legacy_identity_is_still_readable_from_every_path_of_the_repository(world):
    """The legacy (pre-PLAT-017) identity is the authority root's, taken from the spelling given - so root state keeps validating from ``sub``, and a
    nested repository never validates against the enclosing repository's."""
    from kriya.context.workspace_identity import legacy_workspace_identity, validate_ownership

    legacy_of_root = legacy_workspace_identity(str(world["repo"]))
    for name in SAME_AUTHORITY:
        assert legacy_workspace_identity(str(world[name])) == legacy_of_root
        validate_ownership(str(world[name]), {"_workspace": {"workspace_id": legacy_of_root, "version": "1"}}, "state.json")
    for name in OTHER_AUTHORITY:
        assert legacy_workspace_identity(str(world[name])) != legacy_of_root
