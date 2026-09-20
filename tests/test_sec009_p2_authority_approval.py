"""SEC-009 P2: explicit, durable, digest-bound approval for security-
authority configuration, including deterministic non-interactive CI
authorization.

Core invariant under test throughout: approval grants authority to an EXACT
security-relevant effective configuration - never blanket trust to a
repository, a config file, a directory, or any future field/value. P1's
fail-closed behavior (tests/test_sec009_config_authority.py) remains the
floor whenever no valid approval exists; this file proves that floor is
preserved AND that a real, narrow, revocable grant works.

All tests set KRIYA_AUTHORITY_HOME to an isolated tmp directory (autouse
fixture below) so nothing here ever reads or writes the real
~/.kriya/authority/ on the machine running the suite.
"""
import contextlib
import json
import os
import subprocess
import sys

import pytest
import yaml

from kriya.config.config import load_config, resolve_config_state
from kriya.config.authority import ConfigAuthorityError
from kriya.config import authority_approval as aa

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRIYA_BIN = os.path.join(REPO_ROOT, ".venv", "bin", "kriya")


@pytest.fixture(autouse=True)
def isolated_authority_home(tmp_path, monkeypatch):
    home = tmp_path / "_authority_home"
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(home))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    yield


@contextlib.contextmanager
def _cwd(path):
    old = os.getcwd()
    os.makedirs(path, exist_ok=True)
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


def _write_yaml(path, data) -> str:
    with open(path, "w") as f:
        yaml.dump(data, f)
    return str(path)


def _approve(workspace_root=None):
    """Test helper mirroring `kriya authority approve --confirm` exactly -
    resolve fresh, build, save. Never a shortcut that bypasses what the
    real CLI command does."""
    state = resolve_config_state()
    artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
    path = aa.default_local_approval_path(state.workspace_root)
    aa.save_approval_artifact(path, artifact)
    return path, artifact


# --- 1/2/3/4: basic MCP approve/deny/invalidate cycle -----------------------

def test_1_repo_mcp_denied_without_approval(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()


def test_2_exact_mcp_approval_succeeds(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        _approve()
        cfg = load_config()
        assert cfg.mcp["hostile"].command == "/bin/sh"


def test_3_mcp_command_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        _approve()
        load_config()  # sanity - works before change
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/bash"}}})
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()


def test_4_mcp_args_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh", "args": ["-c", "id"]}}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh", "args": ["-c", "whoami"]}}})
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()


# --- 5: secret rotation invalidates without storing/printing the secret ----

def test_5_mcp_env_secret_change_invalidates_without_leaking(tmp_path):
    ws = tmp_path / "ws"
    secret = "sk-original-secret-abc123"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh", "env": {"TOKEN": secret}}}})
        path, artifact = _approve()
        assert secret not in open(path).read()
        cfg = load_config()
        assert cfg.mcp["hostile"].env["TOKEN"] == secret  # AppConfig itself legitimately carries the real value

        rotated = "sk-rotated-secret-xyz789"
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh", "env": {"TOKEN": rotated}}}})
        with pytest.raises(ConfigAuthorityError) as exc_info:
            load_config()
        message = str(exc_info.value)
        assert secret not in message and rotated not in message


# --- 6/7/8: narrow, non-widening authority ----------------------------------

def test_6_additional_mcp_server_not_covered(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"serverA": {"command": "/bin/sh"}}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"serverA": {"command": "/bin/sh"}, "serverB": {"command": "/bin/bash"}}
        })
        with pytest.raises(ConfigAuthorityError) as exc_info:
            load_config()
        field_paths = {v.field_path for v in exc_info.value.violations}
        assert {"mcp.serverA", "mcp.serverB"} <= field_paths  # whole set re-denied, not just the new one


def test_7_plugin_approval_separate_from_mcp_approval(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        _approve()
        load_config()  # mcp-only approval works
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "plugins": {"directory": "./evil"},
        })
        with pytest.raises(ConfigAuthorityError) as exc_info:
            load_config()
        field_paths = {v.field_path for v in exc_info.value.violations}
        assert "plugins.directory" in field_paths


def test_8_plugin_path_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"plugins": {"directory": "./plugins_a", "enabled": ["x"]}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"plugins": {"directory": "./plugins_b", "enabled": ["x"]}})
        with pytest.raises(ConfigAuthorityError, match="plugins.directory"):
            load_config()


