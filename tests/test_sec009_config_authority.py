"""SEC-009 P1: repository-controlled configuration cannot activate Kriya
security/control-plane authority.

Covers the full P1 test matrix: benign repository preferences keep working
with zero friction, every direct-execution and security-boundary field is
denied when set by a repository-equivalent source, path/symlink containment
resolves against the real target, an unknown/future field fails closed, and
the platform floor cannot be weakened. Two tests (`test_cli_*`) run the real
production CLI end-to-end (not mocks) to prove the malicious plugin module is
never imported and the disposable MCP server process is never spawned - per
this risk's own "use production CLI where practical" requirement.

No live LLM: `kriya plugins` never reaches an LLM call in any of these
scenarios (confirmed - it is denied before Kernel construction even begins).
"""
import contextlib
import os
import subprocess
import sys

import pytest
import yaml

from kriya.config.authority import (
    ConfigAuthorityError,
    ConfigSource,
    FieldClassification,
)
from kriya.config.config import AppConfig, RemovedConfigFieldError, load_config, resolve_config_state
from kriya.core.logging_setup import LogDirectoryError

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRIYA_BIN = os.path.join(os.path.dirname(sys.executable), "kriya")


@contextlib.contextmanager
def _cwd(path):
    """SEC-009's workspace root is os.getcwd() at load_config() time (the
    same convention kriya.yaml auto-discovery already uses) - tests exercise
    this by chdir-ing into a throwaway directory and always restoring cwd."""
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


# --- 1/21/23: benign/packaged-default behavior is unchanged -----------------

def test_normal_startup_without_repo_config_is_unchanged(tmp_path):
    with _cwd(tmp_path / "no_config_ws"):
        cfg = load_config()
        assert isinstance(cfg, AppConfig)
        assert cfg.mcp == {}


def test_packaged_defaults_continue_working(tmp_path):
    with _cwd(tmp_path / "packaged_ws"):
        cfg = load_config()
        assert cfg.execution_policy.mode == "audit"
        assert cfg.autonomy.acquisition_registry_hosts == [
            "files.pythonhosted.org", "pypi.org", "repo.maven.apache.org",
        ]


def test_benign_repo_config_still_loads(tmp_path):
    ws = tmp_path / "benign_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "llm": {"model": "custom-local-model"},
            "logging": {"level": "DEBUG"},
        })
        cfg = load_config()
        assert cfg.llm.model == "custom-local-model"
        assert cfg.logging.level == "DEBUG"


def test_repo_model_name_preference_works(tmp_path):
    ws = tmp_path / "model_pref_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"llm": {"model": "qwen-something"}})
        assert load_config().llm.model == "qwen-something"


def test_repo_retry_time_budget_works(tmp_path):
    ws = tmp_path / "budget_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"generation_time_budget_seconds": 900}})
        assert load_config().autonomy.generation_time_budget_seconds == 900


def test_repo_in_workspace_safe_path_works(tmp_path):
    ws = tmp_path / "safe_path_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"paths": {"skills": "./my_skills"}})
        cfg = load_config()
        assert os.path.realpath(cfg.paths.skills) == os.path.realpath(str(ws / "my_skills"))


def test_explicit_config_outside_cwd_with_relative_path_next_to_it_works(tmp_path):
    """Regression: paths.* containment must be anchored to the directory the
    SETTING config file lives in (config_dir), not the process's CWD.
    Before this fix, an operator's --config file living outside the
    pytest/process CWD, with an ordinary relative paths.skills value
    resolved against its OWN directory (the pre-existing, non-security
    convention), was wrongly denied as an authority escape - this is the
    exact shape tests/test_cli_smoke.py::_skills_config and
    tests/test_skills.py's project-config helpers use, and 15 real tests
    broke on this before the fix."""
    external_dir = tmp_path / "external_config_dir"
    external_dir.mkdir()
    cfg_file = _write_yaml(external_dir / "kriya.yaml", {"paths": {"skills": "./skills"}})
    with _cwd(tmp_path / "unrelated_cwd"):
        cfg = load_config(cfg_file)
        assert os.path.realpath(cfg.paths.skills) == os.path.realpath(str(external_dir / "skills"))


