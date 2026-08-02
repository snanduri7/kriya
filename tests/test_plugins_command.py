import sys
from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def _write_plugin(plugin_dir, folder_name, class_name, plugin_name, init_body):
    pkg = plugin_dir / folder_name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(f"""
from kriya.plugins.plugin import BasePlugin

class {class_name}(BasePlugin):
    @property
    def name(self) -> str:
        return "{plugin_name}"

    @property
    def version(self) -> str:
        return "1.0"

{init_body}
""")


def _cfg(plugin_dir):
    cfg = AppConfig()
    cfg.plugins.directory = str(plugin_dir)
    return cfg


def test_plugins_command_reports_initialize_failure_not_just_discovery(tmp_path):
    """Regression test for a real bug found via code review: the `plugins` CLI
    command called PluginManager.discover_and_load() but never initialize_all(),
    unlike every other real call site (tools list/execute, generate) - so it
    reported a plugin as "loaded" purely from a successful __init__, without ever
    attempting the initialize() step where a plugin actually registers its
    tools/listeners. Verified live before fixing: a plugin whose initialize()
    deliberately raises showed as cleanly loaded via `kriya plugins`, with zero
    indication it would fail real usage - confirmed the same plugin dir made
    `kriya tools list` fail outright. Now `plugins` actually attempts
    initialize() for each plugin and reports real per-plugin status."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir, "badplugin", "BadPlugin", "bad_plugin",
        '    async def initialize(self) -> None:\n        raise RuntimeError("missing required dependency \'foo-sdk\'")',
    )
    sys.modules.pop("badplugin", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["plugins"])

    assert "FAILED" in res.output
    assert "foo-sdk" in res.output
    assert res.exit_code != 0


def test_plugins_command_shows_working_plugin_as_initialized(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir, "goodplugin", "GoodPlugin", "good_plugin",
        "    async def initialize(self) -> None:\n        pass",
    )
    sys.modules.pop("goodplugin", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["plugins"])

    assert "INITIALIZED" in res.output
    assert res.exit_code == 0


def test_plugins_command_reports_every_plugin_even_when_one_fails(tmp_path):
    """A broken plugin must not hide the status of other, unrelated plugins -
    the command does its own tolerant per-plugin loop rather than reusing
    PluginManager.initialize_all(), which raises on the first failure and would
    abort before later plugins are even attempted."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir, "badplugin2", "BadPlugin2", "bad_plugin_2",
        '    async def initialize(self) -> None:\n        raise RuntimeError("boom")',
    )
    _write_plugin(
        plugin_dir, "goodplugin2", "GoodPlugin2", "good_plugin_2",
        "    async def initialize(self) -> None:\n        pass",
    )
    sys.modules.pop("badplugin2", None)
    sys.modules.pop("goodplugin2", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["plugins"])

    assert "bad_plugin_2" in res.output and "FAILED" in res.output
    assert "good_plugin_2" in res.output and "INITIALIZED" in res.output
    assert res.exit_code != 0
