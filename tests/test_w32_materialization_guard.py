"""W3-2 Commit B: the exact-byte materialization guard (kriya.workflow.worktree._verify_materialized_tracked_bytes).

Host Git configuration is neutralized by the Git authority (Commit A). A REPOSITORY may still declare attributes that rewrite bytes at
checkout (``eol=crlf``, ``text``, ``working-tree-encoding``, ``ident``) - repository-owned semantics that stay active. They may NOT
silently turn "authoritative workspace bytes A, approved" into "sandbox bytes B, edited and verified" while the run reports success as
though A and B were the same candidate. So, before anything is edited in the sandbox, every clean tracked file must hold exactly the
workspace's bytes, or the creation fails closed with the typed ``WORKTREE_CONTENT_MISMATCH`` naming the paths - and neither side is
rewritten to manufacture equality.

The byte-stability invariant, not 'LF everywhere': an intentionally CRLF file under a repository ``eol=crlf`` rule passes.
"""
import os
from pathlib import Path

import pytest

from kriya.workflow import worktree as wt
from kriya.workflow.file_integrity import WORKTREE_CONTENT_MISMATCH
from kriya.workflow.worktree import WorktreeSyncError
from tests.test_w32_host_git_authority import CRLF, HOST_CASES, LF, Host, _g, _sha, make_repo, sandbox_bytes


@pytest.fixture
def host(tmp_path, monkeypatch) -> Host:
    return Host(tmp_path, monkeypatch)


def _commit_attributes(repo: Path, text: str) -> None:
    (repo / ".gitattributes").write_text(text)
    _g(repo, "add", ".gitattributes")
    _g(repo, "commit", "-q", "-m", "attributes")


def _created(repo: Path) -> Path:
    path = Path(wt.create_git_worktree(str(repo)))
    return path


def _expect_mismatch(repo: Path, *names: str) -> WorktreeSyncError:
    with pytest.raises(WorktreeSyncError) as refused:
        wt.create_git_worktree(str(repo))
    assert refused.value.reason_code == WORKTREE_CONTENT_MISMATCH
    for name in names:
        assert name in str(refused.value)
    return refused.value


# ---- KCEP-4 cases ----------------------------------------------------------------------------------------------------------------

def test_case_1_no_repository_attributes_known_lf_stays_exact_lf(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF})
    assert (repo / "f.txt").read_bytes() == LF
    assert (_created(repo) / "f.txt").read_bytes() == LF


@pytest.mark.parametrize("case", sorted(HOST_CASES))
def test_cases_2_3_4_host_configuration_cannot_cause_drift_so_the_guard_passes(tmp_path, host, case):
    spec = HOST_CASES[case]
    repo = make_repo(tmp_path, {"f.txt": LF}, gitattributes=spec["gitattributes"])
    host.configure(**spec["configure"], attributes=spec.get("host_attributes"))
    assert (_created(repo) / "f.txt").read_bytes() == LF


def test_case_5_repository_asks_for_lf_and_the_bytes_are_coherent(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF}, gitattributes="f.txt text eol=lf\n")
    assert (_created(repo) / "f.txt").read_bytes() == LF


def test_case_6_repository_eol_crlf_over_an_lf_workspace_fails_closed_and_rewrites_neither_side(tmp_path, host):
    """The repository starts declaring ``eol=crlf`` while the approved workspace bytes are LF: the checkout would hand the run CRLF."""
    repo = make_repo(tmp_path, {"f.txt": LF, "other.txt": LF})
    _commit_attributes(repo, "f.txt text eol=crlf\n")
    before = {name: _sha((repo / name).read_bytes()) for name in ("f.txt", "other.txt")}
    assert (repo / "f.txt").read_bytes() == LF
    tracked_status = _g(repo, "status", "--porcelain=v1", "--untracked-files=no")
    error = _expect_mismatch(repo, "f.txt")
    assert "other.txt" not in str(error), "an unaffected file was named"
    assert "git add --renormalize" in str(error) and "Nothing was changed" in str(error)
    assert {name: _sha((repo / name).read_bytes()) for name in ("f.txt", "other.txt")} == before     # the workspace was not normalized
    assert _g(repo, "status", "--porcelain=v1", "--untracked-files=no") == tracked_status, "the failed creation touched tracked state"
    sandbox = wt.managed_worktree_path(str(repo))
    assert (Path(sandbox) / "f.txt").read_bytes() == CRLF, "the guard was meant to catch exactly this rewrite (and not repair it)"