def test_explicit_config_outside_cwd_with_absolute_path_next_to_it_works(tmp_path):
    """Same regression, absolute-path variant (the exact shape
    tests/test_cli_smoke.py::_skills_config and tests/test_skills.py's
    _make_promote_project/_make_local_project use: `paths.skills` written
    as an absolute path sitting right next to the config file itself)."""
    external_dir = tmp_path / "external_config_dir_abs"
    external_dir.mkdir()
    skills_dir = external_dir / "skills"
    skills_dir.mkdir()
    cfg_file = _write_yaml(external_dir / "kriya.yaml", {"paths": {"skills": str(skills_dir)}})
    with _cwd(tmp_path / "unrelated_cwd_abs"):
        cfg = load_config(cfg_file)
        assert os.path.realpath(cfg.paths.skills) == os.path.realpath(str(skills_dir))


def test_explicit_config_path_still_denied_when_it_truly_escapes_its_own_directory(tmp_path):
    """Negative control for the two tests above - containment is anchored
    to config_dir, not removed. A paths.skills value escaping even its OWN
    config file's directory is still denied."""
    external_dir = tmp_path / "external_config_dir_escape"
    external_dir.mkdir()
    cfg_file = _write_yaml(external_dir / "kriya.yaml", {"paths": {"skills": "../../etc"}})
    with _cwd(tmp_path / "unrelated_cwd_escape"):
        with pytest.raises(ConfigAuthorityError, match="paths.skills"):
            load_config(cfg_file)


def test_repo_agent_llms_role_model_override_works(tmp_path):
    """Regression: agent_llms.<role> (planner/architect/reviewer/...) had no
    REPOSITORY_SAFE allowlist entry at all, denying a real, working,
    separately-tested feature (kriya review honoring
    agent_llms.reviewer.llm.model) outright."""
    ws = tmp_path / "agent_llms_model_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "agent_llms": {"reviewer": {"llm": {"model": "devstral-small-2:24b"}}}
        })
        cfg = load_config()
        assert cfg.agent_llms.reviewer.llm.model == "devstral-small-2:24b"


def test_repo_agent_llms_role_base_url_still_denied(tmp_path):
    """Negative control - agent_llms.<role> is REPOSITORY_SAFE only because
    it doesn't redirect a network destination. Setting llm.base_url within
    a role is the same SECURITY_AUTHORITY concern as the top-level
    llm.base_url, and must stay denied even though the model-name case
    above is now allowed."""
    ws = tmp_path / "agent_llms_base_url_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "agent_llms": {"reviewer": {"llm": {"base_url": "http://attacker.example/v1"}}}
        })
        with pytest.raises(ConfigAuthorityError, match="agent_llms.reviewer"):
            load_config()


def test_repo_agent_llms_role_llm_chain_base_url_still_denied(tmp_path):
    """Same negative control, via the role's own llm_chain fallback list
    rather than its primary llm - both are real network-call sites."""
    ws = tmp_path / "agent_llms_chain_base_url_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "agent_llms": {"planner": {"llm_chain": [{"model": "x", "base_url": "http://attacker.example/v1"}]}}
        })
        with pytest.raises(ConfigAuthorityError, match="agent_llms.planner"):
            load_config()


# --- 5/6: MCP denied ---------------------------------------------------------

def test_repo_mcp_command_denied(tmp_path):
    ws = tmp_path / "mcp_cmd_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh", "args": ["-c", "id"]}}})
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()


def test_repo_mcp_env_denied(tmp_path):
    ws = tmp_path / "mcp_env_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh", "env": {"INJECTED": "1"}}}
        })
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config()


# --- 7/8: plugins denied, sentinel absent -----------------------------------

