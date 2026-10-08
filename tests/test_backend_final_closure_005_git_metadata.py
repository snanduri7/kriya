"""OD-1 (BACKEND-FINAL-CLOSURE-005): sanitized, read-only Git metadata for contained builds that deterministically
require it (GRADLE-GIT-AT-CONFIGURATION-BOUNDARY-001: JavaHamcrest's versioning.gradle runs `git describe --tags` at
configuration time; Kriya presents candidate trees as "no repository").

Owner boundary: commits, reachable tags and refs of the exact baseline only - no trees/blobs, remotes, credentials,
hooks, reflogs, stash, alternates, index; bound to workspace identity + base revision, content-addressed, read-only;
stale/wrong-workspace metadata fails closed with a typed reason; the no-Git mask stays the fallback; a kill switch.

Host-level tests build the export from a real repository carrying everything the boundary excludes; the Docker tests
run the exact contained shape (the pinned gradle:8-jdk8 image the T3 cohort used) including the Gradle
configuration-time `git describe`.
"""
import json
import os
import shutil
import subprocess

import pytest

from kriya.config.config import AutonomyConfig
from kriya.tools import git_metadata as gm
from kriya.tools.containment import ContainmentProfile, GitMetadataMount, NetworkAuthority, TrustClass
from kriya.tools.containment_oci import (
    _CONTAINER_WORKSPACE,
    OCIContainmentBackend,
    _git_metadata_env,
    git_metadata_mount_args,
)
from kriya.tools.process import ProcessController
from kriya.tools.toolchain_identity import ToolchainIdentity


def _git(cwd, *args, check=True):
    return subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=cwd, check=check,
                          capture_output=True, text=True)


def _out(cwd, *args):
    return _git(cwd, *args).stdout.strip()


