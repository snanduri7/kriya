"""W3-2 (GIT-AUTOCRLF-HOST-CONFIG-CANDIDATE-BYTES-001): host Git configuration and environment have ZERO authority over the bytes
Kriya materializes and the identity it records.

Reproduces the MEASURED mechanisms, not an implementation summary of them (ENGINEERING_RULES 4/7):

- host ``core.autocrlf=true`` rewrote an LF file to CRLF in the sandbox of a green run (calc.py: 0 -> 6 CRLF lines);
- host ``core.eol=crlf`` did so under a repository ``* text=auto``;
- a host attributes file (``$XDG_CONFIG_HOME/git/attributes``: ``* text eol=crlf``) did so under both pins above;
- under host ``autocrlf=true`` the checkpoint tree (``read-tree`` / ``add -A`` / ``write-tree``) of a CRLF-rewritten working file
  equalled the HEAD tree: the evidence could not see the rewrite;
- ``GIT_LITERAL_PATHSPECS=1`` made ``add -A -- . ':!.kriya'`` fatal (the evidence hashes became None);
- an effective ``attr.tree`` (any scope) redirects attribute resolution and has no documented neutral value: refused, typed.

Every fixture is built from exact bytes with git pinned neutral, and its bytes are asserted immediately before the run, so a fixture
that was already normalized cannot make a test green (the developer's own global gitconfig is never read: GIT_CONFIG_GLOBAL,
HOME and XDG_CONFIG_HOME are isolated per test).
"""
import asyncio
import hashlib
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

import pytest

from kriya.config.environment import child_environment
from kriya.platform import GIT_FORCED_ENVIRONMENT, GIT_STRIPPED_ENVIRONMENT, git_argv, git_environment
from kriya.repository.git import (
    GIT_ATTR_TREE_CONFIGURED,
    GIT_CONTENT_FILTER_REPOSITORY_DEFINED,
    RepositoryContentFilterRefused,
    refuse_repository_content_filters,
)
from kriya.workflow import worktree as wt
from kriya.workflow.checkpoint import compute_workspace_content_hash
from kriya.workflow.milestone_completion import workspace_evidence_hash

LF = b"alpha\nbeta\n"
CRLF = b"alpha\r\nbeta\r\n"
_NEUTRAL = ["-c", "core.autocrlf=false", "-c", "core.eol=lf", "-c", f"core.attributesFile={os.devnull}",
            "-c", "user.name=T", "-c", "user.email=t@example.com"]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _neutral_env(base: Dict[str, str]) -> Dict[str, str]:
    env = dict(base)
    env.update({"GIT_ATTR_NOSYSTEM": "1", "GIT_CONFIG_NOSYSTEM": "1"})
    env.pop("GIT_ATTR_SOURCE", None)
    return env


class Host:
    """The (isolated) host Git configuration a test installs: what a developer's HOME would contribute."""

    def __init__(self, root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.home = root / "home"
        self.xdg = self.home / ".config"
        (self.xdg / "git").mkdir(parents=True)
        self.gitconfig = self.home / ".gitconfig"
        self.monkeypatch = monkeypatch
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_ATTR_SOURCE", "GIT_LITERAL_PATHSPECS", "GIT_CONFIG_SYSTEM",
                     "GIT_CONFIG_COUNT", "GIT_CONFIG_PARAMETERS", "GIT_ATTR_NOSYSTEM"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("HOME", str(self.home))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(self.xdg))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(self.gitconfig))
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        self.configure()

    def configure(self, *, autocrlf: Optional[str] = None, eol: Optional[str] = None, attributes: Optional[str] = None,
                  attr_tree: Optional[str] = None) -> None:
        lines = ["[user]", "\tname = T", "\temail = t@example.com", "[core]"]
        if autocrlf is not None:
            lines.append(f"\tautocrlf = {autocrlf}")
        if eol is not None:
            lines.append(f"\teol = {eol}")
        if attr_tree is not None:
            lines += ["[attr]", f"\ttree = {attr_tree}"]
        self.gitconfig.write_text("\n".join(lines) + "\n")
        attributes_file = self.xdg / "git" / "attributes"
        if attributes is None:
            attributes_file.unlink(missing_ok=True)
        else:
            attributes_file.write_text(attributes)


@pytest.fixture
def host(tmp_path, monkeypatch) -> Host:
    return Host(tmp_path, monkeypatch)


def _g(repo: Path, *args: str) -> str:
    """Fixture-building git: every host input neutralized, so the fixture's bytes are exactly what the test wrote."""
    done = subprocess.run(["git", *_NEUTRAL, *args], cwd=repo, check=True, capture_output=True, text=True,
                          env=_neutral_env({**os.environ, "GIT_CONFIG_GLOBAL": os.devnull}))
    return done.stdout


