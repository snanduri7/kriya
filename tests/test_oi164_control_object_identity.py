"""OI-164 (TYPE C, owner decision 2026-10-21, option A): a protected control file stays protected when another path
names the same filesystem object (a hard link).

The one owner is the PLAT-039 rule, kriya.policy.filesystem.is_trusted_control_path, now identity-aware through
ControlObjectIdentities (the (st_dev, st_ino) of every object at a control path of the operation's roots, built only
when a target has more than one link, bounded to one operation). Every consumer inherits it: the planned
filesystem read / list, search and ast (plugins/core_tools._is_control_path), the candidate writer, the Developer's
planned-target refusal and the terminal commit; the two sandbox copies (worktree sync, scoped snapshot) refuse to copy
another name for a control object, typed, before anything is copied (copied, it would become a new single-link file
holding the control bytes that no identity rule can recognise afterwards - MEASURED on the unfixed bytes).

MEASURED on the unfixed bytes (36c0ffe): every alias below was READ, LISTED and a SEARCH hit (except the existing
name-identity cases: the sentinel and the worktree gitfile); an owner alias created before the run was COPIED into the
candidate (worktree sync and scoped snapshot) and read there. No live model, no Docker.
"""
import os
import stat
from pathlib import Path

import pytest
from test_s18c_read_root_identity import _init, git
from test_s18d_read_view_and_enforce_prompt import SENTINEL, _in_view, _model, dotted, view  # noqa: F401

from kriya.platform import filesystem_semantics as fs
from kriya.policy.filesystem import (
    TRUSTED_CONTROL_PATH_DENIED,
    AuthorizedFileWriter,
    ControlObjectIdentities,
    is_trusted_control_path,
)
from kriya.workflow.edit_safety import CandidateMaterializationError, read_file_revision
from kriya.workflow.terminal_commit import CandidateFile, materialize_candidate
from kriya.workflow.workflow_types import SubtaskStatus
from kriya.workflow.worktree import WorktreeSyncError, create_git_worktree

MARK = "OI164_PLANTED_MARKER"
DENIED = (TRUSTED_CONTROL_PATH_DENIED,)


def _refused(result):
    return result.status == SubtaskStatus.FAILED and result.reason_codes == DENIED and result.tool_output is None


def _append_marker(path):
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"\n# {MARK}\n")


def _worktree_admin(candidate):
    with open(os.path.join(candidate, ".git"), encoding="utf-8") as handle:
        return handle.readline().split(":", 1)[1].strip()


def _protected_object(s, kind):
    """(view, root the alias goes in, the protected object) - every class of control object the rule names."""
    owner_state = os.path.join(s.owner, ".kriya", "control", "state.json")
    if not os.path.exists(owner_state):
        os.makedirs(os.path.dirname(owner_state), exist_ok=True)
        Path(owner_state).write_text('{"KRIYA_STATE": 1}\n')
    if kind == "nested-git-config" and not os.path.isdir(os.path.join(s.owner, "vendor", "nested", ".git")):
        os.makedirs(os.path.join(s.owner, "vendor", "nested"))
        git(os.path.join(s.owner, "vendor", "nested"), "init", "-q")
    nested_gitfile = os.path.join(s.owner, "vendor", "linked", ".git")  # a nested worktree's / submodule's gitfile
    if kind == "nested-gitfile":
        os.makedirs(os.path.dirname(nested_gitfile))
        Path(nested_gitfile).write_text("gitdir: ../../.git/modules/linked\n")
    git(s.owner, "add", "app.py")  # an index exists
    objects = sorted(os.path.join(d, f) for d, _, fs_ in os.walk(os.path.join(s.owner, ".git", "objects")) for f in fs_)
    hook = os.path.join(s.owner, ".git", "hooks", "pre-commit")
    if not os.path.exists(hook):
        Path(hook).parent.mkdir(exist_ok=True)
        Path(hook).write_text("#!/bin/sh\n")
    return {
        "git-config": ("owner", s.owner, os.path.join(s.owner, ".git", "config")),
        "git-head": ("owner", s.owner, os.path.join(s.owner, ".git", "HEAD")),
        "git-index": ("owner", s.owner, os.path.join(s.owner, ".git", "index")),
        "git-object": ("owner", s.owner, objects[0]),
        "git-hook": ("owner", s.owner, hook),
        "kriya-state": ("owner", s.owner, owner_state),
        "nested-git-config": ("owner", s.owner, os.path.join(s.owner, "vendor", "nested", ".git", "config")),
        "nested-gitfile": ("owner", s.owner, nested_gitfile),
        "owner-worktree-admin": ("owner", s.owner, os.path.join(_worktree_admin(s.candidate), "HEAD")),
        "candidate-to-owner-git-config": ("worktree", s.candidate, os.path.join(s.owner, ".git", "config")),
        "candidate-to-owner-kriya-state": ("worktree", s.candidate, owner_state),
        "candidate-to-worktree-admin": ("worktree", s.candidate, os.path.join(_worktree_admin(s.candidate), "HEAD")),
        "candidate-gitfile": ("worktree", s.candidate, os.path.join(s.candidate, ".git")),
        "snapshot-git-config": ("snapshot", s.snapshot, os.path.join(s.snapshot, ".git", "config")),
        "snapshot-sentinel": ("snapshot", s.snapshot, os.path.join(s.snapshot, SENTINEL)),
    }[kind]