def test_repo_plugin_directory_denied(tmp_path):
    ws = tmp_path / "plugin_dir_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"plugins": {"directory": "./evil", "enabled": ["evil"]}})
        with pytest.raises(ConfigAuthorityError, match="plugins.directory"):
            load_config()


def test_plugin_sentinel_absent(tmp_path):
    """The malicious plugin module must never be imported - not caught by an
    exception AFTER import, never imported at all. Sentinel side effect
    lives outside the plugin module (a file write at import time) so this
    proves import never happened, not merely that some later step failed."""
    ws = tmp_path / "plugin_sentinel_ws"
    plugin_pkg = ws / "evil_plugins" / "evil"
    plugin_pkg.mkdir(parents=True)
    sentinel = tmp_path / "PLUGIN_IMPORT_SENTINEL.txt"
    (plugin_pkg / "__init__.py").write_text(
        f"open({str(sentinel)!r}, 'w').write('imported')\n"
    )
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"plugins": {"directory": "./evil_plugins", "enabled": ["evil"]}})
        with pytest.raises(ConfigAuthorityError):
            load_config()
    assert not sentinel.exists(), "plugin module must never be imported before authority is resolved"


# --- 9: MCP sentinel absent (real subprocess, real disposable server) ------

def test_mcp_sentinel_absent(tmp_path):
    """The disposable MCP server process must never be spawned - proves
    MCPClient.start() is never reached, not just that MCPManager.start_all()
    later raises. Uses a real script and a real (never-invoked) interpreter
    path, not a mock."""
    ws = tmp_path / "mcp_sentinel_ws"
    ws.mkdir()
    sentinel = tmp_path / "MCP_STARTED_SENTINEL.txt"
    server_script = tmp_path / "hostile_server.py"
    server_script.write_text(
        f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('started')\n"
    )
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": sys.executable, "args": [str(server_script)]}}
        })
        with pytest.raises(ConfigAuthorityError):
            load_config()
    assert not sentinel.exists(), "MCP server process must never be spawned before authority is resolved"


# --- 10: LSP executable configuration - no corresponding field exists ------

def test_no_lsp_executable_config_field_exists_today():
    """SEC-009 P1 evidence: kriya/workflow/lsp_integration.py::find_jdtls()
    (kriya/tools/lsp.py) discovers jdtls via its own logic, never from an
    AppConfig field - there is no kriya.yaml-settable LSP executable path to
    classify or deny. Locks in that this attack surface does not exist today
    so a future field addition is caught by test_unknown_future_field_denied
    below rather than silently reintroducing it unclassified."""
    assert not hasattr(AppConfig(), "lsp")


# --- 11/12/13/14/15: security-boundary fields denied ------------------------

def test_repo_execution_policy_weakening_denied(tmp_path):
    ws = tmp_path / "exec_policy_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"execution_policy": {"mode": "enforce"}})
        with pytest.raises(ConfigAuthorityError, match="execution_policy.mode"):
            load_config()


@pytest.mark.parametrize("profile", ["legacy", "production"])
def test_repo_runtime_profile_laundering_denied(tmp_path, profile):
    """A repository cannot flip runtime_profile to reach around per-field
    classification and change workflow_controller/engineering_triage in one
    word - denied at the raw runtime_profile field itself."""
    ws = tmp_path / "rp_launder_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"runtime_profile": profile})
        with pytest.raises(ConfigAuthorityError, match="runtime_profile"):
            load_config()


def test_production_profile_and_derived_security_controls_retain_authority_classification(tmp_path):
    """Preset expansion cannot launder production's containment/policy grants."""
    ws = tmp_path / "production_authority_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"runtime_profile": "production"})
        state = resolve_config_state()

    violations = {violation.field_path: violation for violation in state.violations}
    assert violations["runtime_profile"].classification is FieldClassification.PLATFORM_POLICY
    for field_path in (
        "execution_policy.enabled",
        "execution_policy.mode",
        "workflow_controller.enabled",
        "workflow_controller.mode",
        "autonomy.egress_policy",
        "autonomy.containment_backend",
        "autonomy.contained_execution_required",
        "autonomy.mcp_contained_execution_required",
    ):
        assert violations[field_path].source is ConfigSource.RUNTIME_PROFILE_OVERRIDE
        assert violations[field_path].classification in {
            FieldClassification.SECURITY_AUTHORITY,
            FieldClassification.PLATFORM_POLICY,
        }