def make_repo(root: Path, files: Dict[str, bytes], *, gitattributes: Optional[str] = None, name: str = "repo") -> Path:
    repo = root / name
    repo.mkdir()
    _g(repo, "init", "-q")
    if gitattributes is not None:
        (repo / ".gitattributes").write_text(gitattributes)
    for relative, data in files.items():
        (repo / relative).parent.mkdir(parents=True, exist_ok=True)
        (repo / relative).write_bytes(data)
    _g(repo, "add", "-A")
    _g(repo, "commit", "-q", "-m", "base")
    for relative, data in files.items():                  # the fixture's exact bytes, asserted before anything runs
        assert (repo / relative).read_bytes() == data, f"fixture {relative} was not written as intended"
    return repo


def sandbox_bytes(repo: Path, relative: str) -> bytes:
    worktree = wt.create_git_worktree(str(repo))
    try:
        return (Path(worktree) / relative).read_bytes()
    finally:
        wt.remove_git_worktree(str(repo), worktree)


HOST_CASES = {
    "autocrlf-true": dict(configure=dict(autocrlf="true"), gitattributes=None),
    "core-eol-crlf-with-repo-text-auto": dict(configure=dict(autocrlf="false", eol="crlf"), gitattributes="* text=auto\n"),
    "host-attributes-file-eol-crlf": dict(configure=dict(autocrlf="false"), gitattributes=None, host_attributes="* text eol=crlf\n"),
    "all-three": dict(configure=dict(autocrlf="true", eol="crlf"), gitattributes="* text=auto\n", host_attributes="* text eol=crlf\n"),
}


@pytest.mark.parametrize("case", sorted(HOST_CASES))
def test_sandbox_bytes_are_the_committed_lf_bytes_whatever_the_host_configures(tmp_path, host, case):
    spec = HOST_CASES[case]
    repo = make_repo(tmp_path, {"f.txt": LF, "g.txt": LF}, gitattributes=spec["gitattributes"])
    host.configure(**spec["configure"], attributes=spec.get("host_attributes"))
    before = {name: _sha((repo / name).read_bytes()) for name in ("f.txt", "g.txt")}
    assert before == {"f.txt": _sha(LF), "g.txt": _sha(LF)}
    assert sandbox_bytes(repo, "f.txt") == LF
    assert sandbox_bytes(repo, "g.txt") == LF
    assert {name: _sha((repo / name).read_bytes()) for name in ("f.txt", "g.txt")} == before     # the workspace is untouched too


@pytest.mark.parametrize("configure", [dict(), dict(autocrlf="true"), dict(autocrlf="false", eol="crlf")],
                         ids=["no-host-config", "autocrlf-true", "core-eol-crlf"])
def test_positive_control_intentional_crlf_stays_crlf(tmp_path, host, configure):
    """Byte stability, not 'LF everywhere': a file whose authoritative bytes are CRLF is materialized as exactly those bytes."""
    repo = make_repo(tmp_path, {"win.bat": CRLF, "unix.sh": LF})
    host.configure(**configure)
    assert sandbox_bytes(repo, "win.bat") == CRLF
    assert sandbox_bytes(repo, "unix.sh") == LF


def test_binary_bytes_are_preserved(tmp_path, host):
    blob = b"\x00\r\n\xff\n\x00"
    repo = make_repo(tmp_path, {"data.bin": blob}, gitattributes="*.bin binary\n")
    host.configure(autocrlf="true", eol="crlf", attributes="* text eol=crlf\n")
    assert sandbox_bytes(repo, "data.bin") == blob


def test_repository_owned_gitattributes_stay_active(tmp_path, host):
    """Host isolation must not disable the repository's own semantics. With ``eol=crlf`` in the repository's .gitattributes and an
    intentionally CRLF workspace (the one shape the Commit B exact-byte guard admits), the sandbox is CRLF because the repository's
    rule is applied; were the rule ignored, the checkout would come out LF, differ from the workspace's CRLF, and be refused."""
    repo = make_repo(tmp_path, {"f.txt": CRLF}, gitattributes="f.txt text eol=crlf\n")
    host.configure(autocrlf="false")
    assert (repo / "f.txt").read_bytes() == CRLF
    assert sandbox_bytes(repo, "f.txt") == CRLF


# ---- checkpoint / evidence identity (KCEP-6) -------------------------------------------------------------------------------------

HASHES = {"checkpoint": compute_workspace_content_hash, "milestone-evidence": workspace_evidence_hash}