PROTECTED_KINDS = ["git-config", "git-head", "git-index", "git-object", "git-hook", "kriya-state", "nested-git-config",
                   "nested-gitfile", "owner-worktree-admin", "candidate-to-owner-git-config", "candidate-to-owner-kriya-state",
                   "candidate-to-worktree-admin", "candidate-gitfile", "snapshot-git-config", "snapshot-sentinel"]


def _alias_in(s, kind, name="docs/notes.txt"):
    view_name, root, target = _protected_object(s, kind)
    alias = os.path.join(root, *name.split("/"))
    os.makedirs(os.path.dirname(alias), exist_ok=True)
    os.link(target, alias)
    assert os.path.samefile(alias, target) and os.stat(alias).st_nlink > 1, "a real hard link"
    return view_name, target


def _hit_names(result):
    return {line.split(":", 1)[0] for line in str(result.tool_output).splitlines() if ":" in line}


# -- every protected object class x every planned read surface, in its real view ---------------------------------------

@pytest.mark.parametrize("kind", PROTECTED_KINDS)
def test_a_hard_link_to_a_control_object_is_protected_on_every_read_surface(dotted, kind):  # noqa: F811
    s = dotted
    view_name, target = _alias_in(s, kind)
    read = _in_view(s, view_name, "filesystem", {"operation": "read", "path": "docs/notes.txt"})
    assert _refused(read), read
    assert target not in str(read.error), "the refusal names the alias, never the object it aliases"
    assert _refused(_in_view(s, view_name, "ast", {"file_path": "docs/notes.txt"}))
    listing = _in_view(s, view_name, "filesystem", {"operation": "list", "path": "docs"})
    assert listing.status == SubtaskStatus.COMPLETED and listing.tool_output == [], listing
    hits = _in_view(s, view_name, "search", {"pattern": ".", "path": "docs"})
    assert hits.status == SubtaskStatus.COMPLETED and "notes.txt" not in _hit_names(hits), hits.tool_output


