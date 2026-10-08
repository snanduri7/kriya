"""Live matrix, spring-petclinic (run b5a9...): every contained compile failed
in git-commit-id-maven-plugin - "Could not get HEAD Ref ... dotGitDirectory
(currently set to /Users/.../.git/worktrees/candidate-...)" - although the
project sets failOnNoGitDirectory=false and builds fine without git. Kriya's
candidate tree is a git worktree: its .git FILE points at the repository's
gitdir on the host, which the container does not mount. The dangling pointer
is now masked inside the container (the tree reads as "no repository");
nothing outside the workspace is exposed.
"""
import os
import shutil
import subprocess

import pytest

from kriya.tools.containment import ContainmentProfile, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import OCIContainmentBackend, dangling_gitfile_mask
from kriya.tools.process import ProcessController


def _git(cwd, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=True,
                   capture_output=True)


def test_only_a_pointer_outside_the_mount_is_masked(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    assert dangling_gitfile_mask(str(repo)) == []  # a real .git directory
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    assert dangling_gitfile_mask(str(worktree)) == ["--mount", "type=bind,src=/dev/null,dst=/kriya/workspace/.git,readonly"]
    inside = tmp_path / "self"
    (inside / "meta").mkdir(parents=True)
    (inside / ".git").write_text("gitdir: meta\n")
    assert dangling_gitfile_mask(str(inside)) == []  # resolves inside the mount
    plain = tmp_path / "plain"
    plain.mkdir()
    assert dangling_gitfile_mask(str(plain)) == []
    (plain / ".git").write_text("not a gitfile\n")
    assert dangling_gitfile_mask(str(plain)) == []


def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_a_contained_worktree_reads_as_no_repository_not_a_dangling_pointer(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.txt").write_text("a\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree))
    pointer = (worktree / ".git").read_text()
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(worktree),
                                 network=NetworkAuthority.DENIED)
    result = ProcessController().run(
        ["/bin/sh", "-c", "wc -c < .git; cat a.txt; echo injected > .git"],
        cwd=".", timeout=60, containment_profile=profile, containment_backend=OCIContainmentBackend())
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split()
    assert lines[0] == "0" and "gitdir" not in result.stdout  # no pointer to a path the container lacks
    assert "a" in lines  # the tree itself is the workspace
    assert (worktree / ".git").read_text() == pointer  # a write through the mask never reaches the host
    assert os.path.isdir(repo / ".git" / "worktrees")


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_an_ordinary_repository_keeps_its_git_directory(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(repo),
                                 network=NetworkAuthority.DENIED)
    result = ProcessController().run(["/bin/sh", "-c", "test -d .git && echo has-git-dir"], cwd=".", timeout=60,
                                     containment_profile=profile, containment_backend=OCIContainmentBackend())
    assert "has-git-dir" in result.stdout, result.stderr


def test_bind_mounts_use_the_named_mount_syntax_so_colons_and_drive_letters_survive(tmp_path):
    """PLAT-OCI-MOUNT-SYNTAX-001 (PLAT-013): a host path with ':' (a Windows drive letter, any colon) is not split
    into host:container:mode; a path the CSV --mount syntax cannot carry (',') is refused, never mangled."""
    import pytest

    from kriya.tools.containment import BackendUnavailableError
    from kriya.tools.containment_oci import bind_mount_args

    assert bind_mount_args("/Users/me/ws", "/kriya/workspace", writable=True) == ["--mount", "type=bind,src=/Users/me/ws,dst=/kriya/workspace"]
    assert bind_mount_args("/tmp/a:b", "/kriya/cache/0", writable=False) == ["--mount", "type=bind,src=/tmp/a:b,dst=/kriya/cache/0,readonly"]
    assert bind_mount_args(r"C:\Users\me\ws", "/kriya/workspace", writable=True)[1] == r"type=bind,src=C:\Users\me\ws,dst=/kriya/workspace"
    with pytest.raises(BackendUnavailableError, match="MOUNT_PATH_NOT_EXPRESSIBLE"):
        bind_mount_args("/tmp/a,b", "/kriya/workspace", writable=True)