@pytest.mark.parametrize("which", sorted(HASHES))
def test_evidence_identity_is_host_independent_and_cannot_hide_a_crlf_rewrite(tmp_path, host, which):
    """MEASURED pre-fix: under host autocrlf=true the hash of a CRLF-rewritten working file equalled the clean hash."""
    hash_of = HASHES[which]
    repo = make_repo(tmp_path, {"f.txt": LF, "g.txt": LF})
    clean, rewritten = {}, {}
    for autocrlf in ("false", "true"):
        host.configure(autocrlf=autocrlf)
        assert (repo / "f.txt").read_bytes() == LF
        clean[autocrlf] = hash_of(str(repo))
    (repo / "f.txt").write_bytes(CRLF)                        # the byte rewrite the evidence must see
    assert (repo / "f.txt").read_bytes() == CRLF and (repo / "g.txt").read_bytes() == LF
    for autocrlf in ("false", "true"):
        host.configure(autocrlf=autocrlf)
        rewritten[autocrlf] = hash_of(str(repo))
    assert None not in (*clean.values(), *rewritten.values())
    assert clean["false"] == clean["true"]                    # deterministic across host configurations
    assert rewritten["false"] == rewritten["true"]
    assert rewritten["true"] != clean["true"], "the CRLF rewrite is invisible to the evidence identity"


@pytest.mark.parametrize("which", sorted(HASHES))
def test_evidence_identity_ignores_a_hostile_inherited_environment(tmp_path, host, which):
    """GIT_LITERAL_PATHSPECS made the ':!.kriya' exclusion fatal (hash None); GIT_ATTR_SOURCE re-routed attributes."""
    hash_of = HASHES[which]
    repo = make_repo(tmp_path, {"f.txt": LF})
    (repo / ".kriya").mkdir()
    (repo / ".kriya" / "state").write_text("control\n")
    reference = hash_of(str(repo))
    assert reference is not None
    _g(repo, "tag", "crlfattrs", _g(repo, "rev-parse", "HEAD").strip())
    host.monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    host.monkeypatch.setenv("GIT_ATTR_SOURCE", "crlfattrs")
    assert hash_of(str(repo)) == reference


# ---- the environment half of the authority --------------------------------------------------------------------------------------

def test_git_environment_contract():
    base = {"PATH": "/bin", "HOME": "/h", "GIT_ATTR_SOURCE": "t", "GIT_LITERAL_PATHSPECS": "1", "GIT_CONFIG_COUNT": "1",
            "GIT_DIR": "/r/.git", "GIT_INDEX_FILE": "/i", "GIT_ATTR_NOSYSTEM": "0"}
    snapshot = dict(base)
    environment = git_environment(base)
    assert base == snapshot, "the caller's mapping was mutated"
    assert environment is not base
    assert environment["GIT_ATTR_NOSYSTEM"] == "1" and GIT_FORCED_ENVIRONMENT == {"GIT_ATTR_NOSYSTEM": "1"}
    assert not set(GIT_STRIPPED_ENVIRONMENT) & set(environment) and set(GIT_STRIPPED_ENVIRONMENT) == {"GIT_ATTR_SOURCE", "GIT_LITERAL_PATHSPECS"}
    for retained in ("PATH", "HOME", "GIT_CONFIG_COUNT", "GIT_DIR", "GIT_INDEX_FILE"):     # owner decisions: not sanitized
        assert environment[retained] == base[retained]
    assert git_environment(environment) == environment                                      # idempotent
    assert git_environment({}) == {"GIT_ATTR_NOSYSTEM": "1"}