@pytest.mark.parametrize("name", ["owner", "worktree", "snapshot"])
def test_an_ordinary_multi_link_file_still_reads_lists_and_searches(dotted, name):  # noqa: F811
    """The negative control (pnpm-style hard-linked trees): two names for one ORDINARY file are both ordinary - in the
    worktree view too, whose candidate lives under the owner's .kriya (the multi-root index leaves it to its own root).
    A distinct file on the same device never matches a control object (the inode is part of the key)."""
    s = dotted
    root = {"owner": s.owner, "worktree": s.candidate, "snapshot": s.snapshot}[name]
    store = os.path.join(root, "store.py")
    Path(store).write_text(f"{MARK} = 1\n\n\ndef linked():\n    return 1\n")
    os.makedirs(os.path.join(root, "node_modules_like"))
    os.link(store, os.path.join(root, "node_modules_like", "linked.py"))
    assert os.stat(store).st_nlink == 2 and os.stat(store).st_dev == os.stat(os.path.join(root, ".git")).st_dev
    read = _in_view(s, name, "filesystem", {"operation": "read", "path": "node_modules_like/linked.py"})
    assert read.status == SubtaskStatus.COMPLETED and MARK in read.tool_output, read
    ast = _in_view(s, name, "ast", {"file_path": "node_modules_like/linked.py"})
    assert ast.status == SubtaskStatus.COMPLETED and "linked" in str(ast.tool_output), ast
    listing = _in_view(s, name, "filesystem", {"operation": "list", "path": "node_modules_like"})
    assert listing.tool_output == ["linked.py"], listing
    hits = _in_view(s, name, "search", {"pattern": MARK, "path": "node_modules_like"})
    assert _hit_names(hits) == {"linked.py"}, hits.tool_output


def test_symlink_and_subfolder_aliases_into_control_state_stay_protected(dotted):  # noqa: F811
    s = dotted
    os.makedirs(os.path.join(s.owner, "docs"))
    os.symlink(os.path.join(s.owner, ".git", "config"), os.path.join(s.owner, "docs", "cfg_link"))
    os.symlink(os.path.join(s.owner, ".kriya"), os.path.join(s.owner, "docs", "state_dir"))
    assert _refused(_in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/cfg_link"}))
    assert _refused(_in_view(s, "owner", "filesystem", {"operation": "list", "path": "docs/state_dir"}))
    listing = _in_view(s, "owner", "filesystem", {"operation": "list", "path": "docs"})
    assert listing.status == SubtaskStatus.COMPLETED and listing.tool_output == [], listing


def test_a_root_level_control_symlink_protects_what_it_names_and_a_deeper_one_is_judged_where_it_resolves(tmp_path):
    """The name rule resolves a control-named entry in the root (path_relation): <root>/.kriya -> zdata makes zdata's
    files control state, so another name for one of them is protected (the walk reaches zdata first as an ordinary
    directory, in name order, then again through .kriya). Deeper, the name rule judges the resolved path, so a
    control-named symlink there protects nothing the name rule does not: the index agrees."""
    root = tmp_path / "ws"
    (root / "zdata").mkdir(parents=True)
    (root / "zdata" / "state.json").write_text("{}\n")
    os.symlink("zdata", root / ".kriya")
    os.link(root / "zdata" / "state.json", root / "notes.txt")
    assert is_trusted_control_path(str(root), str(root / "zdata" / "state.json")), "by the name rule"
    assert is_trusted_control_path(str(root), str(root / "notes.txt")), "by identity"
    deep = tmp_path / "deep"
    (deep / "zdata").mkdir(parents=True)
    (deep / "a").mkdir()
    (deep / "zdata" / "x.json").write_text("{}\n")
    os.symlink(os.path.join("..", "zdata"), deep / "a" / ".kriya")
    os.link(deep / "zdata" / "x.json", deep / "notes.txt")
    assert not is_trusted_control_path(str(deep), str(deep / "zdata" / "x.json"))
    assert not is_trusted_control_path(str(deep), str(deep / "notes.txt"))


# -- the index is bounded to one operation: a link or control object made later is judged by the next ----------------

def test_a_control_object_and_alias_created_after_an_operation_are_judged_by_the_next(dotted):  # noqa: F811
    s = dotted
    Path(s.owner, "store.py").write_text("STORE = 1\n")
    os.link(os.path.join(s.owner, "store.py"), os.path.join(s.owner, "linked.py"))
    first = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "linked.py"})  # builds an index
    assert first.status == SubtaskStatus.COMPLETED and first.tool_output == "STORE = 1\n", first
    fresh = os.path.join(s.owner, ".kriya", "control", "fresh.json")
    os.makedirs(os.path.dirname(fresh), exist_ok=True)
    Path(fresh).write_text(f'{{"{MARK}": 1}}\n')
    os.link(fresh, os.path.join(s.owner, "later.txt"))
    assert _refused(_in_view(s, "owner", "filesystem", {"operation": "read", "path": "later.txt"}))
    hits = _in_view(s, "owner", "search", {"pattern": MARK, "path": "."})
    assert "later.txt" not in _hit_names(hits), hits.tool_output


