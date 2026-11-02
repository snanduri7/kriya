import logging
import os
import subprocess
import sys
from unittest.mock import patch

from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig


def _write_plugin(plugin_dir, folder_name, source: str):
    pkg = plugin_dir / folder_name
    pkg.mkdir()
    (pkg / "__init__.py").write_text(source)


def _bad_plugin_source(class_name, plugin_name):
    return f"""
from kriya.plugins.plugin import BasePlugin

class {class_name}(BasePlugin):
    @property
    def name(self) -> str:
        return "{plugin_name}"

    @property
    def version(self) -> str:
        return "1.0"

    async def initialize(self) -> None:
        raise RuntimeError("missing required dependency 'foo-sdk'")
"""


def _good_tool_plugin_source(class_name, plugin_name, tool_name):
    return f"""
from kriya.plugins.plugin import BasePlugin
from kriya.tools.tool import BaseTool
from pydantic import BaseModel

class _Args(BaseModel):
    pass

class _GoodTool(BaseTool):
    name = "{tool_name}"
    description = "A working tool."
    arguments_schema = _Args
    async def _run(self, args):
        return "ok"

class {class_name}(BasePlugin):
    @property
    def name(self) -> str:
        return "{plugin_name}"

    @property
    def version(self) -> str:
        return "1.0"

    async def initialize(self) -> None:
        self.kernel.registry.register("tool", "{tool_name}", _GoodTool())
"""


def _cfg(plugin_dir):
    cfg = AppConfig()
    cfg.plugins.directory = str(plugin_dir)
    return cfg


def test_tools_list_shows_working_plugin_despite_broken_sibling(tmp_path):
    """Regression test for a real bug found via code review: `tools list` called
    PluginManager.initialize_all(), which raises on the first plugin
    initialization failure and aborts before later plugins are even attempted -
    so ONE broken plugin took down tool listing entirely, hiding every other
    (unrelated, working) plugin's tools too. Verified live before fixing with a
    deliberately broken plugin alongside a working one: `tools list` reported
    zero tools and an error, even though the working plugin's tool was fine."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(plugin_dir, "badplugin", _bad_plugin_source("BadPlugin", "bad_plugin"))
    _write_plugin(plugin_dir, "goodplugin", _good_tool_plugin_source("GoodPlugin", "good_plugin", "good_tool"))
    sys.modules.pop("badplugin", None)
    sys.modules.pop("goodplugin", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()
    with patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["tools", "list"])

    assert res.exit_code == 0
    assert "good_tool" in res.output
    assert "[WARNING]" in res.output and "bad_plugin" in res.output


def _hermetic_workspace(tmp_path, monkeypatch):
    """TOOLS-COMMAND-TEST-NONHERMETIC-CWD-001: `tools execute` is a possibly-mutating call, so it consults the
    workspace's recovery state (PRD-006, D-001 nested_control_state) from the CURRENT directory. A test that never
    changes directory runs inside the Kriya source repository, whose committed evidence archive holds an unreadable
    commit record, and is refused for a reason unrelated to what it names. Run in a fresh git work tree instead."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    monkeypatch.chdir(workspace)
    return workspace


