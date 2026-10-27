"""Shared support of the D1 direct-tool tests (not a test module): a real plugin whose tool has an OBSERVABLE side effect (a line in a log outside the workspace), and the
invocation of `kriya tools execute` over a real git workspace."""
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from unittest.mock import patch  # noqa: E402

from click.testing import CliRunner  # noqa: E402
from test_tools_command import _cfg, _write_plugin  # noqa: E402

TOOL = "d1_effect_tool"
RESULT_TEXT = "TOOL-RESULT-TEXT"

PLUGIN = """
import glob
import json
import os
from kriya.plugins.plugin import BasePlugin
from kriya.tools.tool import BaseTool
from pydantic import BaseModel

class _Args(BaseModel):
    pass

def _log(line):
    with open(os.environ["D1_EFFECT_LOG"], "a", encoding="utf-8") as handle:
        handle.write(line + "\\n")

def _records_on_disk():
    runs = os.path.join(os.getcwd(), ".kriya", "control", "runs", "*.json")
    return [json.load(open(path)).get("tool_execution") for path in sorted(glob.glob(runs))]

class _EffectTool(BaseTool):
    name = "__TOOL__"
    description = "A tool with an observable external side effect."
    arguments_schema = _Args
    async def _run(self, args):
        window = os.environ.get("D1_WINDOW", "")
        _log("ON_DISK_AT_EFFECT=" + json.dumps(_records_on_disk()))
        if window == "before_effect":
            os._exit(137)
        _log("EFFECT_BEGIN")
        if window == "during_effect":
            os._exit(137)
        _log("EFFECT_DONE")
        return "__RESULT__"

class D1EffectPlugin(BasePlugin):
    @property
    def name(self) -> str:
        return "d1_effect_plugin"

    @property
    def version(self) -> str:
        return "1.0"

    async def initialize(self) -> None:
        self.kernel.registry.register("tool", "__TOOL__", _EffectTool())
""".replace("__TOOL__", TOOL).replace("__RESULT__", RESULT_TEXT)


def make_workspace(base: pathlib.Path):
    """(workspace git repo, plugin dir, effects log path) under ``base``."""
    workspace, plugins = base / "ws", base / "plugins"
    workspace.mkdir()
    plugins.mkdir()
    subprocess.run(["git", "init", "-q", str(workspace)], check=True)
    _write_plugin(plugins, "d1effect", PLUGIN)
    sys.modules.pop("d1effect", None)
    return workspace, plugins, base / "effects.log"


def execute_tool(workspace, plugins, effects_log, *, window="", pre=None):
    """Run `kriya tools execute d1_effect_tool` in-process from ``workspace``; returns the click Result."""
    os.environ["D1_EFFECT_LOG"] = str(effects_log)
    os.environ["D1_WINDOW"] = window
    cwd = os.getcwd()
    os.chdir(workspace)
    try:
        with patch("kriya.cli.load_config", return_value=_cfg(plugins)):
            sys.modules.pop("d1effect", None)
            from kriya.cli import main
            return CliRunner().invoke(main, ["tools", "execute", TOOL, "{}"])
    finally:
        os.chdir(cwd)


def lines(path):
    return path.read_text().splitlines() if path.exists() else []