def test_one_index_never_learns_a_later_object_and_a_fresh_one_does(tmp_path):
    """The documented bound of one ControlObjectIdentities: what existed when it was built. A per-operation index (never
    module state) is what makes the previous test pass."""
    root = tmp_path / "ws"
    (root / ".kriya").mkdir(parents=True)
    (root / "a.txt").write_text("A\n")
    os.link(root / "a.txt", root / "b.txt")
    index = ControlObjectIdentities((str(root),))
    assert not index.aliases_control_object(str(root / "b.txt"))  # built now
    (root / ".kriya" / "late.json").write_text("{}\n")
    os.link(root / ".kriya" / "late.json", root / "late_alias.txt")
    assert not index.aliases_control_object(str(root / "late_alias.txt"))
    assert ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "late_alias.txt"))
    assert is_trusted_control_path(str(root), str(root / "late_alias.txt"))


# -- fail closed ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def unreadable_control_dir(dotted):  # noqa: F811
    """A mode-000 directory inside the owner's .kriya: the index cannot be completed (a real stat/listing failure)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores mode 000")
    s = dotted
    locked = os.path.join(s.owner, ".kriya", "locked")
    os.makedirs(os.path.join(locked, "inner"))
    os.chmod(locked, 0)
    try:
        yield s
    finally:
        os.chmod(locked, 0o755)


def test_an_incomplete_index_refuses_every_multi_link_target_but_no_single_link_one(unreadable_control_dir):
    s = unreadable_control_dir
    Path(s.owner, "single.txt").write_text("SINGLE\n")
    Path(s.owner, "store.txt").write_text("STORE\n")
    os.link(os.path.join(s.owner, "store.txt"), os.path.join(s.owner, "linked.txt"))
    single = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "single.txt"})
    assert single.status == SubtaskStatus.COMPLETED and single.tool_output == "SINGLE\n", single
    refused = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "linked.txt"})
    # WAVE8 OI-170: the reader's name accounting runs before the control rule and cannot walk this unreadable tree either, so it refuses first with its own
    # typed code; the refusal itself (a typed FAILED, no output) is the contract this test pins, and BOTH codes are closed typed refusals.
    assert refused.status == SubtaskStatus.FAILED and refused.tool_output is None and refused.reason_codes in (DENIED, ("HARD_LINK_OUTSIDE_AUTHORIZED_READABLE_ROOTS",)), refused
    listing = _in_view(s, "owner", "filesystem", {"operation": "list", "path": "."})
    assert "single.txt" in listing.tool_output and not {"store.txt", "linked.txt"} & set(listing.tool_output)


def test_a_target_whose_stat_fails_is_not_shared_safe(tmp_path):
    """link_sharing answers UNKNOWN for a real stat failure (a path beneath a mode-000 directory), and the index then
    judges the target a control object."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores mode 000")
    locked = tmp_path / "locked"
    (locked / "inner").mkdir(parents=True)
    (locked / "inner" / "f").write_text("x")
    os.chmod(locked, 0)
    try:
        assert fs.link_sharing(str(locked / "inner" / "f")) == (fs.LinkSharing.UNKNOWN, None)
        assert ControlObjectIdentities((str(tmp_path),)).aliases_control_object(str(locked / "inner" / "f"))
    finally:
        os.chmod(locked, 0o755)


def _stat_with(result, **changes):
    """SYNTHESIZED: a real stat result with some fields replaced (no second filesystem / no inode-0 filesystem here)."""
    fields = list(result)
    for name, value in changes.items():
        fields[{"st_mode": stat.ST_MODE, "st_ino": stat.ST_INO, "st_dev": stat.ST_DEV,
                "st_nlink": stat.ST_NLINK}[name]] = value
    return os.stat_result(fields, {name: getattr(result, name) for name in ("st_atime", "st_mtime", "st_ctime")})


