"""S18d P2b (R8): filesystem aliases of the protected control artifacts (OI-150 c, PLAT-039 rule
kriya.policy.filesystem.is_trusted_control_path; the planned-read judgement plugins/core_tools._is_control_path).

The contract (owner refinement R8): a protected control artifact (.git, .kriya, the scoped-snapshot sentinel) never
becomes readable candidate source through an alternate filesystem alias; a path whose safety cannot be established
because its stat fails counts as protected (fail closed); a lexically ordinary path in a SUBFOLDER that resolves into
.git stays protected. The three surviving mutants of TEST-B (B21, B26 = the DEV's C8, B30) each die here; the production
code already behaved correctly (MEASURED by TEST-B and again here), these are the missing pins. C8 was classified
EQUIVALENT by the P2 DEV ("a stat failure cannot be produced by any deterministic test"): REFUTED - a directory with
mode 000 outside the roots produces it deterministically for a non-root user (the B26 pin below).

MEASURED and NOT fixed here (owner question, see the strict-xfail pin at the end): a HARD LINK to a file BENEATH .git or
.kriya (not to the sentinel) is readable and searchable, because the identity rule compares the target with the control
names themselves and walks its ancestors - a hard link has no control ancestor.
"""
import os

import pytest
from test_s18d_read_view_and_enforce_prompt import SENTINEL, _in_view, dotted  # noqa: F401 - pytest fixture

from kriya.platform.filesystem_semantics import PathRelation, path_relation
from kriya.policy.filesystem import TRUSTED_CONTROL_NAMES, AuthorizedFileWriter, is_trusted_control_path
from kriya.workflow.workflow_types import SubtaskStatus

DENIED = ("TRUSTED_CONTROL_PATH_DENIED",)


def _refused(result):
    return result.status == SubtaskStatus.FAILED and result.reason_codes == DENIED and result.tool_output is None


# -- B21: an identity alias (hard link) of the sentinel -----------------------------------------------------------------

def test_a_hard_link_to_the_sentinel_is_protected_like_the_sentinel(dotted):  # noqa: F811 - pytest fixture
    s = dotted
    alias = os.path.join(s.snapshot, "notes.txt")
    os.link(os.path.join(s.snapshot, SENTINEL), alias)
    assert os.path.samefile(alias, os.path.join(s.snapshot, SENTINEL)) and "notes.txt" not in TRUSTED_CONTROL_NAMES
    assert is_trusted_control_path(s.snapshot, alias), "by filesystem identity, not by name"
    assert _refused(_in_view(s, "snapshot", "filesystem", {"operation": "read", "path": "notes.txt"}))
    assert _refused(_in_view(s, "snapshot", "ast", {"file_path": "notes.txt"}))
    listing = _in_view(s, "snapshot", "filesystem", {"operation": "list", "path": "."})
    assert listing.status == SubtaskStatus.COMPLETED and "notes.txt" not in listing.tool_output
    assert "app.py" in listing.tool_output
    hits = _in_view(s, "snapshot", "search", {"pattern": "scoped", "path": "."})
    assert hits.tool_output == "No matches found.", hits.tool_output
    writer = AuthorizedFileWriter(s.snapshot)
    assert writer.authorize(alias).reason_code == "TRUSTED_CONTROL_PATH_DENIED"


# -- B26 / C8: a stat failure while judging the path is never "safe" ---------------------------------------------------