def _source_repo(tmp_path):
    """A repository carrying everything the export must leave behind: an unreachable branch with its own tag, a
    remote, a credential helper, a hook, a stash and reflogs. Returns (repo, base, the source's own describe)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "symbolic-ref", "HEAD", "refs/heads/main")
    (repo / "build.gradle").write_text("// build\n")
    (repo / "README.md").write_text("one\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "c1")
    _git(repo, "tag", "-a", "v1.0", "-m", "release 1.0")
    (repo / "README.md").write_text("two\n")
    _git(repo, "commit", "-qam", "c2")
    _git(repo, "tag", "snapshot")  # lightweight, reachable
    (repo / "README.md").write_text("three\n")
    _git(repo, "commit", "-qam", "c3")
    base = _out(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "-b", "other", "v1.0")
    (repo / "OTHER.md").write_text("private work\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "o1")
    _git(repo, "tag", "-a", "v9.9", "-m", "unreachable from main")
    _git(repo, "checkout", "-q", "main")
    _git(repo, "remote", "add", "origin", "https://example.invalid/private.git")
    _git(repo, "config", "credential.helper", "store")
    (repo / ".git" / "hooks").mkdir(exist_ok=True)
    (repo / ".git" / "hooks" / "pre-commit").write_text("#!/bin/sh\nexit 1\n")
    (repo / "README.md").write_text("uncommitted\n")
    _git(repo, "stash", "-q")
    assert _out(repo, "rev-parse", "HEAD") == base
    return repo, base, _out(repo, "describe", "--tags")


def _types(repo_dir):
    return sorted({line.split()[1] for line in _out(repo_dir, "cat-file", "--batch-all-objects",
                                                         "--batch-check=%(objectname) %(objecttype)").splitlines()})


# ---------------------------------------------------------------- host level: the export and its boundary
def test_h1_the_export_is_the_baseline_commit_graph_and_reachable_tags_and_nothing_else(tmp_path):
    repo, base, describe = _source_repo(tmp_path)
    state = tmp_path / "state"
    export = gm.export_git_metadata(str(repo), str(state), identity_path=str(repo))
    assert export.base_revision == base and export.root.startswith(str(state.resolve()))
    r = export.repo_dir
    assert _out(r, "rev-parse", "HEAD") == base
    assert _out(r, "describe", "--tags", base) == describe  # the measured operation, same answer as the source
    assert _out(r, "rev-list", "--count", base) == _out(repo, "rev-list", "--count", base) == "3"
    assert _types(r) == ["commit", "tag"]  # no tree, no blob: historical source content is not exported
    unreachable = _out(repo, "rev-parse", "other")
    assert _git(r, "cat-file", "-e", unreachable, check=False).returncode != 0
    assert sorted(export.refs) == ["refs/heads/kriya-base", "refs/tags/snapshot", "refs/tags/v1.0"]
    assert _git(r, "rev-parse", "--verify", "refs/tags/v9.9", check=False).returncode != 0
    assert _git(r, "rev-parse", "--verify", "refs/stash", check=False).returncode != 0
    config = _out(r, "config", "--list", "--file", os.path.join(r, "config"))
    assert "remote." not in config and "credential." not in config and "core.bare=false" in config
    for entry in ("hooks", "logs", "index", "info", "description"):
        assert not os.path.lexists(os.path.join(r, entry)), entry
    assert not os.path.exists(os.path.join(r, "objects", "info", "alternates"))
    assert open(export.gitfile_path).read() == f"gitdir: {gm.CONTAINER_GIT_METADATA}\n"
    manifest = json.load(open(os.path.join(export.root, "manifest.json")))
    assert manifest["policy"] == "commits-and-tags-only" and manifest["object_count"] == 4  # 3 commits + 1 tag object
    assert manifest["digest"] == export.digest == gm._export_digest(r)  # pylint: disable=protected-access
    # pure: the same baseline reuses the same verified export, same digest
    again = gm.export_git_metadata(str(repo), str(state), identity_path=str(repo))
    assert again == export
    assert os.listdir(os.path.dirname(export.root)) == [base]  # no build leftovers


def test_h2_a_worktree_pointed_at_the_export_answers_describe_and_rev_parse_and_refuses_tree_walks(tmp_path):
    """The container shape, on the host: the tree plus a gitfile naming the export."""
    repo, base, describe = _source_repo(tmp_path)
    export = gm.export_git_metadata(str(repo), str(tmp_path / "state"), identity_path=str(repo))
    tree = tmp_path / "tree"
    shutil.copytree(repo, tree, ignore=shutil.ignore_patterns(".git"))
    (tree / ".git").write_text(f"gitdir: {export.repo_dir}\n")
    assert _out(tree, "describe", "--tags") == describe
    assert _out(tree, "rev-parse", "HEAD") == base
    assert _out(tree, "rev-parse", "--short", "HEAD") == base[:7]
    assert _out(tree, "log", "--format=%s", "-3") == "c3\nc2\nc1"
    assert _out(tree, "remote") == ""
    # historical content needs authority the export does not carry: fail closed, never a guess
    assert _git(tree, "show", "HEAD:README.md", check=False).returncode != 0
    assert _git(tree, "describe", "--tags", "--dirty", check=False).returncode != 0
    assert _git(tree, "status", "--porcelain", check=False).returncode != 0


def test_h3_a_stale_wrong_workspace_or_altered_export_is_a_typed_refusal(tmp_path):
    repo, base, _d = _source_repo(tmp_path)
    state = tmp_path / "state"
    export = gm.export_git_metadata(str(repo), str(state), identity_path=str(repo))
    with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_WORKSPACE_MISMATCH):
        gm.verify_git_metadata(export.root, identity_path=str(tmp_path / "elsewhere"), base_revision=base)
    with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_STALE):
        gm.verify_git_metadata(export.root, identity_path=str(repo), base_revision="0" * 40)
    # the workspace moved on: the old export is stale for the new head; a fresh export binds the new head
    (repo / "README.md").write_text("four\n")
    _git(repo, "commit", "-qam", "c4")
    head = _out(repo, "rev-parse", "HEAD")
    with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_STALE):
        gm.verify_git_metadata(export.root, identity_path=str(repo), base_revision=head)
    fresh = gm.export_git_metadata(str(repo), str(state), identity_path=str(repo))
    assert fresh.base_revision == head and fresh.root != export.root
    # alterations of the stored export: an added hook, a remote, a changed gitfile, a changed digest
    for alter in ("hook", "remote", "gitfile", "digest"):
        altered = gm.export_git_metadata(str(repo), str(tmp_path / f"state-{alter}"), identity_path=str(repo))
        if alter == "hook":
            os.makedirs(os.path.join(altered.repo_dir, "hooks"))
            open(os.path.join(altered.repo_dir, "hooks", "post-checkout"), "w").write("#!/bin/sh\n")
        elif alter == "remote":
            _git(altered.repo_dir, "config", "--file", os.path.join(altered.repo_dir, "config"),
                 "remote.origin.url", "https://example.invalid/x.git")
        elif alter == "gitfile":
            open(altered.gitfile_path, "w").write("gitdir: /etc\n")
        else:
            path = os.path.join(altered.root, "manifest.json")
            manifest = json.load(open(path))
            manifest["digest"] = "0" * 64
            json.dump(manifest, open(path, "w"))
        with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_CORRUPT):
            gm.verify_git_metadata(altered.root, identity_path=str(repo), base_revision=head)
    with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_EXPORT_FAILED):
        gm.export_git_metadata(str(tmp_path / "plain-dir"), str(state), identity_path=str(tmp_path))


def test_h4_the_binding_resolves_the_checkout_the_identity_and_the_switch(tmp_path):
    repo, base, _d = _source_repo(tmp_path)
    state = str(tmp_path / "state")
    assert gm.bound_git_metadata(mounted_workspace=str(repo), original_workspace=None, state_root=state, enabled=False) is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("a\n")
    assert gm.bound_git_metadata(mounted_workspace=str(plain), original_workspace=None, state_root=state, enabled=True) is None
    # a worktree (the gates' candidate tree): bound to its own HEAD
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), base)
    mount = gm.bound_git_metadata(mounted_workspace=str(worktree), original_workspace=str(worktree), state_root=state, enabled=True)
    assert isinstance(mount, GitMetadataMount) and mount.base_revision == base and os.path.isdir(mount.repo_dir)
    # a Kriya-owned copy without .git (an authority run): bound through the run's checkout
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
    via_original = gm.bound_git_metadata(mounted_workspace=str(copy), original_workspace=str(repo), state_root=state, enabled=True)
    assert via_original is not None and via_original.base_revision == base and via_original.digest == mount.digest
    assert gm.bound_git_metadata(mounted_workspace=str(copy), original_workspace=None, state_root=state, enabled=True) is None
    # a mounted checkout at another revision than the run's workspace: stale, typed
    (repo / "README.md").write_text("four\n")
    _git(repo, "commit", "-qam", "c4")
    with pytest.raises(gm.GitMetadataError, match=gm.GIT_METADATA_STALE):
        gm.bound_git_metadata(mounted_workspace=str(worktree), original_workspace=str(repo), state_root=state, enabled=True)


def test_h5_the_oci_arguments_mount_only_the_export_read_only_in_every_workspace_shape(tmp_path):
    repo, base, _d = _source_repo(tmp_path)
    export = gm.export_git_metadata(str(repo), str(tmp_path / "state"), identity_path=str(repo))
    mount = export.mount()
    bound = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(repo),
                               network=NetworkAuthority.DENIED, git_metadata=mount)
    unbound = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(repo),
                                 network=NetworkAuthority.DENIED)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), base)
    git_at = f"{_CONTAINER_WORKSPACE}/.git"
    # a worktree: the Kriya gitfile masks the dangling pointer, the export is mounted beside the workspace
    assert git_metadata_mount_args(bound, str(worktree)) == [
        "--mount", f"type=bind,src={export.gitfile_path},dst={git_at},readonly",
        "--mount", f"type=bind,src={export.repo_dir},dst={gm.CONTAINER_GIT_METADATA},readonly"]
    # a real .git directory is hidden under the export; a copy without .git gets the export as .git
    assert git_metadata_mount_args(bound, str(repo)) == ["--mount", f"type=bind,src={export.repo_dir},dst={git_at},readonly"]
    copy = tmp_path / "copy"
    shutil.copytree(repo, copy, ignore=shutil.ignore_patterns(".git"))
    assert git_metadata_mount_args(bound, str(copy)) == ["--mount", f"type=bind,src={export.repo_dir},dst={git_at},readonly"]
    # no export bound: exactly the pre-existing mask (a worktree masked, a real directory left as is)
    assert git_metadata_mount_args(unbound, str(worktree)) == ["--mount", f"type=bind,src=/dev/null,dst={git_at},readonly"]
    assert git_metadata_mount_args(unbound, str(repo)) == []
    assert _git_metadata_env(unbound) == {}
    env = _git_metadata_env(bound)
    assert env["GIT_CONFIG_COUNT"] == "2" and set(env.values()) >= {"safe.directory", _CONTAINER_WORKSPACE, gm.CONTAINER_GIT_METADATA}
    # an export that vanished from the host is a refusal, never a silent mask
    shutil.rmtree(export.root)
    with pytest.raises(Exception, match="GIT_METADATA_EXPORT_MISSING"):
        git_metadata_mount_args(bound, str(worktree))


def test_h6_the_validator_binds_the_export_into_its_containment_profile(tmp_path, monkeypatch):
    from kriya.tools import validate as validate_module

    repo, base, _d = _source_repo(tmp_path)
    worktree = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(worktree), base)
    monkeypatch.setenv("KRIYA_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(validate_module, "resolve_toolchain_selection", lambda *a, **k: None)
    cfg = AutonomyConfig(contained_execution_required=True, containment_backend="oci")
    assert cfg.git_metadata_export is True  # the production default
    validator = validate_module.PolymorphicValidator(str(worktree), original_workspace_path=str(worktree), autonomy_cfg=cfg)
    profile, _backend = validator.build_containment_profile_and_backend()
    assert profile.git_metadata is not None and profile.git_metadata.base_revision == base
    assert profile.git_metadata.repo_dir.startswith(str((tmp_path / "state").resolve()))
    again, _b = validator.build_containment_profile_and_backend()
    assert again.git_metadata == profile.git_metadata  # resolved once per mounted tree
    off = validate_module.PolymorphicValidator(
        str(worktree), original_workspace_path=str(worktree),
        autonomy_cfg=AutonomyConfig(contained_execution_required=True, containment_backend="oci", git_metadata_export=False))
    assert off.build_containment_profile_and_backend()[0].git_metadata is None
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.py").write_text("x = 1\n")
    none = validate_module.PolymorphicValidator(str(plain), original_workspace_path=str(plain), autonomy_cfg=cfg)
    assert none.build_containment_profile_and_backend()[0].git_metadata is None


def test_h7_the_switch_is_a_security_authority_field_a_repository_cannot_set():
    from kriya.config.authority import _SECURITY_AUTHORITY_FIELDS

    assert ("autonomy", "git_metadata_export") in _SECURITY_AUTHORITY_FIELDS


# ---------------------------------------------------------------- the real contained shape (pinned gradle:8-jdk8)
def _docker_reachable() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=10).returncode == 0
    except Exception:
        return False


T3_IDENTITY = ToolchainIdentity("java", "jdk", "8", "gradle", "8", "gradle:8-jdk8", "build.gradle")
GRADLE_BUILD = (
    "def gitVersion() {\n"
    "    def out = new ByteArrayOutputStream()\n"
    "    exec { commandLine 'git', 'describe', '--tags'; standardOutput = out }\n"
    "    return out.toString().trim()\n"
    "}\n"
    "tasks.register('printVersion') { doLast { println 'VERSION=' + gitVersion() } }\n"
)


def _contained(workspace, command, mount, timeout=120):
    profile = ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace),
                                 network=NetworkAuthority.DENIED, toolchain_identity=T3_IDENTITY, git_metadata=mount)
    return ProcessController().run(command, cwd=".", timeout=timeout, containment_profile=profile,
                                   containment_backend=OCIContainmentBackend())


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
@pytest.mark.parametrize("shape", ["worktree", "copy", "directory"])
def test_d1_a_contained_build_sees_describe_and_rev_parse_and_nothing_mutable(tmp_path, shape):
    repo, base, describe = _source_repo(tmp_path)
    export = gm.export_git_metadata(str(repo), str(tmp_path / "state"), identity_path=str(repo))
    if shape == "worktree":
        workspace = tmp_path / "wt"
        _git(repo, "worktree", "add", "-q", "--detach", str(workspace), base)
        pointer = (workspace / ".git").read_text()
    elif shape == "copy":
        workspace = tmp_path / "copy"
        shutil.copytree(repo, workspace, ignore=shutil.ignore_patterns(".git"))
    else:
        workspace = repo
    script = ("git describe --tags && git rev-parse HEAD && git remote | wc -l && git rev-parse --verify -q refs/stash; "
              "echo stash=$?; git show HEAD:README.md >/dev/null 2>&1; echo show=$?; "
              "git describe --tags --dirty >/dev/null 2>&1; echo dirty=$?; "
              "ls .git/hooks >/dev/null 2>&1; echo hooks=$?; echo tampered > .git/HEAD 2>/dev/null; echo write=$?")
    result = _contained(workspace, ["/bin/sh", "-c", script], export.mount())
    assert result.returncode == 0, result.stderr
    lines = result.stdout.split()
    assert lines[0] == describe and lines[1] == base and lines[2] == "0", result.stdout
    assert "stash=1" in lines and "show=128" in lines and "dirty=128" in lines, result.stdout  # fail closed, no trees
    assert "hooks=2" in lines or "hooks=1" in lines, result.stdout  # no hooks directory
    assert "write=1" in lines or "write=2" in lines, result.stdout  # read-only
    assert gm._export_digest(export.repo_dir) == export.digest  # pylint: disable=protected-access
    if shape == "worktree":
        assert (workspace / ".git").read_text() == pointer  # the host's pointer is untouched
    if shape == "directory":
        assert os.path.isfile(repo / ".git" / "hooks" / "pre-commit")  # the host's own .git never reached the container
    # the switch off: the pre-existing behaviour - a worktree or a copy reads as no repository; a plain checkout's
    # real .git directory was always left in place (test_containment_gitfile_mask), remote and all - which the bound
    # export above hides (remote count 0)
    unbound = _contained(workspace, ["/bin/sh", "-c", "git describe --tags && git remote"], None)
    if shape == "directory":
        assert unbound.returncode == 0 and "origin" in unbound.stdout, (unbound.stdout, unbound.stderr)
    else:
        assert unbound.returncode != 0 and "not a git repository" in (unbound.stderr + unbound.stdout).lower()


@pytest.mark.skipif(not _docker_reachable(), reason="docker daemon not reachable")
def test_d2_gradle_resolves_a_configuration_time_git_describe_from_the_export(tmp_path):
    """The exact T3 mechanism: a Gradle script that execs `git describe --tags` while configuring."""
    repo, base, describe = _source_repo(tmp_path)
    (repo / "build.gradle").write_text(GRADLE_BUILD)
    (repo / "settings.gradle").write_text("rootProject.name = 'versioned'\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "gradle build")
    head = _out(repo, "rev-parse", "HEAD")
    assert head != base
    describe = _out(repo, "describe", "--tags")
    export = gm.export_git_metadata(str(repo), str(tmp_path / "state"), identity_path=str(repo))
    workspace = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", "--detach", str(workspace), head)
    command = ["gradle", "-q", "--offline", "--no-daemon", "printVersion"]
    result = _contained(workspace, command, export.mount(), timeout=600)
    assert result.returncode == 0, result.stderr
    assert f"VERSION={describe}" in result.stdout, result.stdout
    unbound = _contained(workspace, command, None, timeout=600)
    assert unbound.returncode != 0 and "not a git repository" in (unbound.stderr + unbound.stdout).lower()