def _patched_stat(monkeypatch, path, **changes):
    real = os.stat
    real_target = os.path.realpath(path)

    def fake(candidate, *args, **kwargs):
        result = real(candidate, *args, **kwargs)
        return _stat_with(result, **changes) if os.path.realpath(os.fspath(candidate)) == real_target else result

    monkeypatch.setattr(fs.os, "stat", fake)


def test_the_same_inode_on_another_device_never_matches(tmp_path, monkeypatch):
    """SYNTHESIZED (no second filesystem is available): a target whose st_ino equals .git/config's but whose st_dev
    differs is not that object; the same (st_dev, st_ino) is."""
    root = tmp_path / "ws"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "config").write_text("[core]\n")
    (root / "other.txt").write_text("OTHER\n")
    protected = os.stat(root / ".git" / "config")
    _patched_stat(monkeypatch, str(root / "other.txt"), st_ino=protected.st_ino, st_dev=protected.st_dev + 1,
                  st_nlink=2)
    assert not ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "other.txt"))
    _patched_stat(monkeypatch, str(root / "other.txt"), st_ino=protected.st_ino, st_dev=protected.st_dev, st_nlink=2)
    assert ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "other.txt"))


def test_an_unusable_identity_fails_closed(tmp_path, monkeypatch):
    """SYNTHESIZED st_ino 0 (some network/FUSE filesystems): for a multi-link target, and for a control object."""
    root = tmp_path / "ws"
    (root / ".kriya").mkdir(parents=True)
    (root / ".kriya" / "state.json").write_text("{}\n")
    (root / "a.txt").write_text("A\n")
    os.link(root / "a.txt", root / "b.txt")
    assert not ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "b.txt"))
    with monkeypatch.context() as patch:
        _patched_stat(patch, str(root / "b.txt"), st_ino=0)
        assert fs.link_sharing(str(root / "b.txt")) == (fs.LinkSharing.UNKNOWN, None)
        assert ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "b.txt"))
    real_lstat = os.lstat
    control = os.path.join(".kriya", "state.json")  # a suffix test: realpath itself calls lstat
    monkeypatch.setattr(fs.os, "lstat", lambda p, *a, **k: _stat_with(real_lstat(p, *a, **k), st_ino=0)
                        if os.fspath(p).endswith(control) else real_lstat(p, *a, **k))
    assert ControlObjectIdentities((str(root),)).aliases_control_object(str(root / "b.txt"))


# -- write paths: the writer, the Developer's planned target and the terminal commit share the one rule ---------------

def test_the_writer_and_the_terminal_commit_refuse_an_alias_of_a_control_object(tmp_path):
    workspace = tmp_path / "ws"
    (workspace / ".kriya" / "control").mkdir(parents=True)
    (workspace / ".kriya" / "control" / "state.json").write_text("{}\n")
    os.link(workspace / ".kriya" / "control" / "state.json", workspace / "notes.json")
    (workspace / "a.txt").write_text("A\n")
    os.link(workspace / "a.txt", workspace / "b.txt")
    writer = AuthorizedFileWriter(str(workspace))
    assert writer.authorize(str(workspace / "notes.json")).reason_code == TRUSTED_CONTROL_PATH_DENIED
    assert writer.authorize(str(workspace / "b.txt")).reason_code != TRUSTED_CONTROL_PATH_DENIED
    candidate = tmp_path / "candidate"
    candidate.mkdir()
    (candidate / "notes.json").write_text("MODEL\n")
    (candidate / "b.txt").write_text("B\n")
    with pytest.raises(CandidateMaterializationError, match="trusted control path"):
        materialize_candidate(str(candidate), str(workspace),
                              [CandidateFile("notes.json", read_file_revision(str(workspace / "notes.json")))])
    writes = materialize_candidate(str(candidate), str(workspace),
                                   [CandidateFile("b.txt", read_file_revision(str(workspace / "b.txt")))])
    assert [os.path.basename(w.target_path) for w in writes] == ["b.txt"]