@pytest.fixture
def locked(dotted):  # noqa: F811 - pytest fixture
    """A symlink in the owner workspace into a mode-000 directory OUTSIDE every root (no root holds its target: the
    judgement's fallback branch) and one into a mode-000 directory INSIDE the owner (held: the innermost-root branch).
    Permissions are restored at teardown."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        pytest.skip("root ignores mode 000: no stat failure can be produced")
    s = dotted
    outside = os.path.join(os.path.dirname(s.owner), "outside_locked")
    inside = os.path.join(s.owner, "locked")
    for directory in (outside, inside):
        os.makedirs(os.path.join(directory, "inner"))
        with open(os.path.join(directory, "inner", "thing"), "w", encoding="utf-8") as handle:
            handle.write("LOCKED_THING\n")
    os.symlink(os.path.join(outside, "inner", "thing"), os.path.join(s.owner, "link_outside"))
    os.symlink(os.path.join(inside, "inner", "thing"), os.path.join(s.owner, "link_inside"))
    os.chmod(outside, 0)
    os.chmod(inside, 0)
    try:
        yield s
    finally:
        os.chmod(outside, 0o755)
        os.chmod(inside, 0o755)


def test_a_path_whose_stat_fails_is_judged_protected_and_left_out(locked):
    s = locked
    # The precondition, MEASURED: the stat really fails (the relation cannot be established).
    for name in ("link_outside", "link_inside"):
        assert path_relation(os.path.join(s.owner, ".git"), os.path.join(s.owner, name)) is PathRelation.UNKNOWN
        assert is_trusted_control_path(s.owner, os.path.join(s.owner, name)), name
    listing = _in_view(s, "owner", "filesystem", {"operation": "list", "path": "."})
    assert listing.status == SubtaskStatus.COMPLETED, listing.error
    names = set(listing.tool_output)
    assert not names & {"link_outside", "link_inside"}, sorted(names)
    # The negative control that makes the pin discriminate: a symlink to an outside file whose stat SUCCEEDS is listed
    # (the fallback answers "not control" for an established outside target), so only the stat failure hides an entry.
    assert {"out_link.txt", "app.py"} <= names
    for name in ("link_outside", "link_inside"):
        read = _in_view(s, "owner", "filesystem", {"operation": "read", "path": name})
        assert read.status == SubtaskStatus.FAILED and read.tool_output is None, read
    hits = _in_view(s, "owner", "search", {"pattern": "LOCKED_THING", "path": "."})
    assert hits.tool_output == "No matches found.", hits.tool_output


# -- B30: a symlink in a SUBFOLDER that resolves into .git --------------------------------------------------------------

@pytest.mark.parametrize("name, link, target", [
    ("snapshot", "docs/cfg_alias", ("..", ".git", "config")),   # relative, inside the scoped snapshot
    ("owner", "docs/cfg_alias", ("..", ".git", "config")),      # relative, in the owner workspace
    ("worktree", "docs/owner_cfg", None),                       # absolute, from the worktree candidate into the owner
])
def test_a_subfolder_symlink_into_git_is_protected(dotted, name, link, target):  # noqa: F811 - pytest fixture
    s = dotted
    root = {"snapshot": s.snapshot, "owner": s.owner, "worktree": s.candidate}[name]
    path = os.path.join(root, *link.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    os.symlink(os.path.join(*target) if target else os.path.join(s.owner, ".git", "config"), path)
    with open(path, encoding="utf-8") as handle:
        assert "repositoryformatversion" in handle.read(), "the link really resolves into a .git/config"
    with open(os.path.join(os.path.dirname(path), "readme.md"), "w", encoding="utf-8") as handle:
        handle.write("DOCS\n")
    listing = _in_view(s, name, "filesystem", {"operation": "list", "path": "docs"})
    assert listing.status == SubtaskStatus.COMPLETED, listing.error
    assert listing.tool_output == ["readme.md"], listing.tool_output
    assert _refused(_in_view(s, name, "filesystem", {"operation": "read", "path": link}))
    hits = _in_view(s, name, "search", {"pattern": "repositoryformatversion", "path": "docs"})
    assert hits.tool_output == "No matches found.", hits.tool_output


# -- MEASURED gap, owner question (not fixed in P2b) --------------------------------------------------------------------

@pytest.mark.parametrize("control_file", [(".git", "config"), (".kriya", "control", "state.json")])
@pytest.mark.xfail(strict=True, reason="OPEN owner question (S18d P2b Q1): a hard link to a file BENEATH .git or .kriya "
                                       "is not recognised by the identity rule; MEASURED readable")
def test_a_hard_link_to_a_file_beneath_a_control_directory_is_protected(dotted, control_file):  # noqa: F811
    s = dotted
    original = os.path.join(s.owner, *control_file)
    os.makedirs(os.path.dirname(original), exist_ok=True)
    if not os.path.exists(original):
        with open(original, "w", encoding="utf-8") as handle:
            handle.write('{"KRIYA_STATE": 1}\n')
    os.link(original, os.path.join(s.owner, "alias.txt"))
    assert _refused(_in_view(s, "owner", "filesystem", {"operation": "read", "path": "alias.txt"}))


def test_a_candidate_write_through_a_hard_link_never_reaches_the_control_file(dotted):  # noqa: F811
    """The write side of the same alias, MEASURED: the writer admits the ordinary name, and Kriya's write is atomic
    (temporary file + os.replace), so it replaces the directory entry and leaves the control file's bytes untouched."""
    from kriya.repository.atomic_write import atomic_write_file

    s = dotted
    original = os.path.join(s.owner, ".kriya", "control", "state.json")
    os.makedirs(os.path.dirname(original), exist_ok=True)
    with open(original, "w", encoding="utf-8") as handle:
        handle.write('{"KRIYA_STATE": 1}\n')
    alias = os.path.join(s.owner, "alias.json")
    os.link(original, alias)
    assert AuthorizedFileWriter(s.owner).authorize(alias).reason_code != "TRUSTED_CONTROL_PATH_DENIED"
    atomic_write_file(alias, "MODEL TEXT\n", workspace_path=s.owner)
    with open(original, encoding="utf-8") as handle:
        assert handle.read() == '{"KRIYA_STATE": 1}\n'
    assert not os.path.samefile(alias, original)
