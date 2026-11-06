"""WAVE8 OI-170 (owner ruling 2026-11-06: RELEASE BLOCKER). An authorized workspace path must not grant read authority over an underlying file object that has a
name OUTSIDE every authorized root, merely because someone made a hard link.

MEASURED before the fix (reproducer below, run on a9d8c30 / e3d738d): a hard link inside the root to a file outside every root (the enclosing repository's
.git/config, a plain outside file, a credential-looking file renamed through the link) was READ, SEARCHED, parsed by ast and printed by a planned ``git diff``
as an ordinary in-root file: containment judged the PATH, and the path was in the root.

The owner of the decision is ``AuthorizedFileReader.authorize`` (kriya/policy/filesystem.py): the canonical read containment every model-authored read, search
hit, ast parse, blame and planned diff path already asks. It is augmented only for the case path containment cannot see: a regular file with more than one link
is admitted only if every one of its names is accounted for by a name inside the readable roots (st_nlink == the number of names the roots hold, by
``(st_dev, st_ino)``); a name that cannot be accounted for, or an identity that cannot be established, refuses (fail closed). A multi-link file whose names are all
inside the roots stays usable (pnpm-style trees inside a workspace). No second containment engine; the control-object rule (OI-164), symlink handling and the
WRITE authority are untouched.
"""
import os
import stat
from pathlib import Path

import pytest
from test_oi164_control_object_identity import MARK, _hit_names
from test_s18c_read_root_identity import build_three_state
from test_s18d_read_view_and_enforce_prompt import _in_view, view

from kriya.policy.filesystem import AuthorizedFileReader, AuthorizedFileWriter, WriteScopeMode
from kriya.workflow.workflow_types import SubtaskStatus

OUTSIDE_CODE = "HARD_LINK_OUTSIDE_AUTHORIZED_READABLE_ROOTS"
SECRET = "OI170_OUTSIDE_SECRET_BYTES"


@pytest.fixture
def s(tmp_path):
    return build_three_state(tmp_path)


def _outside_file(s, name="outside_secret.txt", text=SECRET):
    """A file outside every root of the views, on the same filesystem (a hard link never crosses one)."""
    path = os.path.join(os.path.dirname(s.enclosing), name)
    Path(path).write_text(f"{text}\n")
    return path


def _link(target, root, name="docs/notes.txt"):
    alias = os.path.join(root, *name.split("/"))
    os.makedirs(os.path.dirname(alias), exist_ok=True)
    os.link(target, alias)
    assert os.path.samefile(alias, target) and os.stat(alias).st_nlink == 2 and stat.S_ISREG(os.stat(alias).st_mode), "a real hard link"
    return alias


def _refused_outside(result):
    return result.status == SubtaskStatus.FAILED and result.reason_codes == (OUTSIDE_CODE,) and result.tool_output is None


# ---------------------------------------------------------------------------- the reproducer: every read surface, every view

OUTSIDE_OBJECTS = ["plain-outside-file", "enclosing-git-config", "credential-looking-outside-file"]


def _object(s, kind):
    if kind == "plain-outside-file":
        return _outside_file(s)
    if kind == "enclosing-git-config":
        return os.path.join(s.enclosing, ".git", "config")       # the ledger's probe: a control object of a repository that is NOT one of the roots
    return _outside_file(s, "deploy-secrets.pem", SECRET)         # the name that a direct read would refuse; the link has an innocent name


@pytest.mark.parametrize("kind", OUTSIDE_OBJECTS)
@pytest.mark.parametrize("view_name, root_of", [("owner", lambda s: s.owner), ("worktree", lambda s: s.candidate), ("snapshot", lambda s: s.snapshot)])
def test_a_hard_link_to_an_object_outside_every_root_is_refused_on_every_read_surface(s, kind, view_name, root_of):
    target = _object(s, kind)
    marker = "[core]" if kind == "enclosing-git-config" else SECRET
    _link(target, root_of(s))
    read = _in_view(s, view_name, "filesystem", {"operation": "read", "path": "docs/notes.txt"})
    assert _refused_outside(read), read
    assert target not in str(read.error) and marker not in str(read.error), "the refusal names the link, never the object it aliases or its bytes"
    assert _refused_outside(_in_view(s, view_name, "ast", {"file_path": "docs/notes.txt"}))
    hits = _in_view(s, view_name, "search", {"pattern": ".", "path": "docs"})
    assert hits.status == SubtaskStatus.COMPLETED and "notes.txt" not in _hit_names(hits), hits.tool_output