def test_case_6_on_the_reuse_path_the_checkout_rewrites_a_changed_file_under_the_new_rule(tmp_path, host):
    """Entry point 2: the sandbox already exists (registered) and the reuse path's ``checkout -f`` runs under the new attributes.
    MEASURED git semantic: a checkout rewrites only the files whose content CHANGED between the two trees, so the rule must arrive
    together with a content change for the drift to materialize."""
    repo = make_repo(tmp_path, {"f.txt": LF, "stable.txt": LF})
    assert (_created(repo) / "f.txt").read_bytes() == LF                     # first creation (a fresh `worktree add`): coherent
    changed = b"alpha\nbeta\ngamma\n"
    (repo / "f.txt").write_bytes(changed)
    (repo / ".gitattributes").write_text("f.txt text eol=crlf\n")
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "rule and change")
    assert (repo / "f.txt").read_bytes() == changed
    error = _expect_mismatch(repo, "f.txt")
    assert "stable.txt" not in str(error)


def test_a_newly_declared_rule_alone_does_not_rewrite_an_already_materialized_file_so_the_guard_passes(tmp_path, host):
    """The twin of the test above, pinning the measured semantic: the reuse checkout leaves an UNCHANGED file's bytes alone, the
    sandbox still equals the workspace, and a guard that rejected 'a repository that declares CRLF' would be wrong here."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    assert (_created(repo) / "f.txt").read_bytes() == LF
    _commit_attributes(repo, "f.txt text eol=crlf\n")
    assert (_created(repo) / "f.txt").read_bytes() == LF


def test_case_7_an_intentionally_crlf_workspace_under_a_crlf_rule_passes(tmp_path, host):
    """Overcorrection control: a repository that declares CRLF is not rejected for declaring it. The blob is LF-normalized, the
    workspace holds the intended CRLF, and the checkout reproduces exactly those bytes."""
    repo = make_repo(tmp_path, {"win.bat": CRLF}, gitattributes="win.bat text eol=crlf\n")
    assert (repo / "win.bat").read_bytes() == CRLF
    assert _g(repo, "status", "--porcelain=v1", "--untracked-files=no").strip() == "", "fixture: the CRLF workspace must be clean"
    assert (_created(repo) / "win.bat").read_bytes() == CRLF


def test_case_8_binary_bytes_are_neither_transformed_nor_falsely_rejected(tmp_path, host):
    blob = b"\x00\r\n\xff\n\x00"
    repo = make_repo(tmp_path, {"data.bin": blob}, gitattributes="*.bin binary\n")
    host.configure(autocrlf="true", eol="crlf", attributes="* text eol=crlf\n")
    assert (_created(repo) / "data.bin").read_bytes() == blob


# ---- scope and authority of the check --------------------------------------------------------------------------------------------

def test_a_dirty_file_is_judged_by_the_syncs_own_proof_not_compared_a_second_time(tmp_path, host, monkeypatch):
    """Even under an ``eol=crlf`` rule the uncommitted workspace file keeps its exact bytes (the sync copies them over the
    checkout); the guard must not compare it through a second, competing authority."""
    repo = make_repo(tmp_path, {"f.txt": LF, "clean.txt": LF}, gitattributes="*.txt text eol=crlf\n")
    # fixture: the workspace holds LF while the rule says CRLF -> every clean .txt would drift; the dirty one is synced exactly
    (repo / "f.txt").write_bytes(b"alpha\nbeta\nuncommitted\n")
    compared = []
    real = wt._same_bytes
    monkeypatch.setattr(wt, "_same_bytes", lambda a, b, *rest: compared.append(os.path.relpath(a, repo)) or real(a, b, *rest))
    _expect_mismatch(repo, "clean.txt")                                      # the clean file drifts and is caught ...
    assert "f.txt" not in compared and "clean.txt" in compared               # ... the synced one is not compared again
    sandbox = Path(wt.managed_worktree_path(str(repo)))
    assert (sandbox / "f.txt").read_bytes() == b"alpha\nbeta\nuncommitted\n"


def test_ignored_and_untracked_output_is_never_scanned(tmp_path, host, monkeypatch):
    repo = make_repo(tmp_path, {"f.txt": LF, ".gitignore": b"target/\n"})
    (repo / "target").mkdir()
    (repo / "target" / "out.class").write_bytes(b"generated\r\n")
    compared = []
    real = wt._same_bytes
    monkeypatch.setattr(wt, "_same_bytes", lambda a, b, *rest: compared.append(os.path.relpath(a, repo)) or real(a, b, *rest))
    _created(repo)
    assert sorted(compared) == [".gitignore", "f.txt"], compared


def test_a_tracked_interpreter_cache_the_sync_never_copies_is_not_claimed_to_match(tmp_path, host):
    """``_not_synced`` paths are rebuilt by the candidate tree by design; comparing them would fail a repository that tracks one."""
    repo = make_repo(tmp_path, {"f.txt": LF, "__pycache__/m.pyc": b"v1"})
    (repo / "__pycache__" / "m.pyc").write_bytes(b"v2-workspace-only")
    assert (_created(repo) / "f.txt").read_bytes() == LF


def test_a_tracked_symlink_is_compared_by_its_target(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF})
    os.symlink("f.txt", repo / "link")
    _g(repo, "add", "link")
    _g(repo, "commit", "-q", "-m", "link")
    sandbox = _created(repo)
    assert os.readlink(sandbox / "link") == "f.txt"


def test_bytes_that_cannot_be_read_fail_closed(tmp_path, host, monkeypatch):
    repo = make_repo(tmp_path, {"f.txt": LF})

    def unreadable(a, b, *rest):
        raise PermissionError("denied")
    monkeypatch.setattr(wt, "_same_bytes", unreadable)
    _expect_mismatch(repo, "f.txt")


def test_same_bytes_is_exact_not_normalized_and_not_size_only(tmp_path):
    def write(name: str, data: bytes) -> str:
        path = tmp_path / name
        path.write_bytes(data)
        return str(path)

    assert wt._same_bytes(write("a", b"abcdefgh"), write("b", b"abcdefgh"), chunk=3)
    assert wt._same_bytes(write("e1", b""), write("e2", b""))
    assert not wt._same_bytes(write("c", b"abcdefgh"), write("d", b"abcdefgX"), chunk=3), "equal size, different last byte"
    assert not wt._same_bytes(write("f", b"abcdefgh"), write("g", b"Xbcdefgh"), chunk=3), "equal size, different first byte"
    assert not wt._same_bytes(write("h", b"a\nb\n"), write("i", b"a\r\nb\r\n")), "normalized-equal text is NOT equal bytes"
    assert not wt._same_bytes(write("j", b"abc"), write("k", b"abcd"))


def test_the_failure_precedes_any_edit_the_path_is_never_returned(tmp_path, host):
    """create_git_worktree is the only door to the sandbox path; on a mismatch it raises, so no caller receives a path to edit."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    _commit_attributes(repo, "f.txt text eol=crlf\n")
    returned = []
    with pytest.raises(WorktreeSyncError):
        returned.append(wt.create_git_worktree(str(repo)))
    assert returned == []


def test_sandbox_bytes_helper_agrees_with_the_guard_on_a_coherent_repository(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF})
    host.configure(autocrlf="true")
    assert sandbox_bytes(repo, "f.txt") == LF
