"""Shared test-support helper for obtaining Kriya's core `plugins/
core_tools` module the SAME way production actually loads it - via
kriya/plugins/plugin.py::PluginManager.discover_and_load() - never a
direct `import plugins.core_tools` or `from plugins.core_tools import
...`, which corresponds to NO real production code path at all.

Root cause this exists to close (PLANNER-ROBUST-001 plugin-bootstrap
investigation, 2026-09-19): `plugins/` has no `__init__.py` (it is a
namespace package). PRD-002 now includes plugins.core_tools in release
wheels while preserving its existing top-level location and production
loader. Every real
production plugin-loading call site (kriya/cli.py's several `PluginManager
(kernel=kernel, plugin_dir=cfg.plugins.directory)` constructions)
resolves `plugins.directory` to an ABSOLUTE path and hands it to
PluginManager, which inserts THAT directory itself onto sys.path and
`importlib.import_module("core_tools")`s it as a bare top-level module -
confirmed live from outside the repo entirely (`kriya tools list` run
from /tmp discovers and loads every core tool with zero CWD/PYTHONPATH
dependency). `import plugins.core_tools` only ever resolves when the REPO
ROOT (not the plugins/ directory itself) happens to already be on
sys.path - true only when some OTHER test file's own ad-hoc `sys.path.
insert(repo_root)` has already run earlier in the same pytest process, a
real, confirmed test-order state leak (test_tools.py/test_prd_tools.py/
test_sec005_shell_acquisition_network.py each independently carried this
hack; test_tool001_autonomous_tool_execution.py had none of its own and
surfaced ModuleNotFoundError the first time it was run in a focused
subset that didn't happen to include one of those three files first).

This module fixes it at the actual owning boundary: every test that needs
a real, production-loaded core tool class goes through
`load_core_tools_module()` instead of importing the plugin module by
dotted path. No sys.path mutation of its own; PluginManager's own
sys.path.insert(0, plugins_dir) is production's PRE-EXISTING, unchanged
behavior, not a new test hack. Location/CWD/PYTHONPATH/import-order-
independent (the plugins directory is derived from this file's own real,
absolute path, exactly as a repo-root kriya.yaml's own `./plugins` would
resolve under kriya/config/config.py::load_config()'s real config_dir-
relative resolution - never a CWD-relative guess). Process-cached (a
module-level singleton) so repeated calls across many tests never re-run
filesystem discovery/importlib resolution - discovery happens at most
once per test session, exactly like a real Kriya CLI invocation discovers
its plugins once at Kernel startup, never per tool execution."""
import sys
from pathlib import Path
from typing import Any

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.plugins.plugin import PluginManager

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PLUGINS_DIR = str(_REPO_ROOT / "plugins")

_cached_core_tools_module: Any = None


def load_core_tools_module() -> Any:
    """Returns the real `core_tools` plugin module object - the exact
    module PluginManager.discover_and_load() imports in production,
    carrying every real tool class (FilesystemTool, ShellTool, GitTool,
    SearchTool, ASTTool, ValidationTool/*Args dataclasses) and module-
    level helpers (e.g. resolve_containment_backend) a test may need to
    reference or monkeypatch. Cached after the first call - safe to call
    from every test that needs it without any test ordering/setup
    requirement of its own."""
    global _cached_core_tools_module
    if _cached_core_tools_module is not None:
        return _cached_core_tools_module

    kernel = Kernel(config=AppConfig())
    plugin_manager = PluginManager(kernel=kernel, plugin_dir=_PLUGINS_DIR)
    plugin_manager.discover_and_load(enabled_plugins=[])
    if "core_tools" not in sys.modules:
        raise RuntimeError(
            f"PluginManager failed to discover/load the core_tools plugin from {_PLUGINS_DIR!r} - "
            "this indicates a real production plugin-bootstrap failure, not a test-only import "
            "problem; check plugins/core_tools/__init__.py for an import-time error."
        )
    _cached_core_tools_module = sys.modules["core_tools"]
    return _cached_core_tools_module