def test_the_argv_options_name_the_null_device_through_os_devnull():
    argv = git_argv(["git", "status"])
    assert argv[0] == "git" and argv[-1] == "status"
    leading = argv[1:-1]
    assert leading[0::2] == ["-c"] * (len(leading) // 2) and len(leading) % 2 == 0, f"global options must be -c pairs: {leading}"
    pairs = leading[1::2]
    assert {"core.autocrlf=false", "core.eol=lf", f"core.attributesFile={os.devnull}", f"core.hooksPath={os.devnull}"} <= set(pairs)
    assert len(pairs) == len(set(pairs)), "a pinned key is repeated"


class _SpawnRecorder:
    """Records (subcommand, environment) of every real git spawn made through ``subprocess.run`` while installed, then runs it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.spawns: List[tuple] = []
        real_run = subprocess.run
        recorder = self

        def run(args, *a, **kw):
            if args and os.path.basename(str(args[0])) == "git":
                words = [word for word in args[1:] if not word.startswith("-") and "=" not in word]
                recorder.spawns.append((words[0] if words else "", kw.get("env")))
            return real_run(args, *a, **kw)

        monkeypatch.setattr(subprocess, "run", run)

    @property
    def envs(self) -> List[Optional[Dict[str, str]]]:
        return [env for _command, env in self.spawns]

    @property
    def commands(self) -> List[str]:
        return [command for command, _env in self.spawns]


def test_every_family_spawns_git_with_the_canonical_environment(tmp_path, host, monkeypatch):
    """Family A (worktree), Family B (checkpoint scratch index, which must keep ITS GIT_INDEX_FILE), Family D (milestone)."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    host.monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    host.monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    recorder = _SpawnRecorder(monkeypatch)
    wt.git_read_lines(str(repo), "rev-parse", "HEAD")
    assert compute_workspace_content_hash(str(repo)) is not None
    assert workspace_evidence_hash(str(repo)) is not None
    assert recorder.envs, "no git spawn was observed"
    for env in recorder.envs:
        assert env is not None and env.get("GIT_ATTR_NOSYSTEM") == "1", env
        assert "GIT_ATTR_SOURCE" not in env and "GIT_LITERAL_PATHSPECS" not in env
    scratch = [env for env in recorder.envs if "GIT_INDEX_FILE" in env]
    assert len(scratch) >= 6, "checkpoint (read-tree, add, rm, write-tree) and milestone (read-tree, add, add -u, write-tree) use a scratch index"
    assert all(os.path.basename(env["GIT_INDEX_FILE"]).startswith((".kriya-scratch-index-", ".kriya-evidence-index-")) for env in scratch)


def test_kriyas_scratch_index_overrides_a_host_index_and_the_policy_never_overwrites_it(tmp_path, host, monkeypatch):
    """Order: base environment -> canonical policy -> Kriya's own GIT_INDEX_FILE. A host that exports GIT_INDEX_FILE (a git hook
    that launched Kriya) must not redirect the checkpoint / milestone index operations onto the host's index."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    host_index = tmp_path / "host.index"
    host.monkeypatch.setenv("GIT_INDEX_FILE", str(host_index))
    recorder = _SpawnRecorder(monkeypatch)
    assert compute_workspace_content_hash(str(repo)) is not None
    assert workspace_evidence_hash(str(repo)) is not None
    index_commands = [(command, env) for command, env in recorder.spawns if command in {"read-tree", "add", "rm", "write-tree"}]
    assert {command for command, _env in index_commands} == {"read-tree", "add", "rm", "write-tree"}
    for command, env in index_commands:
        index = env["GIT_INDEX_FILE"]
        assert index != str(host_index), f"{command} ran on the HOST's index"
        assert os.path.basename(index).startswith((".kriya-scratch-index-", ".kriya-evidence-index-")), (command, index)
        assert env["GIT_ATTR_NOSYSTEM"] == "1"
    assert not host_index.exists(), "the host's index file was written"


def test_the_inspection_runs_under_the_environment_the_operation_runs_under(tmp_path, host, monkeypatch):
    """The S19 inspector must read the very configuration the caller's git will: both the default (no env given) and a caller-given
    environment carry the canonical policy, so inspection and execution cannot disagree."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    host.monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    host.monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    hostile_given = {**os.environ, "GIT_ATTR_NOSYSTEM": "0"}
    recorder = _SpawnRecorder(monkeypatch)
    refuse_repository_content_filters(str(repo))                      # default: the inspector builds the environment itself
    default_spawns = len(recorder.envs)
    refuse_repository_content_filters(str(repo), env=hostile_given)   # a caller-given environment, hostile on purpose
    assert default_spawns > 0 and len(recorder.envs) > default_spawns, "the inspection spawned no git"
    for env in recorder.envs:
        assert env is not None and env["GIT_ATTR_NOSYSTEM"] == "1", env
        assert "GIT_ATTR_SOURCE" not in env and "GIT_LITERAL_PATHSPECS" not in env


def test_gittool_receives_the_canonical_environment_through_its_containment_profile(tmp_path, host, monkeypatch):
    """Family C: ContainmentProfile.resolved_env, honoured by the Null backend; the profile and backend are unchanged."""
    from kriya.tools.containment import NullContainmentBackend
    from plugins.core_tools import GitArgs, GitTool

    repo = make_repo(tmp_path, {"f.txt": LF})
    host.monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    host.monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    seen = []
    real = NullContainmentBackend.prepare

    def spy(self, profile, command):
        prepared = real(self, profile, command)
        seen.append((profile, prepared))
        return prepared

    monkeypatch.setattr(NullContainmentBackend, "prepare", spy)
    tool = GitTool()
    assert tool._tracked_paths(str(repo)) == ["f.txt"]                                       # profile site 1
    tool._require_own_repository_view(str(repo), "status", base_revision=False)             # profile site 2
    asyncio.run(tool._run_in(GitArgs(subcommand="status"), str(repo)))                      # profile site 3 (async)
    assert len(seen) >= 3, f"expected all three GitTool profile sites to be exercised, saw {len(seen)}"
    for profile, prepared in seen:
        assert profile.trust_class.name == "TRUSTED_KRIYA_INFRASTRUCTURE" and not profile.env_allowlist
        assert prepared.env is not None and prepared.env["GIT_ATTR_NOSYSTEM"] == "1"
        assert "GIT_ATTR_SOURCE" not in prepared.env and "GIT_LITERAL_PATHSPECS" not in prepared.env
        inherited = child_environment()
        assert all(prepared.env.get(name) == value for name, value in inherited.items()
                   if name not in GIT_STRIPPED_ENVIRONMENT and name not in GIT_FORCED_ENVIRONMENT), "containment broadened/narrowed"


# ---- attr.tree: typed refusal in every configuration scope --------------------------------------------------------------------

def _set_local(repo: Path, value: str) -> None:
    _g(repo, "config", "attr.tree", value)


def _set_worktree_scope(repo: Path, value: str) -> None:
    _g(repo, "config", "extensions.worktreeConfig", "true")
    _g(repo, "config", "--worktree", "attr.tree", value)


def _expect_refusal(repo: Path, scope: str) -> None:
    with pytest.raises(RepositoryContentFilterRefused) as refused:
        refuse_repository_content_filters(str(repo))
    assert refused.value.reason_code == GIT_ATTR_TREE_CONFIGURED
    assert refused.value.scope == scope
    assert "attr.tree" in str(refused.value) and "unset-all" in str(refused.value)


def test_attr_tree_in_global_config_is_refused(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF})
    refuse_repository_content_filters(str(repo))                                           # control: nothing configured
    host.configure(autocrlf="false", attr_tree="HEAD")
    _expect_refusal(repo, "global")