# --- 9/10/11/12: security-boundary drift invalidates ------------------------

def test_9_runtime_profile_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"runtime_profile": "legacy"})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"runtime_profile": "hardened"})
        with pytest.raises(ConfigAuthorityError, match="runtime_profile"):
            load_config()


def test_10_registry_host_expansion_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"acquisition_registry_hosts": ["pypi.org"]}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"acquisition_registry_hosts": ["pypi.org", "evil.example.com"]}})
        with pytest.raises(ConfigAuthorityError, match="acquisition_registry_hosts"):
            load_config()


def test_11_egress_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"egress_policy": "local_only"}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"egress_policy": "unrestricted"}})
        with pytest.raises(ConfigAuthorityError, match="egress_policy"):
            load_config()


def test_12_policy_weakening_invalidates(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"execution_policy": {"mode": "audit"}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {"execution_policy": {"mode": "enforce"}})
        with pytest.raises(ConfigAuthorityError, match="execution_policy.mode"):
            load_config()


# --- 13/14: identity binding ------------------------------------------------

def test_13_workspace_copy_approval_reuse_fails(tmp_path):
    import shutil
    wsA = tmp_path / "wsA"
    wsB = tmp_path / "wsB"
    wsA.mkdir()
    _write_yaml(wsA / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
    with _cwd(wsA):
        _approve()
        load_config()
    shutil.copytree(wsA, wsB)
    with _cwd(wsB):
        with pytest.raises(ConfigAuthorityError):
            load_config()


def test_14_symlink_identity_substitution_fails(tmp_path):
    real_unapproved = tmp_path / "real_unapproved"
    real_unapproved.mkdir()
    _write_yaml(real_unapproved / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
    alias = tmp_path / "alias"
    os.symlink(real_unapproved, alias)
    with _cwd(alias):
        with pytest.raises(ConfigAuthorityError):
            load_config()  # resolves to real_unapproved's identity, which has no approval


# --- 15/16/17: trust-source rules -------------------------------------------

def test_15_repo_shipped_approval_artifact_is_not_self_authorizing(tmp_path):
    """The core anti-recursion property: an attacker who controls the whole
    repository can craft a perfectly self-consistent approval.json and
    check it into the repo itself (e.g. .kriya/authority/approval.json) -
    it must never be read from inside the workspace at all."""
    ws = tmp_path / "malicious_repo"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        state = resolve_config_state()
        fake = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        in_repo_path = ws / ".kriya" / "authority" / "approval.json"
        aa.save_approval_artifact(str(in_repo_path), fake)
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()  # no trust_file passed - only the external home-dir store is ever consulted


def test_16_ci_with_valid_external_trust_artifact_succeeds_non_interactively(tmp_path):
    ws = tmp_path / "ws"
    external = tmp_path / "external_trust_dir"
    external.mkdir()
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        state = resolve_config_state()
        artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        trust_path = str(external / "trust.json")
        aa.save_approval_artifact(trust_path, artifact)
        cfg = load_config(trust_file=trust_path)
        assert cfg.mcp["hostile"].command == "/bin/sh"


def test_17_ci_missing_trust_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        with pytest.raises(ConfigAuthorityError):
            load_config(trust_file=str(tmp_path / "does_not_exist.json"))


def test_17b_ci_mismatched_trust_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    external = tmp_path / "external"
    external.mkdir()
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        state = resolve_config_state()
        artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        trust_path = str(external / "trust.json")
        aa.save_approval_artifact(trust_path, artifact)
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/DIFFERENT"}}})
        with pytest.raises(ConfigAuthorityError):
            load_config(trust_file=trust_path)


def test_trust_file_inside_workspace_is_rejected(tmp_path):
    """CI's own .github/workflows/ci.yml (attacker-modifiable in a PR)
    naming an in-repo trust-file path is the same recursion as #15, spelled
    differently - must be refused outright, not silently trusted."""
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        state = resolve_config_state()
        artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        in_ws_trust = str(ws / ".kriya" / "ci-trust.json")
        aa.save_approval_artifact(in_ws_trust, artifact)
        with pytest.raises(aa.TrustPathInsideWorkspaceError):
            load_config(trust_file=in_ws_trust)


def test_trust_file_env_var_also_rejected_inside_workspace(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        state = resolve_config_state()
        artifact = aa.build_approval_artifact(state.violations, state.config_dict, state.workspace_root)
        in_ws_trust = str(ws / ".kriya" / "ci-trust.json")
        aa.save_approval_artifact(in_ws_trust, artifact)
        monkeypatch.setenv("KRIYA_TRUST_FILE", in_ws_trust)
        with pytest.raises(aa.TrustPathInsideWorkspaceError):
            load_config()


# --- 18/19: -y and --config alone never approve -----------------------------

def test_18_y_flag_has_no_approval_semantics():
    import inspect as _inspect
    sig = _inspect.signature(load_config)
    assert "yes" not in sig.parameters and "y" not in sig.parameters
    assert "trust_file" in sig.parameters and "yes" not in str(sig)


def test_19_ordinary_config_flag_never_creates_an_approval_artifact(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        with pytest.raises(ConfigAuthorityError):
            load_config("./kriya.yaml")
        assert not os.path.exists(aa.default_local_approval_path(os.getcwd()))


# --- 20/21: tamper/schema fail closed ---------------------------------------

def test_20_approval_artifact_tampering_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        path, _artifact = _approve()
        load_config()
        raw = json.load(open(path))
        raw["security_subset"]["set_digest"] = "0" * 64
        json.dump(raw, open(path, "w"))
        with pytest.raises(ConfigAuthorityError):
            load_config()


def test_21_approval_schema_version_tampering_fails_closed(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        path, _artifact = _approve()
        raw = json.load(open(path))
        raw["schema_version"] = 999
        json.dump(raw, open(path, "w"))
        with pytest.raises(ConfigAuthorityError):
            load_config()


# --- 22: revoke ---------------------------------------------------------

def test_22_revoke_immediately_restores_denial(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        _approve()
        load_config()  # works
        removed = aa.revoke_local_approval(os.getcwd())
        assert removed
        with pytest.raises(ConfigAuthorityError):
            load_config()
        assert aa.revoke_local_approval(os.getcwd()) is False  # idempotent


# --- 23: benign changes don't require reapproval ----------------------------

def test_23_benign_changes_continue_without_reapproval(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "llm": {"model": "model-a"},
            "autonomy": {"generation_time_budget_seconds": 100},
        })
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "llm": {"model": "model-b"},  # benign change
            "autonomy": {"generation_time_budget_seconds": 999},  # benign change
        })
        cfg = load_config()  # must NOT raise
        assert cfg.llm.model == "model-b"
        assert cfg.autonomy.generation_time_budget_seconds == 999


def test_23b_in_workspace_safe_path_change_does_not_require_reapproval(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "paths": {"skills": "./skills_a"},
        })
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "paths": {"skills": "./skills_b"},
        })
        cfg = load_config()
        assert cfg.paths.skills.endswith("skills_b")


