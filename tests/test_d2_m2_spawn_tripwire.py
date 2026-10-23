"""D2 M2 slice 5: the structural tripwire behind the private review channel.

The channel's non-inheritance rests on how Kriya creates processes: every child comes from ``subprocess`` / asyncio's subprocess with the DEFAULT descriptor handling (``close_fds=True``), nothing
passes ``pass_fds``, and no code forks, execs, spawns, or shares an address space with a child by another route. This test scans all production code (``kriya/``, ``plugins/``) and fails when any
of that changes, with an EMPTY allowlist: a legitimate future spawn that needs inherited descriptors must add itself here and prove, in tests/test_d2_m2_fd_inheritance.py, that the review socket is
excluded (owner 2026-10-23). The scan is itself pinned by planting every forbidden construct in a scratch tree and requiring each to be found."""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
SCANNED = ("kriya", "plugins")
ALLOWLIST: Dict[str, Set[str]] = {}  # file -> constructs it may use; EMPTY on purpose

FORBIDDEN_CALLS = {
    "os.fork", "os.forkpty", "os.system", "os.popen", "os.posix_spawn", "os.posix_spawnp",
    "os.execv", "os.execve", "os.execvp", "os.execvpe", "os.execl", "os.execle", "os.execlp", "os.execlpe",
    "os.spawnv", "os.spawnve", "os.spawnvp", "os.spawnvpe", "os.spawnl", "os.spawnle", "os.spawnlp", "os.spawnlpe",
    "pty.fork", "pty.spawn", "subprocess.getoutput", "subprocess.getstatusoutput",
}
FORBIDDEN_IMPORTS = {"multiprocessing", "pty", "ctypes", "_posixsubprocess", "concurrent.futures.process"}
FORBIDDEN_NAMES = {"ProcessPoolExecutor", "posix_spawn", "posix_spawnp", "fork_exec"}
FORBIDDEN_KEYWORDS = {"pass_fds"}


def _dotted(node: ast.AST) -> str:
    parts: List[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def findings_in(source: str) -> List[str]:
    found: List[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found += [f"import {a.name}" for a in node.names if a.name.split(".")[0] in FORBIDDEN_IMPORTS or a.name in FORBIDDEN_IMPORTS]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module.split(".")[0] in FORBIDDEN_IMPORTS or module in FORBIDDEN_IMPORTS:
                found.append(f"from {module} import ...")
            found += [f"from {module} import {a.name}" for a in node.names if a.name in FORBIDDEN_NAMES]
        elif isinstance(node, ast.Call):
            name = _dotted(node.func)
            if name in FORBIDDEN_CALLS or name.split(".")[-1] in FORBIDDEN_NAMES:
                found.append(f"call {name}")
            for keyword in node.keywords:
                if keyword.arg in FORBIDDEN_KEYWORDS:
                    found.append(f"keyword {keyword.arg}= in {name or 'a call'}")
                if keyword.arg == "close_fds" and not (isinstance(keyword.value, ast.Constant) and keyword.value.value is True):
                    found.append(f"close_fds is not literally True in {name or 'a call'}")
            # a descriptor made inheritable again: os.set_inheritable(fd, True) / os.dup2(..., inheritable=True is the default, so only explicit set_inheritable is flagged)
            if name in ("os.set_inheritable", "set_inheritable") and not (len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) and node.args[1].value is False):
                found.append("os.set_inheritable(fd, <not literally False>)")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"name {node.id}")
    return found


def scan(root: Path) -> Dict[str, List[str]]:
    result: Dict[str, List[str]] = {}
    for base in SCANNED:
        for path in sorted((root / base).rglob("*.py")):
            findings = findings_in(path.read_text(encoding="utf-8"))
            allowed = ALLOWLIST.get(path.relative_to(root).as_posix(), set())
            findings = [f for f in findings if f not in allowed]
            if findings:
                result[path.relative_to(root).as_posix()] = sorted(set(findings))
    return result


def test_production_code_creates_processes_only_through_subprocess_with_default_descriptor_handling():
    assert scan(ROOT) == {}, ("a construct that could hand an inherited descriptor (the private review socket) to a child; "
                              "add it to ALLOWLIST ONLY with a proof in tests/test_d2_m2_fd_inheritance.py that the review socket is excluded")


def test_the_allowlist_is_empty_today_so_every_entry_is_a_deliberate_reviewed_edit():
    assert ALLOWLIST == {}


def test_the_scan_is_not_vacuous_it_finds_every_forbidden_construct_in_a_planted_tree(tmp_path):
    planted = {
        "os.fork": "import os\npid = os.fork()\n",
        "os.system": "import os\nos.system('true')\n",
        "os.posix_spawn": "import os\nos.posix_spawn('/bin/true', ['true'], {})\n",
        "os.execv": "import os\nos.execv('/bin/true', ['true'])\n",
        "os.spawnl": "import os\nos.spawnl(os.P_WAIT, '/bin/true', 'true')\n",
        "os.popen": "import os\nos.popen('true')\n",
        "pty.spawn": "import pty\npty.spawn(['true'])\n",
        "import multiprocessing": "import multiprocessing\n",
        "from multiprocessing": "from multiprocessing import Process\n",
        "import ctypes": "import ctypes\n",
        "ProcessPoolExecutor": "from concurrent.futures import ProcessPoolExecutor\nProcessPoolExecutor()\n",
        "pass_fds": "import subprocess\nsubprocess.run(['true'], pass_fds=(3,))\n",
        "pass_fds via Popen": "import subprocess\nsubprocess.Popen(['true'], pass_fds=[5])\n",
        "close_fds=False": "import subprocess\nsubprocess.run(['true'], close_fds=False)\n",
        "close_fds=variable": "import subprocess\nflag = True\nsubprocess.run(['true'], close_fds=flag)\n",
        "set_inheritable True": "import os\nos.set_inheritable(3, True)\n",
        "set_inheritable variable": "import os\nflag = False\nos.set_inheritable(3, flag)\n",
        "asyncio pass_fds": "import asyncio\nasyncio.create_subprocess_exec('true', pass_fds=(3,))\n",
        "getoutput": "import subprocess\nsubprocess.getoutput('true')\n",
    }
    for label, source in planted.items():
        assert findings_in(source), f"the tripwire did not notice: {label}"
    tree = tmp_path / "kriya"
    tree.mkdir()
    (tree / "bad.py").write_text(planted["pass_fds"])
    (tmp_path / "plugins").mkdir()
    assert scan(tmp_path) == {"kriya/bad.py": ["keyword pass_fds= in subprocess.run"]}


def test_the_ordinary_ways_kriya_spawns_are_not_flagged():
    fine = ("import subprocess, asyncio, os\n"
            "subprocess.run(['git', 'status'], capture_output=True)\n"
            "subprocess.Popen(['x'], stdout=subprocess.PIPE, start_new_session=True, preexec_fn=lambda: None)\n"
            "subprocess.run(['x'], close_fds=True)\n"
            "asyncio.create_subprocess_exec('x', stdout=asyncio.subprocess.PIPE)\n"
            "os.set_inheritable(3, False)\n")
    assert findings_in(fine) == []