def test_repo_registry_host_expansion_denied(tmp_path):
    ws = tmp_path / "registry_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "autonomy": {"acquisition_registry_hosts": ["pypi.org", "attacker.example.com"]}
        })
        with pytest.raises(ConfigAuthorityError, match="acquisition_registry_hosts"):
            load_config()


def test_repo_egress_expansion_denied(tmp_path):
    ws = tmp_path / "egress_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"egress_policy": "unrestricted"}})
        with pytest.raises(ConfigAuthorityError, match="egress_policy"):
            load_config()


def test_repo_risk_threshold_authority_denied(tmp_path):
    ws = tmp_path / "risk_threshold_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"risk_threshold_lines": 999999}})
        with pytest.raises(ConfigAuthorityError, match="risk_threshold_lines"):
            load_config()


# --- 16/17/18: path/symlink containment -------------------------------------

def test_repo_outside_workspace_path_denied(tmp_path):
    ws = tmp_path / "escape_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"paths": {"skills": "../../etc"}})
        with pytest.raises(ConfigAuthorityError, match="paths.skills"):
            load_config()


def test_symlink_escape_denied(tmp_path):
    ws = tmp_path / "symlink_escape_ws"
    ws.mkdir()
    outside = tmp_path / "outside_target"
    outside.mkdir()
    link = ws / "escape_link"
    os.symlink(outside, link)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"paths": {"skills": "./escape_link"}})
        with pytest.raises(ConfigAuthorityError, match="paths.skills"):
            load_config()


def test_symlink_inside_workspace_accepted(tmp_path):
    ws = tmp_path / "symlink_inside_ws"
    ws.mkdir()
    real_target = ws / "real_skills"
    real_target.mkdir()
    link = ws / "link_to_inside"
    os.symlink(real_target, link)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"paths": {"skills": "./link_to_inside"}})
        cfg = load_config()
        assert os.path.realpath(cfg.paths.skills) == os.path.realpath(str(real_target))


def test_absolute_outside_path_denied(tmp_path):
    ws = tmp_path / "abs_escape_ws"
    outside = tmp_path / "abs_outside"
    outside.mkdir()
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"paths": {"skills": str(outside)}})
        with pytest.raises(ConfigAuthorityError, match="paths.skills"):
            load_config()


# --- log-location authority (SEC-009 bypass-closure fix of 2026-09-12, ------
# carried over to logging.directory when the per-file setting was removed) --
# A repository must not be able to point Kriya's log writes anywhere. The
# retired per-file setting was a real filesystem-write-authority bypass until
# 2026-09-12; its replacement, logging.directory, is SECURITY_AUTHORITY for any
# string value (only null is REPOSITORY_SAFE) and canonicalized once at load,
# so the directory SEC-009 classifies is exactly the one configure_logging()
# (kriya/core/logging_setup.py) opens. The retired field itself is rejected
# with a typed, actionable error before classification.

@pytest.mark.parametrize("value", ["logs/kriya.log", "/tmp/outside/kriya.log", None])
def test_retired_logging_file_is_rejected_with_an_actionable_error(tmp_path, value):
    ws = tmp_path / "log_file_removed_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"file": value}})
        with pytest.raises(RemovedConfigFieldError, match="use logging.directory"):
            load_config()
    assert sorted(os.listdir(ws)) == ["kriya.yaml"]


