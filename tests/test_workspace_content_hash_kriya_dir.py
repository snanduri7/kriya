"""WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR (P1, BACKEND-FINAL-CLOSURE-005 third repair cycle): the deterministic
reproducer written BEFORE the production change (ENGINEERING_RULES rule 4).

Measured (2026-10-09, remeasuring the P1-2 reproducer; repair-003/prefix/WORKSPACE-CONTENT-HASH-IGNORED-KRIYA-DIR_
measurements.txt): in a workspace whose .gitignore lists ``.kriya/``, ``compute_workspace_content_hash`` is a value
before the first test gate runs and None after it - git's own status clean throughout. Producer: the scratch-index
staging ``git add -A -- . ':!.kriya'`` exits 1 once ``.kriya`` exists on disk and is ignored (git 2.54.0: "The following
paths are ignored by one of your .gitignore files: .kriya"), although the staged set is correct; the function reads any
non-zero exit as "identity unavailable". Every consumer then fails closed: the checkpoint identity, the PRD-024 baseline
capture (indeterminate -> a hard stop under the ``required`` policy), the REG-R1 stability guard
(BASELINE_REVISION_CHANGED, so no replay, STABILITY_UNRESOLVED) and the attribution owner's current revision. The
frozen cohort repositories do not ignore ``.kriya/`` (C2-S2_A: untracked in git status), so the final twelve never hit
it; a user who ignores Kriya's runtime directory - the natural thing to do - does.

Contract (owner decision 2026-10-09): the presence of Kriya runtime artifacts under the root ``.kriya/`` never makes
the workspace identity unavailable and never changes the source-content hash, whether ``.kriya`` is absent, present
and ignored, present and untracked, or tracked (root ``.kriya/`` is ALWAYS excluded from the identity; the base commit
folded into it keeps tracked ``.kriya`` content at HEAD bound). A nested directory named ``.kriya`` is repository
content as before (the exclusion is top-anchored). Unrelated ignored files keep their semantics (never staged, never
part of the identity); any change to real repository content still changes the hash.
"""
import subprocess

import pytest

from kriya.workflow.checkpoint import compute_workspace_content_hash

KRIYA_IGNORED = ".kriya/\nbuild/\n__pycache__/\n"
KRIYA_NOT_IGNORED = "build/\n__pycache__/\n"


def _git(cwd, *args):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd,
                          capture_output=True, text=True, check=True).stdout


def _workspace(tmp_path, gitignore=KRIYA_IGNORED, tracked_kriya=False):
    root = tmp_path / "ws"
    root.mkdir()
    (root / ".gitignore").write_text(gitignore)
    (root / "a.txt").write_text("alpha\n")
    (root / "sub").mkdir()
    (root / "sub" / "b.txt").write_text("beta\n")
    if tracked_kriya:
        (root / ".kriya").mkdir()
        (root / ".kriya" / "config.json").write_text("{}\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")  # the tracked shape ships .kriya/config.json under an ignore list that does not name .kriya
    _git(root, "commit", "-qm", "base")
    return root


def _status(root):
    """Repository content as git sees it (untracked listed per file); the root .kriya runtime directory left out."""
    lines = _git(root, "status", "--porcelain", "--untracked-files=all").splitlines()
    return sorted(line for line in lines if not line[3:].startswith(".kriya/"))  # "XY path": the path starts at column 3


def _write_runtime_artifact(root):
    (root / ".kriya" / "test-reports").mkdir(parents=True, exist_ok=True)
    (root / ".kriya" / "test-reports" / "x.xml").write_text("<testsuite/>\n")