def test_attr_tree_in_system_config_is_refused(tmp_path, host, monkeypatch):
    """The system scope without touching the real system config: git reads GIT_CONFIG_SYSTEM as the system file."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    system = tmp_path / "system-gitconfig"
    system.write_text("[attr]\n\ttree = HEAD\n")
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(system))
    _expect_refusal(repo, "system")


def test_attr_tree_in_local_config_is_refused(tmp_path, host):
    """Local config is NOT versioned repository content: it is refused too (owner decision, no scope exceptions)."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    _set_local(repo, "HEAD")
    _expect_refusal(repo, "local")


def test_attr_tree_in_worktree_scope_is_refused(tmp_path, host):
    repo = make_repo(tmp_path, {"f.txt": LF})
    _set_worktree_scope(repo, "HEAD")
    _expect_refusal(repo, "worktree")


@pytest.mark.parametrize("value", ["HEAD", ""], ids=["non-empty", "empty"])
def test_attr_tree_injected_through_command_scope_environment_is_refused(tmp_path, host, monkeypatch, value):
    """GIT_CONFIG_* stays a supported mechanism; only the unsafe key is refused - and an empty value is refused too."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "attr.tree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", value)
    _expect_refusal(repo, "command")


def test_ordinary_config_and_other_attr_keys_are_unaffected(tmp_path, host, monkeypatch):
    repo = make_repo(tmp_path, {"f.txt": LF})
    _g(repo, "config", "core.whitespace", "trailing-space")
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")                       # command-scope config that is NOT attr.tree is still supported
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "safe.directory")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", str(repo))
    refuse_repository_content_filters(str(repo))


EVIDENCE_OPERATIONS = {
    "worktree-materialization": lambda repo: wt.create_git_worktree(str(repo)),
    "checkpoint-index-identity": lambda repo: compute_workspace_content_hash(str(repo)),
    "milestone-evidence-identity": lambda repo: workspace_evidence_hash(str(repo)),
}


MUTATING_SUBCOMMANDS = {"worktree", "read-tree", "add", "rm", "write-tree", "reset", "checkout", "commit", "init", "clean"}


@pytest.mark.parametrize("operation", sorted(EVIDENCE_OPERATIONS))
def test_attr_tree_refusal_precedes_every_evidence_bearing_git_operation_and_changes_nothing(tmp_path, host, monkeypatch, operation):
    """The refusal is not a materialization-only guard: the checkpoint and milestone identities refuse too, before any git that
    materializes, stages or writes a tree runs (only the inspector's read-only configuration query has run)."""
    repo = make_repo(tmp_path, {"f.txt": LF})
    _set_local(repo, "HEAD")
    status_before = _g(repo, "status", "--porcelain=v1", "--untracked-files=all")
    worktrees_before = _g(repo, "worktree", "list", "--porcelain")
    recorder = _SpawnRecorder(monkeypatch)
    with pytest.raises(RepositoryContentFilterRefused) as refused:
        EVIDENCE_OPERATIONS[operation](repo)
    assert refused.value.reason_code == GIT_ATTR_TREE_CONFIGURED
    assert recorder.commands, "the inspector never ran git (the refusal came from somewhere else)"
    assert not set(recorder.commands) & MUTATING_SUBCOMMANDS, recorder.commands
    assert _g(repo, "worktree", "list", "--porcelain") == worktrees_before
    assert _g(repo, "status", "--porcelain=v1", "--untracked-files=all") == status_before
    assert (repo / "f.txt").read_bytes() == LF


def test_the_model_facing_diff_exposes_a_crlf_rewrite_the_hosts_own_view_hides(tmp_path, host):
    """ACCEPTED CONSEQUENCE (owner, 2026-10-30): Kriya's deterministic git view may show a byte difference the user's host
    configuration hides. MEASURED: blob LF, working file CRLF, host autocrlf=true -> the host's `git diff` is empty (0 lines); the
    pinned one is a real diff. Deterministic evidence is preferred to cosmetic parity with the host."""
    from plugins.core_tools import GitArgs, GitTool

    repo = make_repo(tmp_path, {"f.txt": LF})
    (repo / "f.txt").write_bytes(CRLF)
    host.configure(autocrlf="true")
    hosts_own_view = subprocess.run(["git", "diff"], cwd=repo, capture_output=True, text=True, check=True).stdout
    assert hosts_own_view == "", "validity: the host's own git must hide the rewrite for this test to mean anything"
    kriyas_view = asyncio.run(GitTool()._run_in(GitArgs(subcommand="diff"), str(repo)))
    assert "alpha" in str(kriyas_view) and ("-alpha" in str(kriyas_view) or "+alpha" in str(kriyas_view)), kriyas_view


def test_release_identity_is_host_independent_and_still_sees_a_real_change(tmp_path, host):
    """KCEP-6 item 4: certification binds ``release_identity`` (revision + dirty flag). MEASURED discriminator: a CLEAN checkout of an
    intentionally-CRLF file read as dirty to plain git under a host attributes file ``* text eol=crlf`` (a false dirty written into
    the identity by host configuration alone); a genuine rewrite is dirty under every configuration."""
    from kriya.core.release_identity import kriya_revision, release_identity

    repo = make_repo(tmp_path, {"win.bat": CRLF})
    assert (repo / "win.bat").read_bytes() == CRLF
    identities = {}
    for name, configure in {"no-host-config": dict(), "host-attributes-eol-crlf": dict(attributes="* text eol=crlf\n"),
                            "autocrlf-true": dict(autocrlf="true"), "core-eol-crlf": dict(autocrlf="false", eol="crlf")}.items():
        host.configure(**configure)
        revision = kriya_revision(str(repo))
        assert revision["dirty"] is False, f"{name}: a clean checkout read as dirty"
        identities[name] = release_identity("none", source_root=str(repo))["digest"]
    assert len(set(identities.values())) == 1, identities
    (repo / "win.bat").write_bytes(LF)                                  # a genuine byte change
    for configure in (dict(), dict(attributes="* text eol=crlf\n"), dict(autocrlf="true")):
        host.configure(**configure)
        assert kriya_revision(str(repo))["dirty"] is True


# ---- release identity: the attr.tree rule governs kriya_revision (RELEASE-IDENTITY-ATTR-TREE-UNINSPECTED-001) -----------------
# S19 decision B (Kriya's own checkout is operator-managed infrastructure) exempts ONLY the content-filter judgment of that call.
# The W3-2 attr.tree rule is not role-based: no Git path whose output becomes certification evidence is exempt from it.

class _IdentityEvents:
    """One ordered log of every real git spawn and of every evidence-bearing ``release_identity._command`` call, each with the
    environment it runs under, so a test can assert WHAT ran, in WHAT order and under WHICH environment."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from kriya.core import release_identity as ri

        self.events: List[tuple] = []
        real_run, real_command, log = subprocess.run, ri._command, self.events

        def run(args, *a, **kw):
            if args and os.path.basename(str(args[0])) == "git":
                log.append(("spawn", tuple(str(word) for word in args[1:]), kw.get("env")))
            return real_run(args, *a, **kw)

        def command(argv, cwd=None, env=None):
            log.append(("evidence", tuple(argv[1:]), env))
            return real_command(argv, cwd=cwd, env=env)

        monkeypatch.setattr(subprocess, "run", run)
        monkeypatch.setattr(ri, "_command", command)

    @property
    def evidence(self) -> List[tuple]:
        return [event for event in self.events if event[0] == "evidence"]

    @property
    def spawn_envs(self) -> List[Dict[str, str]]:
        return [event[2] for event in self.events if event[0] == "spawn"]


def _verb_of(args: tuple) -> str:
    """The git subcommand of a spawn's argv (after the pinned ``-c key=value`` options)."""
    words = [word for word in args if not word.startswith("-") and "=" not in word]
    return words[0] if words else ""


def _attr_tree_scopes(repo: Path) -> List[str]:
    """Validity control: the scopes at which THIS git, under the environment Kriya's git runs with, really sees attr.tree."""
    done = subprocess.run(["git", "config", "--show-scope", "--get-all", "attr.tree"], cwd=repo, capture_output=True, text=True,
                          env=git_environment(child_environment()))
    return [line.split("\t")[0] for line in done.stdout.splitlines()]


def _set_command_scope(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "attr.tree")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", value)


def _arm_global(repo: Path, host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    host.configure(autocrlf="false", attr_tree="HEAD")


def _arm_local(repo: Path, host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_local(repo, "HEAD")


def _arm_command(repo: Path, host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_command_scope(monkeypatch, "HEAD")


def _arm_command_empty(repo: Path, host: Host, monkeypatch: pytest.MonkeyPatch) -> None:
    _set_command_scope(monkeypatch, "")


ATTR_TREE_SCENARIOS = {"global": (_arm_global, "global"), "local": (_arm_local, "local"), "command-scope": (_arm_command, "command"),
                       "command-scope-empty": (_arm_command_empty, "command")}


@pytest.mark.parametrize("scenario", sorted(ATTR_TREE_SCENARIOS))
def test_release_identity_refuses_an_effective_attr_tree_before_any_evidence_git_call(tmp_path, host, monkeypatch, scenario):
    """Proofs 1-3 and 8: an effective attr.tree in the global file, the repository's own config, or the command-scope environment makes
    ``kriya_revision`` and ``release_identity`` refuse typed; neither evidence git call (rev-parse HEAD, status) runs and no identity
    (no digest) is built. A control first proves the SAME checkout yields an identity before attr.tree is configured."""
    from kriya.core import release_identity as ri

    repo = make_repo(tmp_path, {"f.txt": LF})
    arm, scope = ATTR_TREE_SCENARIOS[scenario]
    baseline = ri.kriya_revision(str(repo))                                  # control: an identity is produced without attr.tree
    assert baseline["revision"] == _g(repo, "rev-parse", "HEAD").strip() and baseline["dirty"] is False
    arm(repo, host, monkeypatch)
    assert _attr_tree_scopes(repo) == [scope], "validity: git must see attr.tree at exactly this scope under Kriya's own environment"
    digests = []
    monkeypatch.setattr(ri, "_digest", lambda material: digests.append(material) or "d")
    events = _IdentityEvents(monkeypatch)
    for entry in (lambda: ri.kriya_revision(str(repo)), lambda: ri.release_identity("none", source_root=str(repo))):
        with pytest.raises(RepositoryContentFilterRefused) as refused:
            entry()
        assert refused.value.reason_code == GIT_ATTR_TREE_CONFIGURED and refused.value.scope == scope
    assert events.evidence == [], events.evidence                           # no evidence call ran
    verbs = [_verb_of(args) for kind, args, _env in events.events if kind == "spawn"]
    assert "config" in verbs, f"the inspection never ran its configuration query: {verbs}"
    assert not set(verbs) & {"status", "add", "commit", "checkout", "reset"}, verbs
    assert digests == [], "an identity digest was built after the refusal"


def test_release_identity_inspects_before_its_first_git_call_and_under_the_operations_environment(tmp_path, host, monkeypatch):
    """Proof 4 (a normal identity is unchanged) and proof 7: the inspection happens BEFORE the first evidence call, and every git
    spawn - the inspection's and the two evidence calls' - runs under the one canonical environment, hostile inherited variables
    included."""
    from kriya.core import release_identity as ri

    repo = make_repo(tmp_path, {"f.txt": LF})
    monkeypatch.setenv("GIT_ATTR_SOURCE", "HEAD")
    monkeypatch.setenv("GIT_LITERAL_PATHSPECS", "1")
    head = _g(repo, "rev-parse", "HEAD").strip()                       # fixture git BEFORE the recorder, so it is not one of the spawns
    events = _IdentityEvents(monkeypatch)
    identity = ri.kriya_revision(str(repo))
    assert identity == {"revision": head, "dirty": False}
    first_evidence = [kind for kind, _args, _env in events.events].index("evidence")
    assert any(kind == "spawn" and "config" in args for kind, args, _env in events.events[:first_evidence]), \
        "the inspection's configuration query did not run before the first evidence call"
    assert len(events.evidence) == 2
    expected = git_environment(child_environment())
    assert events.spawn_envs and all(env == expected for env in events.spawn_envs), "a git spawn ran under another environment"
    assert "GIT_ATTR_SOURCE" not in expected and "GIT_LITERAL_PATHSPECS" not in expected


def test_the_inspection_reads_the_environment_the_evidence_calls_run_under(tmp_path, host, monkeypatch):
    """Proof 7, discriminating form: attr.tree supplied ONLY through the command-scope environment is seen by the inspection, which
    can only be so if the inspection reads the environment the operation runs under (the host files and the repository hold nothing)."""
    from kriya.core import release_identity as ri

    repo = make_repo(tmp_path, {"f.txt": LF})
    _set_command_scope(monkeypatch, "HEAD")
    assert _attr_tree_scopes(repo) == ["command"]
    assert "attr.tree" not in (repo / ".git" / "config").read_text() and "tree" not in host.gitconfig.read_text()
    with pytest.raises(RepositoryContentFilterRefused) as refused:
        ri.kriya_revision(str(repo))
    assert refused.value.scope == "command"


def _plant_repository_content_filter(repo: Path) -> None:
    """A repository-defined content filter that is ACTIVE for a tracked path: the standard policy refuses it."""
    (repo / ".gitattributes").write_text("* filter=noop\n")
    _g(repo, "add", ".gitattributes")
    _g(repo, "commit", "-q", "-m", "attrs")
    _g(repo, "config", "filter.noop.clean", "cat")


def test_the_installation_state_exemption_still_covers_a_content_filter_but_never_attr_tree(tmp_path, host):
    """Proofs 5 and 6: S19 decision B exempts the content-filter judgment of Kriya's own checkout (kriya_revision answers although the
    standard inspection refuses the same folder), and nothing else: with a content filter AND attr.tree together the attr.tree
    refusal is the one raised."""
    from kriya.core import release_identity as ri

    repo = make_repo(tmp_path, {"f.txt": LF})
    _plant_repository_content_filter(repo)
    with pytest.raises(RepositoryContentFilterRefused) as control:                 # validity: the standard policy refuses this folder
        refuse_repository_content_filters(str(repo))
    assert control.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED
    identity = ri.kriya_revision(str(repo))                                        # the role-exempt call still answers
    assert identity["revision"] == _g(repo, "rev-parse", "HEAD").strip() and identity["dirty"] is False
    _set_local(repo, "HEAD")
    for entry in (lambda: ri.kriya_revision(str(repo)), lambda: refuse_repository_content_filters(str(repo))):
        with pytest.raises(RepositoryContentFilterRefused) as both:
            entry()
        assert both.value.reason_code == GIT_ATTR_TREE_CONFIGURED, both.value.reason_code   # attr.tree outranks the content filter in both


def test_a_checkout_without_git_is_not_inspected_and_its_embedded_identity_is_unchanged(tmp_path, host, monkeypatch):
    """No ``.git`` -> no git call and no inspection (the embedded build identity stands in): the follow-up changes nothing there, even
    with an attr.tree configured."""
    from kriya.core import release_identity as ri

    host.configure(autocrlf="false", attr_tree="HEAD")
    folder = tmp_path / "wheel"
    folder.mkdir()
    events = _IdentityEvents(monkeypatch)
    identity = ri.kriya_revision(str(folder))
    assert set(identity) == {"revision", "dirty"} and events.events == []


def _checkout_with_submodule(root: Path) -> tuple:
    """(parent, submodule work tree): a populated submodule, the repository the inspection recurses into."""
    sub = make_repo(root, {"s.txt": LF}, gitattributes="* filter=noop\n", name="sub-origin")
    parent = make_repo(root, {"f.txt": LF}, name="parent")
    _g(parent, "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(sub), "mod")
    _g(parent, "commit", "-q", "-m", "add submodule")
    assert (parent / "mod" / "s.txt").read_bytes() == LF, "the submodule was not populated"
    return parent, parent / "mod"


def test_the_exemption_reaches_a_submodule_but_so_does_the_attr_tree_rule(tmp_path, host):
    """The recursion carries the role: a submodule's ACTIVE content filter does not refuse the exempt call (the standard policy refuses
    it - validity control), while a submodule's own attr.tree does, before either evidence call."""
    from kriya.core import release_identity as ri

    parent, module = _checkout_with_submodule(tmp_path)
    _g(module, "config", "filter.noop.clean", "cat")
    with pytest.raises(RepositoryContentFilterRefused) as control:
        refuse_repository_content_filters(str(parent))
    assert control.value.reason_code == GIT_CONTENT_FILTER_REPOSITORY_DEFINED, "validity: the submodule's filter must be refusable"
    assert ri.kriya_revision(str(parent))["revision"] == _g(parent, "rev-parse", "HEAD").strip()
    _g(module, "config", "attr.tree", "HEAD")
    with pytest.raises(RepositoryContentFilterRefused) as refused:
        ri.kriya_revision(str(parent))
    assert refused.value.reason_code == GIT_ATTR_TREE_CONFIGURED