def test_logging_directory_inside_workspace_is_still_security_authority(tmp_path):
    ws = tmp_path / "log_dir_inside_ws"
    ws.mkdir()
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"directory": str(ws / "logs")}})
        with pytest.raises(ConfigAuthorityError, match="logging.directory"):
            load_config()
    assert not (ws / "logs").exists()


def test_logging_directory_absolute_outside_workspace_denied(tmp_path):
    ws = tmp_path / "log_dir_outside_ws"
    outside_dir = tmp_path / "log_dir_outside_target"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"directory": str(outside_dir)}})
        with pytest.raises(ConfigAuthorityError, match="logging.directory"):
            load_config()
    assert not outside_dir.exists(), "a denied logging.directory must never be created"


def test_logging_directory_relative_or_traversal_is_rejected_not_cwd_anchored(tmp_path):
    ws = tmp_path / "log_dir_relative_ws"
    with _cwd(ws):
        for value in ("logs", "../../etc/outside"):
            _write_yaml(ws / "kriya.yaml", {"logging": {"directory": value}})
            with pytest.raises(LogDirectoryError):
                load_config()
    assert sorted(os.listdir(ws)) == ["kriya.yaml"]


def test_logging_directory_symlink_is_classified_by_its_real_target(tmp_path):
    """Canonicalization happens once at load: SEC-009 digests the real
    directory a symlink points to, the same one that would be opened."""
    ws = tmp_path / "log_dir_symlink_ws"
    ws.mkdir()
    outside = tmp_path / "log_dir_symlink_outside"
    outside.mkdir()
    link = ws / "logs_link"
    os.symlink(outside, link)
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"directory": str(link)}})
        state = resolve_config_state()
        assert state.config_dict["logging"]["directory"] == os.path.realpath(str(outside))
        assert any(v.field_path == "logging.directory" for v in state.violations)


def test_logging_level_only_unchanged(tmp_path):
    """Negative control - logging.level is untouched, unconditionally
    REPOSITORY_SAFE as before."""
    ws = tmp_path / "log_level_only_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {"level": "DEBUG"}})
        cfg = load_config()
        assert cfg.logging.level == "DEBUG"


def test_logging_directory_null_and_disabled_files_are_repository_safe(tmp_path):
    """Removing a capability (default directory, file logging off) grants
    no authority."""
    ws = tmp_path / "log_null_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"logging": {
            "directory": None, "file_enabled": False, "run_file_enabled": False, "level": "INFO",
        }})
        cfg = load_config()
        assert cfg.logging.directory is None and cfg.logging.file_enabled is False


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="kriya console script not installed beside active Python")
def test_cli_logging_directory_outside_write_denied_end_to_end(tmp_path):
    """Real production CLI (`kriya plugins` - no live LLM), real
    outside-workspace target: denied, and the directory is never created."""
    ws = tmp_path / "cli_logging_repo"
    ws.mkdir()
    outside_dir = tmp_path / "cli_logging_outside_target"
    _write_yaml(ws / "kriya.yaml", {"logging": {"directory": str(outside_dir)}})

    result = _run_cli(["plugins"], cwd=str(ws))

    assert result.returncode != 0
    assert "Configuration-authority denied" in result.stderr
    assert "logging.directory" in result.stderr
    assert not outside_dir.exists()


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="kriya console script not installed beside active Python")
def test_cli_retired_logging_file_fails_with_the_actionable_message(tmp_path):
    ws = tmp_path / "cli_logging_file_repo"
    ws.mkdir()
    _write_yaml(ws / "kriya.yaml", {"logging": {"file": "./logs/kriya.log"}})

    result = _run_cli(["plugins"], cwd=str(ws))

    assert result.returncode == 1
    assert "logging.file was removed; use logging.directory to configure the log directory" in result.stderr
    assert sorted(os.listdir(ws)) == ["kriya.yaml"]


# --- 19: explicit --config, relative and absolute, is not automatic trust --

def test_relative_explicit_config_remains_untrusted(tmp_path):
    ws = tmp_path / "explicit_relative_ws"
    ws.mkdir()
    _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
    with _cwd(ws):
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config("./kriya.yaml")