# --- 24: unknown future field ------------------------------------------

def test_24_unknown_future_security_field_not_covered_by_older_approval(tmp_path):
    ws = tmp_path / "ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
        _approve()
        load_config()
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "totally_new_field_from_the_future": {"grant_me": "authority"},
        })
        with pytest.raises(ConfigAuthorityError, match="unknown/future field"):
            load_config()


# --- 25/26/27: logging.file approval cycle (SEC-009 bypass-closure fix, ----
# 2026-09-12) - mirrors the MCP approve/deny/invalidate cycle above (tests
# 1-4), plus the no-grandfathering case unique to this fix: an approval
# whose security subset never included logging.file (because it was
# in-workspace, i.e. not a violation at all, at approval time) must not
# silently authorize it once it becomes an escaping, SECURITY_AUTHORITY
# target - Option A's whole-set digest (compute_set_digest()) makes this
# automatic, this test proves it rather than just inferring it.

def test_25_exact_logging_file_approval_succeeds(tmp_path):
    ws = tmp_path / "ws"
    outside_dir = tmp_path / "log25_outside"
    target = outside_dir / "kriya.log"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"file": str(target)}})
        with pytest.raises(ConfigAuthorityError, match="logging.file"):
            load_config()
        _approve()
        cfg = load_config()
        assert os.path.realpath(cfg.logging.file) == os.path.realpath(str(target))


def test_26_logging_file_target_change_invalidates(tmp_path):
    ws = tmp_path / "ws"
    outside_dir = tmp_path / "log26_outside"
    target = outside_dir / "kriya.log"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"file": str(target)}})
        _approve()
        load_config()  # sanity - works before change
        target2 = outside_dir / "renamed.log"
        _write_yaml(ws / "kriya.yaml", {"logging": {"file": str(target2)}})
        with pytest.raises(ConfigAuthorityError, match="logging.file"):
            load_config()