def _broken_and_good_plugins(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(plugin_dir, "badplugin2", _bad_plugin_source("BadPlugin2", "bad_plugin_2"))
    _write_plugin(plugin_dir, "goodplugin2", _good_tool_plugin_source("GoodPlugin2", "good_plugin_2", "good_tool_2"))
    sys.modules.pop("badplugin2", None)
    sys.modules.pop("goodplugin2", None)
    return plugin_dir


def test_tools_execute_works_despite_unrelated_broken_plugin(tmp_path, monkeypatch, caplog):
    """Same bug, execute path: a specific, working, explicitly-named tool must
    be executable even when an unrelated plugin fails to initialize."""
    _hermetic_workspace(tmp_path, monkeypatch)
    cfg = _cfg(_broken_and_good_plugins(tmp_path))
    runner = CliRunner()
    with caplog.at_level(logging.ERROR, logger="kriya.cli"), patch("kriya.cli.load_config", return_value=cfg):
        res = runner.invoke(main, ["tools", "execute", "good_tool_2", "{}"])

    assert res.exit_code == 0
    assert "ok" in res.output
    # negative control: the broken plugin really was loaded and really did fail, so the success above is the
    # plugin-tolerance behaviour the test names, not a run in which the broken plugin was never reached.
    assert "Plugin 'bad_plugin_2' failed to initialize" in caplog.text


def test_tools_execute_still_refuses_in_a_workspace_holding_unsettled_nested_state(tmp_path, monkeypatch):
    """The original failure mechanism, reproduced deterministically: control state left unreadable under a
    sub-directory of the work tree makes the possibly-mutating `tools execute` refuse (the policy is unchanged and
    correct). This is what the committed evidence archive did to the un-isolated version of the test above."""
    workspace = _hermetic_workspace(tmp_path, monkeypatch)
    commits = workspace / "sub" / ".kriya" / "control" / "commits"
    commits.mkdir(parents=True)
    (commits / "0123456789abcdef0123456789abcdef.json").write_text("{ not a commit record", encoding="utf-8")
    cfg = _cfg(_broken_and_good_plugins(tmp_path))
    with patch("kriya.cli.load_config", return_value=cfg):
        res = CliRunner().invoke(main, ["tools", "execute", "good_tool_2", "{}"])

    assert res.exit_code == 1
    assert "Recovery Required" in res.output
    assert os.path.realpath(workspace / "sub") in res.output   # the refusal is about THIS workspace's nested state, not an ambient repository's


def test_tools_list_stops_kernel_even_when_listing_raises(tmp_path):
    """Regression test for a real bug found via code review: kernel.start()
    (which spawns real MCP subprocess servers via asyncio.create_subprocess_exec)
    was only ever followed by pm.shutdown_all()/kernel.stop() on the happy path -
    the "No tools registered" early return AND any exception raised while
    listing tools both skipped cleanup entirely. Those subprocesses are not
    reaped when the CLI process exits, so this was a real orphaned-process leak,
    not just a code-tidiness issue. Verified the fix via a spy on Kernel.stop -
    it must still be awaited even when the tool-listing loop raises."""
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(plugin_dir, "brokenschema", """
from kriya.plugins.plugin import BasePlugin
from kriya.tools.tool import BaseTool

class _BrokenSchemaTool(BaseTool):
    name = "broken_schema_tool"
    description = "Has a malformed arguments_schema that breaks listing."
    arguments_schema = None
    async def _run(self, args):
        return "unused"

class BrokenSchemaPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "broken_schema_plugin"
    @property
    def version(self) -> str:
        return "1.0"
    async def initialize(self) -> None:
        self.kernel.registry.register("tool", "broken_schema_tool", _BrokenSchemaTool())
""")
    sys.modules.pop("brokenschema", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()

    from kriya.core.kernel import Kernel

    with patch("kriya.cli.load_config", return_value=cfg), \
         patch.object(Kernel, "stop", autospec=True) as mock_stop:
        res = runner.invoke(main, ["tools", "list"])

    assert res.exit_code != 0
    assert mock_stop.called, "kernel.stop() must run even when the tool-listing loop raises (finally, not happy-path-only)"


def test_tools_execute_stops_kernel_even_on_invalid_json_args(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(plugin_dir, "goodplugin3", _good_tool_plugin_source("GoodPlugin3", "good_plugin_3", "good_tool_3"))
    sys.modules.pop("goodplugin3", None)

    cfg = _cfg(plugin_dir)
    runner = CliRunner()

    from kriya.core.kernel import Kernel

    with patch("kriya.cli.load_config", return_value=cfg), \
         patch.object(Kernel, "stop", autospec=True) as mock_stop:
        res = runner.invoke(main, ["tools", "execute", "good_tool_3", "{not valid json"])

    assert res.exit_code != 0
    assert mock_stop.called, "kernel.stop() must run even when arguments_json fails to parse (finally, not happy-path-only)"