# -- the sandbox copies: an alias made before the run never becomes candidate source -----------------------------------

def _repository(path):
    _init(Path(path), "main", "O")
    Path(path, "app.py").write_text("A = 1\n")
    git(path, "add", "-A")
    git(path, "commit", "-qm", "seed")


def test_the_worktree_sync_refuses_to_copy_an_alias_of_a_control_object(tmp_path):
    owner = tmp_path / "owner"
    _repository(owner)
    (owner / "store.txt").write_text("STORE\n")
    os.link(owner / "store.txt", owner / "linked.txt")      # ordinary multi-link: synced
    candidate = create_git_worktree(str(owner))
    assert Path(candidate, "linked.txt").read_text() == "STORE\n"
    os.link(owner / ".git" / "config", owner / "alias_config.txt")
    with pytest.raises(WorktreeSyncError) as refused:
        create_git_worktree(str(owner))
    assert refused.value.reason_code == TRUSTED_CONTROL_PATH_DENIED
    assert str(owner / ".git" / "config") not in str(refused.value)
    assert not os.path.exists(os.path.join(candidate, "alias_config.txt")), "refused before anything was copied"


def test_the_worktree_sync_refuses_a_tracked_file_replaced_by_an_alias(tmp_path):
    """A TRACKED file replaced by a hard link to a control object is a modified entry in git status: refused alike."""
    owner = tmp_path / "owner"
    _repository(owner)
    os.unlink(owner / "app.py")
    os.link(owner / ".git" / "config", owner / "app.py")
    with pytest.raises(WorktreeSyncError) as refused:
        create_git_worktree(str(owner))
    assert refused.value.reason_code == TRUSTED_CONTROL_PATH_DENIED


def test_a_planned_blame_of_an_alias_is_refused(dotted):  # noqa: F811
    """git blame's file goes through the same planned-read authorization (owner view)."""
    s = dotted
    os.link(os.path.join(s.owner, ".git", "config"), os.path.join(s.owner, "blamed.txt"))
    result = _in_view(s, "owner", "git", {"subcommand": "blame", "file_path": "blamed.txt"})
    assert _refused(result), result


def test_the_worktree_sync_copies_a_symlink_as_a_link_whatever_it_names(tmp_path):
    """A symlink is not a second name for an object's bytes: the sync recreates it as a link and the read-side rule judges
    it where it resolves. Even when it points at a multi-link control object (here .git/config with a second name inside
    .git), the sandbox is created."""
    owner = tmp_path / "owner"
    _repository(owner)
    os.link(owner / ".git" / "config", owner / ".git" / "config.bak")
    os.symlink(os.path.join(".git", "config"), owner / "cfg_link")
    candidate = create_git_worktree(str(owner))
    assert os.readlink(os.path.join(candidate, "cfg_link")) == os.path.join(".git", "config")


def test_the_scoped_snapshot_refuses_to_copy_an_alias_and_does_not_materialize_a_nested_repository(tmp_path):
    enclosing = tmp_path / "enclosing"
    _repository(enclosing)
    plain = enclosing / "plain"
    plain.mkdir()
    (plain / "app.py").write_text("P = 1\n")
    nested = plain / "vendor"
    git(str(plain), "clone", "-q", str(enclosing), str(nested))   # a local clone hard-links its objects
    shared_objects = [os.path.join(d, f) for d, _, fs_ in os.walk(nested / ".git" / "objects") for f in fs_
                      if os.stat(os.path.join(d, f)).st_nlink > 1]
    assert shared_objects, "precondition: the nested repository's own objects are multi-link control objects"
    snapshot = create_git_worktree(str(plain))
    assert os.path.isfile(os.path.join(snapshot, "app.py"))
    # W3-3 U3: a nested repository is its own authority; the enclosing repository's bound tree lists it as ONE directory entry, so neither its files nor its
    # control objects are materialized (the exact-root sandbox skips it the same way)
    assert not os.path.lexists(os.path.join(snapshot, "vendor", "app.py")) and not os.path.lexists(os.path.join(snapshot, "vendor", ".git"))
    os.link(nested / ".git" / "config", plain / "notes.txt")
    with pytest.raises(WorktreeSyncError) as refused:
        create_git_worktree(str(plain))
    assert refused.value.reason_code == TRUSTED_CONTROL_PATH_DENIED