def test_27_older_approval_without_logging_file_in_security_subset_does_not_grandfather_it(tmp_path):
    """The requirement-11 case: approve a config where logging.file resolves
    INSIDE the workspace (so it is REPOSITORY_SAFE, not part of the
    approved security subset at all - only `mcp.hostile` is), then flip
    logging.file to an escaping target without re-approving. The old
    approval's set_digest was computed over {mcp.hostile} only, so it
    cannot possibly match the new set {mcp.hostile, logging.file} -
    Option A denies the whole thing, not just the new field."""
    ws = tmp_path / "ws"
    outside_dir = tmp_path / "log27_outside"
    target = outside_dir / "kriya.log"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "logging": {"file": "logs/kriya.log"},  # in-workspace: not a violation
            "mcp": {"hostile": {"command": "/bin/sh"}},
        })
        _approve()
        cfg = load_config()
        assert cfg.mcp["hostile"].command == "/bin/sh"

        _write_yaml(ws / "kriya.yaml", {
            "logging": {"file": str(target)},  # now escapes - newly SECURITY_AUTHORITY
            "mcp": {"hostile": {"command": "/bin/sh"}},
        })
        with pytest.raises(ConfigAuthorityError, match="logging.file"):
            load_config()
    assert not outside_dir.exists()


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_cli_logging_file_approval_reaches_configure_logging_end_to_end(tmp_path, monkeypatch):
    """Real production CLI, real approval artifact, real outside-workspace
    target: proves the SIDE_EFFECT invariant in both directions through the
    actual entry point - denied means no directory/file, approved means the
    real log target is created. `kriya plugins` never reaches an LLM call."""
    home = tmp_path / "_home"
    ws = tmp_path / "repo"
    ws.mkdir()
    outside_dir = tmp_path / "cli_log_outside"
    target = outside_dir / "attacker.log"
    _write_yaml(ws / "kriya.yaml", {"logging": {"file": str(target)}})
    env = {"KRIYA_AUTHORITY_HOME": str(home)}

    code, out, err = _run_cli(["plugins"], cwd=str(ws), extra_env=env)
    assert code != 0
    assert "Configuration-authority denied" in err
    assert not outside_dir.exists()
    assert not target.exists()

    approve_code, approve_out, approve_err = _run_cli(
        ["authority", "approve", "--confirm"], cwd=str(ws), extra_env=env,
    )
    assert approve_code == 0, approve_err

    code2, out2, err2 = _run_cli(["plugins"], cwd=str(ws), extra_env=env)
    assert code2 == 0, err2
    assert target.exists(), "approved logging.file target must actually be created by configure_logging()"


# --- Resume: same authority resolver, no stale-approval inheritance --------

@pytest.mark.asyncio
async def test_resume_path_reresolves_authority_not_stale_checkpoint(tmp_path, monkeypatch):
    """Every AppConfig in this codebase comes from load_config() (grep-
    verified: no bare AppConfig(...) construction anywhere in cli.py/
    workflow.py/repl.py) - `generate --resume` gets its config from
    main()'s ctx.obj['config'] = load_config(...), called BEFORE the
    generate subcommand's own resume logic ever runs. A security-field
    change between the original approved run and a later `--resume`
    invocation is therefore caught at config-load time, before resume-
    specific code (or any workflow/checkpoint logic) is ever reached -
    proven here via the real CLI dispatch, with run_generation_workflow
    patched to raise if it's ever called, so this is not just an inference
    from reading the code."""
    from click.testing import CliRunner
    from kriya.cli import main

    ws = tmp_path / "ws"
    ws.mkdir()
    _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
    with _cwd(ws):
        _approve()
        # change the security field AFTER approval, simulating drift before a --resume call
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/bash"}}})

    def _must_not_be_called(*a, **k):
        raise AssertionError("run_generation_workflow must never be reached when config-authority is denied")

    monkeypatch.setattr(
        "kriya.workflow.workflow.WorkflowEngine.run_generation_workflow", _must_not_be_called,
    )

    runner = CliRunner()
    old_cwd = os.getcwd()
    os.chdir(ws)
    try:
        result = runner.invoke(main, ["generate", "--resume", "-y"])
    finally:
        os.chdir(old_cwd)

    assert result.exit_code != 0
    assert "Configuration-authority denied" in result.output


