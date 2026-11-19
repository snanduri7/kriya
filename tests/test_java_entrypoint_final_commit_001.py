"""JAVA-ENTRYPOINT-IDENTITY-001, the final-commit half (Stage C run c1, 20261119T101125-d5e300d1; owner ruling 2026-11-19).

The candidate-stage check (tests/test_java_entrypoint_identity_001.py) fails only the unit that may write pom.xml. Nothing judged the
FINAL contents: a plan whose pom owner never repaired the mismatch, a direct run that adds the main class to a project with an existing
pom, a tool unit or a resumed run all reach the same function, ``terminal_commit.commit_terminal_candidate``. That function now refuses
a batch that would newly leave the real workspace with ``<exec.mainClass>`` naming a class that is not the application's one main class.

Which commits are judged is decided inside the commit module from the run's own lease (``run_coordinator.candidate_workspace_root``):
only a destination the active run authorized as its isolated candidate is an intermediate apply. No caller states it, so no caller can
declare a real-workspace commit intermediate.

The detector is the one the candidate gates use (``attempt.java_main_classes_of`` -> ``DependencyGraph.find_java_main_class``).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS, no_approval_needed

from kriya.control.run_coordinator import authorize_candidate_workspace, begin_mutating_run
from kriya.workflow import terminal_commit
from kriya.workflow.attempt import java_main_classes_of
from kriya.workflow.edit_safety import StagedFileWrite, content_revision
from kriya.workflow.terminal_commit import (
    ENTRYPOINT_IDENTITY_INDETERMINATE,
    ENTRYPOINT_IDENTITY_REFUSED,
    commit_terminal_candidate,
    entrypoint_identity_refusal,
)
from kriya.workflow.verification_binding import bind_candidate

POM, APP, PACKAGED_APP = "pom.xml", "src/main/java/ProtocolApplication.java", "src/main/java/com/example/ProtocolApplication.java"
CONFIGURED = "com.example.ProtocolApplication"


def _pom(main_class: str = CONFIGURED) -> str:
    return ("<project>\n  <modelVersion>4.0.0</modelVersion>\n  <properties>\n"
            f"    <exec.mainClass>{main_class}</exec.mainClass>\n  </properties>\n</project>\n")


def _app(package: str = "", name: str = "ProtocolApplication") -> str:
    declaration = f"package {package};\n\n" if package else ""
    return (f"{declaration}public class {name} {{\n    public static void main(String[] args) {{\n"
            "        System.out.println(\"run\");\n    }\n}\n")


HELPER = "public class Helper {\n    static int one() { return 1; }\n}\n"


def _workspace(tmp_path, files, name="ws"):
    """A real git work tree holding ``files`` as its committed content (the membership the check reads is the canonical bound tree:
    what git tracks or leaves visible)."""
    root = tmp_path / name
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for relpath, text in files.items():
        (root / relpath).parent.mkdir(parents=True, exist_ok=True)
        (root / relpath).write_text(text, encoding="utf-8")
    if files:
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        # maintenance.auto=false / gc.auto=0: the harness rule for a fixture commit (tests/_named_test_oracle_harness.py::git is the model)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "maintenance.auto=false",
                        "-c", "gc.auto=0", "commit", "-q", "-m", "base"], check=True)
    return str(root)


def _batch(workspace, changes):
    """The exact batch a commit would write: ``changes`` maps a path to its new text, or to None for a deletion."""
    batch = []
    for relpath, text in changes.items():
        target = str(Path(workspace, relpath))
        exists = Path(target).is_file()
        base = Path(target).read_text(encoding="utf-8") if exists else ""
        batch.append(StagedFileWrite(target_path=target, content=text or "", base_path=target, delete=text is None,
                                     expected_base_revision=content_revision(base), expected_base_exists=exists))
    return batch


def _refusal(workspace, changes, detector=java_main_classes_of):
    return entrypoint_identity_refusal(_batch(workspace, changes), workspace, detector)


def _commit(workspace, changes, **overrides):
    batch = _batch(workspace, changes)
    return commit_terminal_candidate(
        batch, workspace_path=workspace, transaction_id="tx", static_analysis=DISABLED_STATIC_ANALYSIS,
        verified_candidate=bind_candidate(batch, workspace), approval=no_approval_needed(batch, workspace),
        **{"java_main_classes": java_main_classes_of, **overrides})


def _tree(workspace):
    return {str(path.relative_to(workspace)): path.read_bytes() for path in Path(workspace).rglob("*")
            if path.is_file() and ".git" not in path.relative_to(workspace).parts}


def _assert_refused(found, configured=CONFIGURED, actual="ProtocolApplication", source=APP):
    assert found is not None
    reason_code, detail = found
    assert reason_code == ENTRYPOINT_IDENTITY_REFUSED
    assert POM in detail and repr(configured) in detail and repr(actual) in detail and source in detail


# ------------------------------------------------------------------ what is refused, and what is not

def test_run_c1_greenfield_mismatch_is_refused_at_the_final_commit(tmp_path):
    workspace = _workspace(tmp_path, {})
    _assert_refused(_refusal(workspace, {POM: _pom(), APP: _app()}))


@pytest.mark.parametrize("configured,path,package", [
    (CONFIGURED, PACKAGED_APP, "com.example"),
    ("ProtocolApplication", APP, ""),
])
def test_a_matching_packaged_or_default_package_main_class_is_not_refused(tmp_path, configured, path, package):
    workspace = _workspace(tmp_path, {})
    assert _refusal(workspace, {POM: _pom(configured), path: _app(package)}) is None


def test_a_java_only_batch_that_introduces_the_mismatch_is_refused_against_the_unchanged_pom(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})                       # PENDING before: a pom, no main class yet
    _assert_refused(_refusal(workspace, {APP: _app()}))


def test_a_pom_only_batch_that_introduces_the_mismatch_is_refused_against_the_unchanged_java(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom("ProtocolApplication"), APP: _app()})        # VERIFIED before
    _assert_refused(_refusal(workspace, {POM: _pom()}))


def test_an_identical_pre_existing_mismatch_does_not_block_unrelated_java_work(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), APP: _app()})          # the mismatch is already in the workspace
    assert _refusal(workspace, {"src/main/java/Helper.java": HELPER}) is None


def test_a_new_mismatch_that_replaces_an_old_one_is_refused(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), APP: _app()})
    _assert_refused(_refusal(workspace, {POM: _pom("org.other.Main")}), configured="org.other.Main")


def test_a_batch_that_touches_neither_the_pom_nor_java_is_not_judged_and_never_needs_the_detector(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), APP: _app()})
    assert _refusal(workspace, {"README.md": "notes\n"}, detector=None) is None


def test_java_work_in_a_project_whose_pom_declares_no_entry_point_never_needs_the_detector(tmp_path):
    workspace = _workspace(tmp_path, {POM: "<project>\n  <modelVersion>4.0.0</modelVersion>\n</project>\n"})
    assert _refusal(workspace, {APP: _app()}, detector=None) is None
    assert _refusal(_workspace(tmp_path, {}, name="no-pom"), {APP: _app()}, detector=None) is None


NO_MAIN = "package com.example;\n\npublic class ProtocolApplication {\n    static int one() { return 1; }\n}\n"


@pytest.mark.parametrize("change", [None, NO_MAIN], ids=["class deleted", "main method removed"])
def test_removing_the_verified_entry_point_under_an_unchanged_pom_is_refused(tmp_path, change):
    """Owner review 2026-11-19: PENDING after the batch must not hide an entry point the batch itself destroyed."""
    workspace = _workspace(tmp_path, {POM: _pom(), PACKAGED_APP: _app("com.example")})       # VERIFIED before
    reason_code, detail = _refusal(workspace, {PACKAGED_APP: change})
    assert reason_code == ENTRYPOINT_IDENTITY_REFUSED
    assert repr(CONFIGURED) in detail and "no class that has a main method" in detail and PACKAGED_APP in detail


def test_a_project_that_never_had_a_verified_entry_point_may_stay_pending(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), "src/main/java/Helper.java": HELPER})     # PENDING before: incomplete, or greenfield
    assert _refusal(workspace, {"src/main/java/Other.java": HELPER.replace("Helper", "Other")}) is None
    assert _refusal(workspace, {POM: _pom("org.other.Main")}) is None                         # still no main class: nothing known to be wrong
    assert _refusal(_workspace(tmp_path, {}, name="greenfield"), {POM: _pom()}) is None       # the pom arrives first


def test_several_main_classes_are_matched_by_membership_not_given_up_on(tmp_path):
    """Owner review 2026-11-19: several main classes must not hide a configured name that is none of them."""
    tool = "src/main/java/tools/Migrate.java"
    workspace = _workspace(tmp_path, {POM: _pom(), tool: _app("tools", "Migrate")})
    # the configured name is absent from the complete set of main classes after the batch: refused, naming all of them
    reason_code, detail = _refusal(workspace, {POM: _pom("org.other.Main"), APP: _app()})
    assert reason_code == ENTRYPOINT_IDENTITY_REFUSED
    assert repr("org.other.Main") in detail and "none of the application's 2 classes" in detail
    assert "ProtocolApplication, tools.Migrate" in detail and APP in detail and tool in detail
    # the configured name is one of them: not rejected
    assert _refusal(workspace, {PACKAGED_APP: _app("com.example")}) is None
    assert _refusal(workspace, {POM: _pom("tools.Migrate"), APP: _app()}) is None


def test_membership_is_the_canonical_bound_tree_with_the_batchs_additions_and_deletions(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), PACKAGED_APP: _app("com.example"), ".gitignore": "target/\n"})   # VERIFIED before
    # moved to the default package (old file deleted, new file added): now a mismatch
    _assert_refused(_refusal(workspace, {PACKAGED_APP: None, APP: _app()}))
    # a git-ignored build output is not a member and is never read
    Path(workspace, "target").mkdir()
    Path(workspace, "target", "Stray.java").write_text(_app(name="Stray"), encoding="utf-8")
    assert _refusal(workspace, {"src/main/java/Helper.java": HELPER}) is None
    # an untracked but visible main class IS a member (it is in every candidate built from this workspace), so it is judged: with a
    # tracked-only listing this project would look PENDING before and after, and the pom change below would pass
    untracked = _workspace(tmp_path, {POM: _pom("ProtocolApplication")}, name="untracked-main")
    Path(untracked, APP).parent.mkdir(parents=True)
    Path(untracked, APP).write_text(_app(), encoding="utf-8")
    _assert_refused(_refusal(untracked, {POM: _pom()}))


def test_an_ignored_pom_on_disk_decides_nothing_but_an_authorized_one_does(tmp_path):
    """Owner review 2026-11-19: pom.xml is workspace content only as a member of the bound tree (or as part of the batch)."""
    ignored = _workspace(tmp_path, {".gitignore": "pom.xml\n"}, name="ignored-pom")
    Path(ignored, POM).write_text(_pom(), encoding="utf-8")                # on disk, git-ignored: in no candidate
    assert _refusal(ignored, {APP: _app()}, detector=None) is None         # not judged at all: the detector is never needed
    # control: the same file as a member (untracked but visible) is judged, and so is one the batch itself writes
    visible = _workspace(tmp_path, {"README.md": "x\n"}, name="visible-pom")
    Path(visible, POM).write_text(_pom(), encoding="utf-8")
    _assert_refused(_refusal(visible, {APP: _app()}))
    _assert_refused(_refusal(ignored, {POM: _pom(), APP: _app()}))


# ------------------------------------------------------------------ fail closed

def test_an_applicable_check_without_a_detector_refuses(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    reason_code, detail = _refusal(workspace, {APP: _app()}, detector=None)
    assert reason_code == ENTRYPOINT_IDENTITY_INDETERMINATE and "no Java main-class detector" in detail


def test_a_detector_that_raises_refuses(tmp_path):
    def broken(_sources):
        raise OSError("index unavailable")

    workspace = _workspace(tmp_path, {POM: _pom()})
    reason_code, detail = _refusal(workspace, {APP: _app()}, detector=broken)
    assert reason_code == ENTRYPOINT_IDENTITY_INDETERMINATE and "OSError: index unavailable" in detail


def test_a_membership_listing_that_raises_refuses(tmp_path, monkeypatch):
    def broken(_workspace_path):
        raise RuntimeError("git ls-files failed")

    monkeypatch.setattr(terminal_commit, "bound_tree_paths", broken)
    workspace = _workspace(tmp_path, {POM: _pom()})
    reason_code, _detail = _refusal(workspace, {APP: _app()})
    assert reason_code == ENTRYPOINT_IDENTITY_INDETERMINATE


# ------------------------------------------------------------------ which destination is judged: the run's lease decides

def test_an_intermediate_apply_into_the_runs_authorized_candidate_is_not_judged_but_the_final_commit_is(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    candidate = _workspace(tmp_path, {POM: _pom()}, name="plan-candidate")
    with begin_mutating_run(workspace):
        authorize_candidate_workspace(candidate)
        # the plan unit that writes the class into the plan candidate (run c1's s5): staged behaviour, no refusal here
        assert _refusal(candidate, {APP: _app()}) is None
        # the same batch aimed at the run's real workspace is the final commit: refused
        _assert_refused(_refusal(workspace, {APP: _app()}))


def test_a_real_workspace_cannot_be_declared_an_intermediate_destination(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    with begin_mutating_run(workspace):
        authorize_candidate_workspace(workspace)                           # a false "this is only my candidate" for the run's own workspace
        _assert_refused(_refusal(workspace, {APP: _app()}))


def test_a_destination_no_run_authorized_is_judged_with_or_without_an_active_run(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    other = _workspace(tmp_path, {POM: _pom()}, name="other")
    _assert_refused(_refusal(workspace, {APP: _app()}))                   # no active run at all (a direct call)
    with begin_mutating_run(workspace):
        _assert_refused(_refusal(other, {APP: _app()}))                   # a run is active, but it never authorized this path
    _assert_refused(_refusal(workspace, {APP: _app()}))                   # authorization expired with the run


# ------------------------------------------------------------------ the commit function itself (both routes end here)

def test_the_commit_function_refuses_before_any_byte_changes(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom(), "README.md": "old\n"})
    before = _tree(workspace)
    outcome = _commit(workspace, {APP: _app(), "README.md": "new\n"})
    assert not outcome.committed and outcome.workspace_state == "UNCHANGED"
    assert outcome.reason_code == ENTRYPOINT_IDENTITY_REFUSED
    assert isinstance(outcome.error, terminal_commit.EntrypointIdentityRefused)
    assert repr(CONFIGURED) in str(outcome.error) and repr("ProtocolApplication") in str(outcome.error) and APP in str(outcome.error)
    payload = outcome.failure_payload()
    assert payload["reason_code"] == ENTRYPOINT_IDENTITY_REFUSED and payload["commit_result"] == "NOT_COMMITTED"
    assert _tree(workspace) == before                                      # no partial commit: not even the unrelated README


def test_the_commit_function_refuses_an_applicable_batch_when_its_caller_supplied_no_detector(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    before = _tree(workspace)
    outcome = _commit(workspace, {APP: _app()}, java_main_classes=None)
    assert not outcome.committed and outcome.reason_code == ENTRYPOINT_IDENTITY_INDETERMINATE
    assert _tree(workspace) == before


def test_the_commit_function_commits_a_matching_entry_point_and_unrelated_work(tmp_path):
    workspace = _workspace(tmp_path, {POM: _pom()})
    outcome = _commit(workspace, {PACKAGED_APP: _app("com.example"), "README.md": "notes\n"})
    assert outcome.committed and outcome.workspace_state == "COMMITTED"
    assert Path(workspace, PACKAGED_APP).read_text(encoding="utf-8") == _app("com.example")


def test_negative_control_without_the_final_check_the_mismatch_reaches_the_real_workspace(tmp_path, monkeypatch):
    """The original bypass, reproduced: with the check switched off the same batch is committed, mismatch and all."""
    monkeypatch.setattr(terminal_commit, "entrypoint_identity_refusal", lambda *_args, **_kwargs: None)
    workspace = _workspace(tmp_path, {POM: _pom()})
    outcome = _commit(workspace, {APP: _app()})
    assert outcome.committed and outcome.workspace_state == "COMMITTED"
    assert Path(workspace, APP).read_text(encoding="utf-8") == _app()
    assert CONFIGURED in Path(workspace, POM).read_text(encoding="utf-8")


# ------------------------------------------------------------------ both callers hand the commit function the one detector

def test_both_callers_pass_the_canonical_detector_and_no_caller_passes_an_intent_flag():
    import inspect

    from kriya.workflow import commit_service, stage_apply, workflow

    assert "java_main_classes=_java_main_classes_of" in inspect.getsource(commit_service.commit_verified_candidate)
    assert "from kriya.workflow.attempt import java_main_classes_of" in inspect.getsource(commit_service._java_main_classes_of)
    assert "java_main_classes=deps.java_main_classes_of" in inspect.getsource(stage_apply)
    assert "java_main_classes_of=java_main_classes_of" in inspect.getsource(workflow)
    assert workflow.java_main_classes_of is java_main_classes_of
    # the destination decision has no parameter: the signature carries the detector and nothing that says "intermediate"
    parameters = set(inspect.signature(commit_terminal_candidate).parameters)
    assert "java_main_classes" in parameters
    assert not {name for name in parameters if "intermediate" in name or "final" in name or "intent" in name}
    assert set(inspect.signature(entrypoint_identity_refusal).parameters) == {"writes", "workspace_path", "java_main_classes"}