# -- the real run environment (T6 enforce harness) -------------------------------------------------------------------

def _enforce(tmp_path, monkeypatch, plant, reads):
    import _t6_harness
    import test_enforce_verified_no_change as shape
    from _chaos_harness import chaos_config
    from test_s18d_read_view_and_enforce_prompt import _core

    from kriya.workflow.plan_schema import EngineeringPlan

    def plan():
        document = shape._plan(shape.TOOL_CRITERION).model_dump()
        document["subtasks"] += [
            {"id": step_id, "description": f"read the notes ({step_id})", "execution_method": "tool",
             "tool_name": "filesystem", "tool_arguments": {"operation": "read", "path": "notes.txt"},
             "depends_on": list(depends_on), "provides": [f"{step_id}_shown"], "acceptance_criteria_ids": [],
             "relevant_global_invariant_ids": ["gi1"], "verification": []}
            for step_id, depends_on in reads]
        return EngineeringPlan.model_validate(document)

    cfg = chaos_config()
    cfg.autonomy.mode = "guardrails"
    workspace = Path(os.path.realpath(tmp_path)) / "ws"
    observed = _t6_harness.enforce_run(tmp_path, monkeypatch, _t6_harness.shop_responder, _t6_harness.shop_files(),
                                       shape.GOAL, [plan], cfg=cfg, tools={"filesystem": _core().FilesystemTool()},
                                       prepare_engine=lambda _engine: plant(workspace))
    return observed


def test_an_enforce_run_never_reads_an_ignored_owner_alias_of_git_config(tmp_path, monkeypatch):
    """Real enforce run: an IGNORED owner alias (never synced) is read by a baseline planned read (depends_on []);
    MEASURED on the unfixed bytes: the read returned .git/config's bytes."""
    def plant(workspace):
        _append_marker(workspace / ".git" / "config")
        (workspace / ".git" / "info").mkdir(exist_ok=True)
        with open(workspace / ".git" / "info" / "exclude", "a", encoding="utf-8") as handle:
            handle.write("notes.txt\n")
        os.link(workspace / ".git" / "config", workspace / "notes.txt")

    observed = _enforce(tmp_path, monkeypatch, plant, [("r_owner", [])])
    steps = {r.subtask_id: r for r in observed.result.subtask_results}
    assert _refused(steps["r_owner"]), steps["r_owner"]
    assert MARK not in str(observed.result.subtask_results) and MARK not in observed.runtime.transcript()


def test_an_enforce_run_refuses_a_sandbox_that_would_copy_an_owner_alias(tmp_path, monkeypatch):
    """Real enforce run: an untracked owner alias of .git/config, created before the run. MEASURED on the unfixed bytes:
    the sync copied it into the candidate as a single-link file and the candidate-view read returned the control bytes.
    Now the run stops typed before any Developer call; the owner's bytes are untouched."""
    def plant(workspace):
        _append_marker(workspace / ".git" / "config")
        os.link(workspace / ".git" / "config", workspace / "notes.txt")

    observed = _enforce(tmp_path, monkeypatch, plant, [("r_owner", []), ("r_candidate", ["s1"])])
    legacy = observed.result.legacy_result
    assert legacy["status"] == "failure" and legacy["reason_codes"] == [TRUSTED_CONTROL_PATH_DENIED], legacy
    assert legacy["failure_category"] == "trusted_control_path_denied"
    assert observed.runtime.count("developer") == 0, "stopped before any Developer call"
    assert MARK not in observed.runtime.transcript() and MARK not in str(observed.result.subtask_results)
