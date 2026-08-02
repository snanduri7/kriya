import json
from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig, FallbackModelConfig, MCPServerConfig


def test_config_redacts_top_level_llm_api_key():
    """Regression test for a real bug found via code review: `kriya config` dumped
    cfg.model_dump_json() verbatim, which includes llm.api_key in plaintext.
    user_guide.md documents this field as holding a real credential for remote
    endpoints ("set a real key for remote endpoints"), so a user pasting `kriya
    config` output into a bug report / Slack message / support ticket would leak
    it. Verified live before fixing that the literal secret string appeared in
    the command's real output."""
    cfg = AppConfig()
    cfg.llm.api_key = "sk-real-secret-key-abc123"

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["config"])

    assert res.exit_code == 0
    assert "sk-real-secret-key-abc123" not in res.output
    assert "***REDACTED***" in res.output


def test_config_redacts_llm_chain_and_agent_role_api_keys():
    """Same bug, deeper nesting: llm_chain fallback entries and each agent_llms
    role's own llm/llm_chain override also carry their own api_key field - a
    naive top-level-only redaction would miss these."""
    cfg = AppConfig()
    cfg.llm_chain = [FallbackModelConfig(model="fallback-model", api_key="sk-fallback-secret")]
    cfg.agent_llms.reviewer.llm = cfg.llm.model_copy()
    cfg.agent_llms.reviewer.llm.api_key = "sk-reviewer-secret"

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["config"])

    assert "sk-fallback-secret" not in res.output
    assert "sk-reviewer-secret" not in res.output


def test_config_redacts_mcp_server_env_values_but_keeps_keys():
    """Same bug, different shape: MCPServerConfig.env is an arbitrary dict of
    env vars passed to the MCP subprocess, which commonly carry real tokens
    (e.g. GITHUB_TOKEN). Key names must stay visible for diagnostic value -
    only the values are secret."""
    cfg = AppConfig()
    cfg.mcp = {
        "github": MCPServerConfig(command="npx", args=["-y", "gh-mcp"], env={"GITHUB_TOKEN": "ghp_realtoken456"})
    }

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["config"])

    assert "ghp_realtoken456" not in res.output
    assert "GITHUB_TOKEN" in res.output
    assert "***REDACTED***" in res.output


def test_config_output_still_valid_json_and_preserves_non_secret_fields():
    cfg = AppConfig()
    cfg.llm.model = "qwen3-coder:30b"

    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["config"])

    parsed = json.loads(res.output)
    assert parsed["llm"]["model"] == "qwen3-coder:30b"
    assert parsed["llm"]["api_key"] == "***REDACTED***"