# ---------------------------------------------------------------- the measured shape
def test_runtime_artifacts_under_an_ignored_kriya_dir_never_make_the_identity_unavailable(tmp_path):
    """.gitignore lists .kriya/: the identity before the first runtime write equals the identity after it, and neither
    is None; git's own view of the repository content is clean before and after."""
    root = _workspace(tmp_path)
    assert _status(root) == []
    before = compute_workspace_content_hash(str(root))
    assert before is not None
    _write_runtime_artifact(root)
    after = compute_workspace_content_hash(str(root))
    assert after is not None, "the identity became unavailable once .kriya/ existed (ignored)"
    assert after == before
    assert _status(root) == []
    # and again, with more runtime state (checkpoints, worktrees): still the same identity
    (root / ".kriya" / "checkpoints").mkdir()
    (root / ".kriya" / "checkpoints" / "run.json").write_text("{}\n")
    assert compute_workspace_content_hash(str(root)) == before


# ---------------------------------------------------------------- controls: every shape of the root .kriya directory
@pytest.mark.parametrize("shape", ["absent", "present_ignored", "present_not_ignored", "tracked"])
def test_every_shape_of_the_root_kriya_dir_is_excluded_from_the_identity(tmp_path, shape):
    root = _workspace(tmp_path, gitignore=KRIYA_NOT_IGNORED if shape in ("present_not_ignored", "tracked") else KRIYA_IGNORED,
                      tracked_kriya=(shape == "tracked"))
    status_before = _status(root)
    before = compute_workspace_content_hash(str(root))
    assert before is not None
    if shape == "absent":
        assert not (root / ".kriya").exists()
        assert compute_workspace_content_hash(str(root)) == before  # deterministic
        return
    _write_runtime_artifact(root)
    if shape == "tracked":
        # Kriya's own runtime state changing a tracked file under .kriya is still not a content change: always excluded.
        (root / ".kriya" / "config.json").write_text('{"changed": true}\n')
    after = compute_workspace_content_hash(str(root))
    assert after is not None and after == before, shape
    assert _status(root) == status_before, shape  # the repository content as git sees it did not move


def test_a_nested_directory_named_kriya_is_repository_content_as_before(tmp_path):
    """Only the ROOT .kriya is Kriya's runtime directory; sub/.kriya is a path like any other (top-anchored exclusion)."""
    root = _workspace(tmp_path, gitignore=KRIYA_NOT_IGNORED)
    before = compute_workspace_content_hash(str(root))
    (root / "sub" / ".kriya").mkdir()
    (root / "sub" / ".kriya" / "data.txt").write_text("content\n")
    nested = compute_workspace_content_hash(str(root))
    assert nested is not None and nested != before


def test_real_content_changes_still_change_the_identity_and_ignored_files_still_do_not(tmp_path):
    root = _workspace(tmp_path)
    _write_runtime_artifact(root)  # the runtime directory is present throughout
    base = compute_workspace_content_hash(str(root))
    assert base is not None
    # an unrelated ignored artifact: not content (existing semantics)
    (root / "build").mkdir()
    (root / "build" / "out.txt").write_text("binary\n")
    assert compute_workspace_content_hash(str(root)) == base
    # a new untracked, not-ignored file: content
    (root / "new.txt").write_text("new\n")
    with_new = compute_workspace_content_hash(str(root))
    assert with_new is not None and with_new != base
    # a tracked file edited on disk (unstaged): content
    (root / "a.txt").write_text("alpha changed\n")
    edited = compute_workspace_content_hash(str(root))
    assert edited is not None and edited not in (base, with_new)
    # a tracked file deleted: content
    (root / "sub" / "b.txt").unlink()
    deleted = compute_workspace_content_hash(str(root))
    assert deleted is not None and deleted not in (base, with_new, edited)
    # restoring the content restores the identity exactly (the identity is a function of the content alone)
    (root / "sub" / "b.txt").write_text("beta\n")
    (root / "a.txt").write_text("alpha\n")
    (root / "new.txt").unlink()
    assert compute_workspace_content_hash(str(root)) == base


def test_a_workspace_that_is_not_a_git_repository_is_still_unavailable(tmp_path):
    """The fail-closed answer for a non-repository is unchanged (resume and baselines need a real identity)."""
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("alpha\n")
    assert compute_workspace_content_hash(str(plain)) is None