# --- CRITICAL: production-CLI end-to-end proofs (real subprocess, no mocks) -

def _run_cli(args, cwd, extra_env=None, timeout=15):
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        r = subprocess.run([KRIYA_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
        return r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        return None, "", "TIMEOUT"


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_cli_plugin_and_mcp_sentinels_absent_without_approval(tmp_path):
    home = tmp_path / "_home"
    ws = tmp_path / "repo"
    plugin_pkg = ws / "safe_plugins" / "greet"
    plugin_pkg.mkdir(parents=True)
    plugin_sentinel = ws / "PLUGIN_SENTINEL.txt"
    (plugin_pkg / "__init__.py").write_text(
        f"import os\nfrom kriya.plugins.plugin import BasePlugin\n"
        f"open({str(plugin_sentinel)!r}, 'w').write('imported')\n"
        f"class GreetPlugin(BasePlugin):\n"
        f"    @property\n    def name(self): return 'greet'\n"
        f"    @property\n    def version(self): return '0.0.1'\n"
    )
    mcp_sentinel = tmp_path / "MCP_SENTINEL.txt"
    server_script = tmp_path / "server.py"
    server_script.write_text(f"import pathlib; pathlib.Path({str(mcp_sentinel)!r}).write_text('started')\n")
    _write_yaml(ws / "kriya.yaml", {
        "plugins": {"directory": "./safe_plugins", "enabled": ["greet"]},
        "mcp": {"approved_server": {"command": sys.executable, "args": [str(server_script)]}},
    })

    code, out, err = _run_cli(["plugins"], cwd=str(ws), extra_env={"KRIYA_AUTHORITY_HOME": str(home)}, timeout=10)

    assert code != 0
    assert not plugin_sentinel.exists()
    assert not mcp_sentinel.exists()


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="editable install not present at .venv/bin/kriya")
def test_cli_approval_gates_real_plugin_import_and_mcp_spawn(tmp_path):
    """P2 is proving AUTHORIZATION, not containment - once approved, the
    configuration proceeds to the normal, EXISTING plugin/MCP path exactly
    as it would have pre-SEC-009. This does not claim the resulting
    process/plugin is safe; only that authorization correctly gates
    reachability. The disposable plugin here is a harmless module-level
    sentinel only, per this risk's own instruction."""
    home = tmp_path / "_home"
    ws = tmp_path / "repo"
    plugin_pkg = ws / "safe_plugins" / "greet"
    plugin_pkg.mkdir(parents=True)
    plugin_sentinel = ws / "PLUGIN_SENTINEL.txt"
    (plugin_pkg / "__init__.py").write_text(
        f"import os\nfrom kriya.plugins.plugin import BasePlugin\n"
        f"open({str(plugin_sentinel)!r}, 'w').write('imported')\n"
        f"class GreetPlugin(BasePlugin):\n"
        f"    @property\n    def name(self): return 'greet'\n"
        f"    @property\n    def version(self): return '0.0.1'\n"
    )
    mcp_sentinel = tmp_path / "MCP_SENTINEL.txt"
    server_script = tmp_path / "server.py"
    # Writes its sentinel immediately, then sleeps - proving spawn happened
    # does not require completing a real MCP handshake (a separate,
    # out-of-scope SEC-003/004/005 concern), only that the process started.
    server_script.write_text(
        f"import pathlib, time\npathlib.Path({str(mcp_sentinel)!r}).write_text('started')\ntime.sleep(30)\n"
    )
    _write_yaml(ws / "kriya.yaml", {
        "plugins": {"directory": "./safe_plugins", "enabled": ["greet"]},
        "mcp": {"approved_server": {"command": sys.executable, "args": [str(server_script)]}},
    })

    extra_env = {"KRIYA_AUTHORITY_HOME": str(home)}
    code, out, err = _run_cli(["authority", "approve", "--confirm"], cwd=str(ws), extra_env=extra_env, timeout=15)
    assert code == 0, (code, out, err)

    # This run is expected to time out (the disposable server never speaks
    # real MCP protocol) - the sentinels, not the exit code, are the proof.
    _run_cli(["plugins"], cwd=str(ws), extra_env=extra_env, timeout=8)

    assert plugin_sentinel.exists(), "approved plugin must be imported"
    assert mcp_sentinel.exists(), "approved MCP server must be spawned"