@pytest.mark.parametrize("name", ["owner", "worktree"])
def test_a_tracked_file_replaced_by_a_hard_link_to_an_outside_object_is_not_printed_by_a_planned_diff(s, name):
    target = _outside_file(s)
    root = {"owner": s.owner, "worktree": s.candidate}[name]
    tracked = os.path.join(root, "pkg", "mod.py")
    os.unlink(tracked)
    os.link(target, tracked)
    if name == "owner":
        result = view("git", {"subcommand": "diff"}, (s.owner,))       # the in-place run: the owner folder IS what the run changes
    else:
        result = _in_view(s, "worktree", "git", {"subcommand": "diff"})
    assert result.status == SubtaskStatus.FAILED and result.reason_codes == (OUTSIDE_CODE,) and result.tool_output is None, result
    assert SECRET not in str(result.error) and "mod.py" in str(result.error)


# ---------------------------------------------------------------------------- ordinary in-root hard links stay usable

@pytest.mark.parametrize("name", ["owner", "worktree", "snapshot"])
def test_a_multi_link_file_whose_every_name_is_inside_the_roots_still_reads_lists_and_searches(s, name):
    root = {"owner": s.owner, "worktree": s.candidate, "snapshot": s.snapshot}[name]
    store = os.path.join(root, "store.py")
    Path(store).write_text(f"{MARK} = 1\n\n\ndef linked():\n    return 1\n")
    os.makedirs(os.path.join(root, "node_modules_like"))
    for index in range(3):                                                 # three names, all inside the root
        os.link(store, os.path.join(root, "node_modules_like", f"linked{index}.py"))
    assert os.stat(store).st_nlink == 4
    read = _in_view(s, name, "filesystem", {"operation": "read", "path": "node_modules_like/linked1.py"})
    assert read.status == SubtaskStatus.COMPLETED and MARK in read.tool_output, read
    assert _in_view(s, name, "ast", {"file_path": "node_modules_like/linked2.py"}).status == SubtaskStatus.COMPLETED
    hits = _in_view(s, name, "search", {"pattern": MARK, "path": "node_modules_like"})
    assert _hit_names(hits) == {f"linked{i}.py" for i in range(3)}, hits.tool_output


def test_names_spread_over_the_owner_and_its_candidate_are_all_inside_the_readable_roots(s):
    """The worktree view's roots are (owner, candidate) and the candidate lives under the owner's own .kriya: a name in each is two names, counted once each."""
    shared = os.path.join(s.owner, "shared.py")
    Path(shared).write_text(f"{MARK} = 2\n")
    os.link(shared, os.path.join(s.candidate, "shared_in_candidate.py"))
    read = _in_view(s, "worktree", "filesystem", {"operation": "read", "path": "shared_in_candidate.py"})
    assert read.status == SubtaskStatus.COMPLETED and MARK in read.tool_output, read


def test_one_name_inside_and_one_outside_is_refused_even_when_a_second_name_is_inside(s):
    """Three names: two inside the root, one outside - a link count of 3 with only 2 names accounted for."""
    target = _outside_file(s)
    _link(target, s.owner, "docs/first.txt")
    os.link(target, os.path.join(s.owner, "docs", "second.txt"))
    assert os.stat(target).st_nlink == 3
    for name in ("first.txt", "second.txt"):
        assert _refused_outside(_in_view(s, "owner", "filesystem", {"operation": "read", "path": f"docs/{name}"}))


def test_the_accounting_is_judged_per_operation_a_later_outside_link_is_refused_by_the_next_read(s):
    store = os.path.join(s.owner, "store.py")
    Path(store).write_text("STORE = 1\n")
    os.link(store, os.path.join(s.owner, "linked.py"))
    first = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "linked.py"})
    assert first.status == SubtaskStatus.COMPLETED and first.tool_output == "STORE = 1\n", first
    outside_name = os.path.join(os.path.dirname(s.enclosing), "later_outside_name.py")
    os.link(store, outside_name)                                            # the operator now links the same object from outside every root
    assert _refused_outside(_in_view(s, "owner", "filesystem", {"operation": "read", "path": "linked.py"}))


# ---------------------------------------------------------------------------- what must not change

def test_control_object_hard_links_inside_and_outside_the_root_stay_refused(s):
    """An in-root hard link to the OWNER's .git/config is a control object (OI-164) and keeps its reason code; the outside object is the new code."""
    git_config = os.path.join(s.owner, ".git", "config")
    _link(git_config, s.owner, "docs/cfg.txt")
    refused = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/cfg.txt"})
    assert refused.status == SubtaskStatus.FAILED and refused.reason_codes == ("TRUSTED_CONTROL_PATH_DENIED",), refused
    state = os.path.join(s.owner, ".kriya", "control", "state.json")
    os.makedirs(os.path.dirname(state), exist_ok=True)
    Path(state).write_text("{}\n")
    _link(state, s.owner, "docs/state.txt")
    refused = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/state.txt"})
    assert refused.status == SubtaskStatus.FAILED and refused.reason_codes == ("TRUSTED_CONTROL_PATH_DENIED",), refused