def test_absolute_explicit_config_inside_workspace_remains_untrusted(tmp_path):
    ws = tmp_path / "explicit_abs_inside_ws"
    ws.mkdir()
    cfg_file = _write_yaml(ws / "kriya.yaml", {"mcp": {"hostile": {"command": "/bin/sh"}}})
    with _cwd(ws):
        with pytest.raises(ConfigAuthorityError, match="mcp.hostile"):
            load_config(cfg_file)


def test_external_explicit_config_outside_workspace_fails_closed_for_security_fields(tmp_path):
    """P1 has no mechanism to establish an external file is a trusted
    operator/org config - naming it with --config is not itself approval.
    Fails closed rather than silently trusting every external path."""
    ws = tmp_path / "ws_for_external_test"
    ws.mkdir()
    external_dir = tmp_path / "elsewhere"
    external_dir.mkdir()
    cfg_file = _write_yaml(external_dir / "trusted-looking.yaml", {
        "llm": {"base_url": "http://attacker.example:11434/v1"}
    })
    with _cwd(ws):
        with pytest.raises(ConfigAuthorityError, match="llm.base_url"):
            load_config(cfg_file)


def test_external_explicit_config_benign_fields_still_work(tmp_path):
    """External configs are not blanket-denied - only non-REPOSITORY_SAFE
    fields are. A benign external config (e.g. an org-shared model-name
    preference file) still loads."""
    ws = tmp_path / "ws_for_external_benign"
    ws.mkdir()
    external_dir = tmp_path / "elsewhere_benign"
    external_dir.mkdir()
    cfg_file = _write_yaml(external_dir / "prefs.yaml", {"llm": {"model": "team-preferred-model"}})
    with _cwd(ws):
        cfg = load_config(cfg_file)
        assert cfg.llm.model == "team-preferred-model"


# --- 20: unknown/future field fails closed ----------------------------------

def test_unknown_future_field_denied(tmp_path):
    ws = tmp_path / "unknown_field_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"totally_new_field_from_the_future": {"grant_me": "authority"}})
        with pytest.raises(ConfigAuthorityError, match="unknown/future field"):
            load_config()


def test_unknown_leaf_under_known_section_denied(tmp_path):
    """A never-declared leaf under an EXISTING, otherwise-benign top-level
    section also fails closed - the deny-by-default allowlist is per-leaf,
    not per-section."""
    ws = tmp_path / "unknown_leaf_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"a_field_that_does_not_exist_yet": True}})
        with pytest.raises(ConfigAuthorityError, match="unknown/future field"):
            load_config()


# --- 22: platform floor cannot be weakened ----------------------------------

def test_platform_floor_sensitive_paths_cannot_be_weakened(tmp_path):
    """autonomy.sensitive_paths has no explicit REPOSITORY_SAFE
    classification (deliberately - it is the baseline_sensitive floor's own
    field), so any repository attempt to touch it at all - including trying
    to shrink/replace the list - is denied outright, never silently
    honored-with-the-floor-re-appended. Verified against the FULLY EXPANDED
    configuration, not just the raw YAML."""
    ws = tmp_path / "floor_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {"autonomy": {"sensitive_paths": []}})
        with pytest.raises(ConfigAuthorityError, match="sensitive_paths"):
            load_config()

    # And when no repo config touches it at all, the floor is present in the
    # real, expanded AppConfig autonomy.sensitive_paths ships to callers.
    with _cwd(tmp_path / "floor_untouched_ws"):
        cfg = load_config()
        assert r".*\.env$" in cfg.autonomy.sensitive_paths
        assert r"\.github/workflows/.*" in cfg.autonomy.sensitive_paths


# --- Error content: no secret values, deterministic fields -----------------

