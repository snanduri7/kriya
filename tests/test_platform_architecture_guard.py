"""Platform architecture guard (ARCH-PLATFORM-001).

Operating-system mechanism lives in kriya/platform/. Everywhere else in
kriya/ and plugins/:
- no import of an OS-only module (fcntl, resource, pwd, grp, termios, tty,
  pty, msvcrt, winreg, _winapi);
- no OS branch or POSIX-only primitive (sys.platform, os.name,
  platform.system/mac_ver/win32_ver/machine, os.killpg/getpgid/setsid/
  setpgid/fork, os.getuid/getgid/geteuid/getegid).

TEMPORARY entries are allowed only for mechanism modules whose migration is
a named, open registry item; the list may only shrink (a stale entry
fails), and a policy or orchestration layer can never be listed. Kriya core
must import with the POSIX-only modules unavailable (checked in a fresh
interpreter: conftest has already imported kriya here).
"""
import ast
import csv
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Set, Tuple

REPO = Path(__file__).resolve().parent.parent
PLATFORM_PACKAGE = "kriya/platform/"
FORBIDDEN_MODULES = {"fcntl", "resource", "pwd", "grp", "termios", "tty", "pty", "msvcrt", "winreg", "_winapi"}
FORBIDDEN_ATTRIBUTES = {
    "sys": {"platform"},
    "os": {"name", "killpg", "getpgid", "setsid", "setpgid", "fork", "getuid", "getgid", "geteuid", "getegid"},
    "platform": {"system", "mac_ver", "win32_ver", "machine"},
}
# Layers that own policy or orchestration: never allowlisted, not even temporarily.
NEVER_ALLOWLISTED = ("kriya/workflow/", "kriya/policy/", "kriya/control/", "kriya/config/", "kriya/metrics/",
                     "kriya/static_analysis/service.py", "kriya/static_analysis/policy", "kriya/agents/")

# path -> (open registry item migrating it, why it is still here)
TEMPORARY_ALLOWLIST: Dict[str, Tuple[str, str]] = {
    "kriya/tools/process.py": ("PLAT-PROCESS-CONTROL-001", "process-group spawn/kill; ProcessControlPort"),
    "kriya/mcp/lifecycle.py": ("PLAT-PROCESS-CONTROL-001", "MCP process-group spawn; ProcessControlPort"),
}

BLOCKED_FOR_IMPORT = ("fcntl", "resource", "pwd", "grp", "termios")
CORE_MODULES = ("kriya.cli", "kriya.workflow.workflow", "kriya.workflow.workflow_controller", "kriya.control.recovery",
                "kriya.control.run_coordinator", "kriya.tools.validate", "kriya.tools.containment_oci",
                "kriya.mcp.mcp", "kriya.production_doctor", "kriya.platform.services", "plugins.core_tools")


def _python_files() -> List[str]:
    files = []
    for top in ("kriya", "plugins"):
        for root, dirs, names in os.walk(REPO / top):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            files.extend(os.path.relpath(os.path.join(root, n), REPO).replace(os.sep, "/")
                         for n in names if n.endswith(".py"))
    return sorted(files)


def _offences(relpath: str) -> Set[str]:
    tree = ast.parse((REPO / relpath).read_text(encoding="utf-8"), filename=relpath)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {f"import {a.name}" for a in node.names if a.name.split(".")[0] in FORBIDDEN_MODULES}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            top = node.module.split(".")[0]
            if top in FORBIDDEN_MODULES:
                found.add(f"from {node.module} import")
            found |= {f"from {top} import {a.name}" for a in node.names
                      if a.name in FORBIDDEN_ATTRIBUTES.get(node.module, ())}
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            if node.attr in FORBIDDEN_ATTRIBUTES.get(node.value.id, ()):
                found.add(f"{node.value.id}.{node.attr}")
    return found


def _open_registry_items() -> Set[str]:
    with open(REPO / "handover" / "BACKLOG_REGISTRY.csv", newline="", encoding="utf-8") as handle:
        return {row["id"] for row in csv.DictReader(handle) if row["status"] != "CLOSED"}


def test_no_platform_mechanism_outside_the_platform_package_and_the_allowlist():
    violations = {path: sorted(offences) for path in _python_files()
                  if not path.startswith(PLATFORM_PACKAGE) and path not in TEMPORARY_ALLOWLIST
                  for offences in [_offences(path)] if offences}
    assert violations == {}, (
        "OS-specific mechanism belongs behind a kriya/platform port "
        f"(handover/PLATFORM_ARCHITECTURE.md): {violations}")


def test_the_allowlist_only_shrinks_and_names_open_mechanism_migrations():
    open_items = _open_registry_items()
    for path, (item, reason) in TEMPORARY_ALLOWLIST.items():
        assert not path.startswith(NEVER_ALLOWLISTED), f"{path} is a policy/orchestration layer"
        assert reason and item in open_items, f"{path}: {item} is not an open registry item"
        assert _offences(path), f"{path} no longer needs its allowlist entry; remove it"


def test_the_guard_detects_every_forbidden_form(tmp_path, monkeypatch):
    sample = tmp_path / "kriya" / "sample.py"
    sample.parent.mkdir()
    sample.write_text(
        "import fcntl\nimport resource as r\nfrom pwd import getpwuid\nfrom os import getuid\nimport os, sys, platform\n"
        "a = sys.platform\nb = os.name\nc = platform.system()\nd = os.killpg\ne = os.getgid()\n")
    monkeypatch.setattr(sys.modules[__name__], "REPO", tmp_path)
    assert _offences("kriya/sample.py") == {
        "import fcntl", "import resource", "from pwd import", "from os import getuid", "sys.platform", "os.name",
        "platform.system", "os.killpg", "os.getgid"}


def test_kriya_core_imports_without_posix_only_modules():
    blocked = "; ".join(f"sys.modules[{name!r}] = None" for name in BLOCKED_FOR_IMPORT)
    code = f"import sys; {blocked}\nimport importlib\nfor name in {CORE_MODULES!r}:\n    importlib.import_module(name)\n"
    result = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120,
                            env=dict(os.environ, PYTHONPATH=str(REPO)))
    assert result.returncode == 0, result.stderr[-4000:]