def test_symlink_behaviour_is_unchanged(s):
    outside = _outside_file(s)
    os.makedirs(os.path.join(s.owner, "docs"))
    os.symlink(outside, os.path.join(s.owner, "docs", "out_link.txt"))
    refused = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/out_link.txt"})
    assert refused.status == SubtaskStatus.FAILED and refused.reason_codes == ("PATH_OUTSIDE_AUTHORIZED_READABLE_ROOTS",), refused   # as before: the path code
    os.symlink(os.path.join("..", "app.py"), os.path.join(s.owner, "docs", "in_link.py"))
    ok = _in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/in_link.py"})
    assert ok.status == SubtaskStatus.COMPLETED and "ORIGIN_OWNER" in ok.tool_output, ok
    # a symlink to an in-root multi-link file is as usable as the file itself
    store = os.path.join(s.owner, "docs", "store.py")
    Path(store).write_text("STORE = 7\n")
    os.link(store, os.path.join(s.owner, "docs", "store2.py"))
    os.symlink("store.py", os.path.join(s.owner, "docs", "store_link.py"))
    assert _in_view(s, "owner", "filesystem", {"operation": "read", "path": "docs/store_link.py"}).tool_output == "STORE = 7\n"


def test_write_authority_is_unchanged_the_writer_judges_a_linked_name_exactly_as_before(s):
    """OI-170 is a READ-authority fix. The writer's decision for the same name is the same with and without the outside link (pinned, not endorsed:
    TRACED that every Kriya write replaces the directory entry (atomic_write_file), so an outside object's bytes are not rewritten through the link)."""
    target = _outside_file(s)
    plain = os.path.join(s.owner, "docs", "plain.txt")
    os.makedirs(os.path.dirname(plain))
    Path(plain).write_text("x\n")
    linked = _link(target, s.owner, "docs/linked.txt")
    writer = AuthorizedFileWriter(s.owner, write_scope_mode=WriteScopeMode.UNRESTRICTED)
    assert writer.authorize(linked).decision == writer.authorize(plain).decision
    assert writer.authorize(linked).reason_code == writer.authorize(plain).reason_code


# ---------------------------------------------------------------------------- the canonical reader, directly (fail closed)

def test_the_reader_refuses_an_outside_link_and_admits_an_inside_one_with_the_new_reason_code(s):
    target = _outside_file(s)
    outside_link = _link(target, s.owner, "docs/outside.txt")
    store = os.path.join(s.owner, "docs", "store.txt")
    Path(store).write_text("S\n")
    inside_link = os.path.join(s.owner, "docs", "store_alias.txt")
    os.link(store, inside_link)
    reader = AuthorizedFileReader(s.owner)
    refused = reader.authorize(outside_link)
    assert refused.reason_code == OUTSIDE_CODE and refused.decision.name == "DENY" and target not in refused.explanation
    assert reader.authorize(inside_link).decision.name == "ALLOW" and reader.authorize(store).decision.name == "ALLOW"
    assert reader.authorize(os.path.join(s.owner, "app.py")).decision.name == "ALLOW"   # a single-link file never pays for the accounting


@pytest.mark.skipif(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0), reason="an unreadable directory needs POSIX permissions and a non-root user")
def test_an_unreadable_directory_makes_the_accounting_incomplete_and_a_multi_link_file_fails_closed(s):
    store = os.path.join(s.owner, "store.py")
    Path(store).write_text("S = 1\n")
    os.link(store, os.path.join(s.owner, "store_alias.py"))
    sealed = os.path.join(s.owner, "sealed")
    os.makedirs(sealed)
    os.chmod(sealed, 0o000)
    try:
        reader = AuthorizedFileReader(s.owner)
        assert reader.authorize(store).reason_code == OUTSIDE_CODE, "names that cannot be counted are not assumed to be inside"
        assert reader.authorize(os.path.join(s.owner, "app.py")).decision.name == "ALLOW", "a single-link file does not depend on the census"
    finally:
        os.chmod(sealed, 0o755)


def test_a_host_that_gives_no_file_identity_fails_closed_for_a_multi_link_file(s, monkeypatch):
    """st_ino 0 (some network/FUSE filesystems): the names of a multi-link file cannot be matched, so it is not admitted; a single-link file is unaffected."""
    import kriya.policy.filesystem as policy_filesystem

    store = os.path.join(s.owner, "store.py")
    Path(store).write_text("S = 1\n")
    os.link(store, os.path.join(s.owner, "store_alias.py"))
    monkeypatch.setattr(policy_filesystem, "object_key", lambda result: None)
    reader = AuthorizedFileReader(s.owner)
    assert reader.authorize(store).reason_code == OUTSIDE_CODE
    assert reader.authorize(os.path.join(s.owner, "app.py")).decision.name == "ALLOW"