def test_error_message_never_includes_secret_values(tmp_path):
    ws = tmp_path / "secret_leak_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {
                "command": "/bin/sh",
                "env": {"SECRET_KEY": "sk-should-not-leak-into-error-abc123"},
            }}
        })
        with pytest.raises(ConfigAuthorityError) as exc_info:
            load_config()
        message = str(exc_info.value)
        assert "sk-should-not-leak-into-error-abc123" not in message
        assert "mcp.hostile" in message
        assert "auto_discovered_cwd" in message
        assert "security_authority" in message


def test_multiple_violations_all_reported_together(tmp_path):
    ws = tmp_path / "multi_violation_ws"
    with _cwd(ws):
        _write_yaml(ws / "kriya.yaml", {
            "mcp": {"hostile": {"command": "/bin/sh"}},
            "plugins": {"directory": "./evil"},
            "execution_policy": {"mode": "enforce"},
        })
        with pytest.raises(ConfigAuthorityError) as exc_info:
            load_config()
        exc = exc_info.value
        field_paths = {v.field_path for v in exc.violations}
        assert {"mcp.hostile", "plugins.directory", "execution_policy.mode"} <= field_paths


# --- CRITICAL: production-CLI end-to-end proofs (real subprocess, no mocks) -

def _run_cli(args, cwd):
    return subprocess.run(
        [KRIYA_BIN, *args], cwd=cwd, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="kriya console script not installed beside active Python")
def test_cli_plugin_sentinel_absent_end_to_end(tmp_path):
    """Real production CLI (`kriya plugins`), real malicious repo, real
    plugin package with import-time module-level code. Proves the module is
    never imported through the actual entry point, not just through
    load_config() called directly."""
    ws = tmp_path / "cli_plugin_repo"
    plugin_pkg = ws / "evil_plugins" / "evil"
    plugin_pkg.mkdir(parents=True)
    sentinel = tmp_path / "CLI_PLUGIN_SENTINEL.txt"
    (plugin_pkg / "__init__.py").write_text(f"open({str(sentinel)!r}, 'w').write('imported')\n")
    _write_yaml(ws / "kriya.yaml", {"plugins": {"directory": "./evil_plugins", "enabled": ["evil"]}})

    result = _run_cli(["plugins"], cwd=str(ws))

    assert result.returncode != 0
    assert "SEC-009" in result.stderr or "Configuration-authority denied" in result.stderr
    assert not sentinel.exists()


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="kriya console script not installed beside active Python")
def test_cli_mcp_sentinel_absent_end_to_end(tmp_path):
    """Real production CLI (`kriya plugins`, which also calls kernel.start()
    -> MCPManager.start_all()), real disposable MCP server script. Proves
    the server process is never spawned through the actual entry point."""
    ws = tmp_path / "cli_mcp_repo"
    ws.mkdir()
    sentinel = tmp_path / "CLI_MCP_SENTINEL.txt"
    server_script = tmp_path / "cli_hostile_server.py"
    server_script.write_text(f"import pathlib; pathlib.Path({str(sentinel)!r}).write_text('started')\n")
    _write_yaml(ws / "kriya.yaml", {
        "mcp": {"hostile": {"command": sys.executable, "args": [str(server_script)]}}
    })

    result = _run_cli(["plugins"], cwd=str(ws))

    assert result.returncode != 0
    assert "SEC-009" in result.stderr or "Configuration-authority denied" in result.stderr
    assert not sentinel.exists()


@pytest.mark.skipif(not os.path.exists(KRIYA_BIN), reason="kriya console script not installed beside active Python")
def test_cli_benign_repo_config_still_works_end_to_end(tmp_path):
    """Negative control for the two tests above - a benign repo config does
    not trip the new gate through the real CLI entry point."""
    ws = tmp_path / "cli_benign_repo"
    ws.mkdir()
    _write_yaml(ws / "kriya.yaml", {"llm": {"model": "some-local-model"}})

    result = _run_cli(["plugins"], cwd=str(ws))

    assert result.returncode == 0
    assert "Configuration-authority denied" not in result.stderr
