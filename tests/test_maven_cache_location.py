"""Live matrix (commons-lang, Apache RAT): Kriya's Maven cache never lives in
the tree the project's own build walks.

Live run 86fa47d4: every contained compile of commons-lang failed in
apache-rat-plugin's validate-phase check - "Too many files with unapproved
license: 565" - and all 567 flagged files were Kriya's own cached artifacts
under the worktree's .kriya/m2_cache. The cache now lives under the Kriya
state root, keyed by the canonical workspace, so every run and worktree of
one workspace reuses what its registry-scoped acquisition fetched.
"""
import os

from kriya.config.config import AutonomyConfig
from kriya.core.state_paths import ENV_STATE_DIR
from kriya.tools.validate import PolymorphicValidator


def _within(path, root):
    return os.path.commonpath([os.path.realpath(path), os.path.realpath(root)]) == os.path.realpath(root)


def test_the_maven_cache_is_outside_every_source_tree_and_shared_per_workspace(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setenv(ENV_STATE_DIR, str(state))
    workspace = tmp_path / "repo"
    worktree = workspace / ".kriya" / "worktree"
    candidate = workspace / ".kriya" / "worktrees" / "candidate-1"
    other = tmp_path / "other-repo"
    for path in (worktree, candidate, other):
        path.mkdir(parents=True)
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci")

    caches = [PolymorphicValidator(str(tree), original_workspace_path=str(workspace), autonomy_cfg=cfg)
              ._maven_cache_dir() for tree in (worktree, candidate)]  # pylint: disable=protected-access
    assert caches[0] == caches[1] and os.path.isdir(caches[0])  # one cache per workspace, reused by every run
    assert _within(caches[0], state)
    for tree in (workspace, worktree, candidate):
        assert not _within(caches[0], tree)  # never inside what a project build tool walks
    other_cache = PolymorphicValidator(str(other), autonomy_cfg=cfg)._maven_cache_dir()  # pylint: disable=protected-access
    assert other_cache != caches[0] and not _within(other_cache, other)
