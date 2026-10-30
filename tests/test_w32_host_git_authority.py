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
    """ACCEPTED CONSEQUENCE (owner, 2026-10-31): Kriya's deterministic git view may show a byte difference the user's host
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
