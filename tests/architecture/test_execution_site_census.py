"""Every external-process launch in production code is classified (D2 EXEC-LAYER rebuild, slice S1 census tripwire).

``execution_site_inventory.INVENTORY`` answers nine questions per launching function; this test makes an UNCLASSIFIED launch impossible to
add silently, and pins the evidence the exemptions rest on so it cannot rot:

- the scanner finds every launch shape (planted in scratch sources, including the receiver-agnostic ProcessController form a first draft missed);
- the scanned set of non-git launching functions equals the inventory exactly (a new site fails; a vanished site fails until deleted);
- git launches (through ``git_argv``) match the closed per-file count;
- every entry is completely classified, an EXEMPT entry carries evidence, and a WORKLOAD names the slice that owns its next change;
- the executable facts behind three exemptions are tests, not prose;
- every ``ContainmentProfile(...)`` construction is classified, and a profile that mounts a writable workspace belongs to a function that creates the
  authority namespace on the host first (``ensure_control_directory``): the omission that left ShellTool unprotected in the first attempt.
"""
from __future__ import annotations

import ast
import re
from typing import Dict, List, Optional, Tuple

import pytest

from . import execution_site_inventory as inv
from .execution_sites import REPO, ROOTS, launches, production_launches

NON_GIT = {(path, function): [launch for launch in found if not launch.git]
           for path, functions in production_launches().items() for function, found in functions.items()}
NON_GIT = {key: found for key, found in NON_GIT.items() if found}


# --- the scanner is pinned by planting every launch shape ---------------------------------------------------------------------------

_PLANTED = {
    "subprocess_attr": "import subprocess\ndef f():\n    subprocess.run(['x'])\n",
    "subprocess_from_import": "from subprocess import Popen\ndef f():\n    Popen(['x'])\n",
    "asyncio_exec": "import asyncio\nasync def f():\n    await asyncio.create_subprocess_exec('x')\n",
    "asyncio_shell": "import asyncio\nasync def f():\n    await asyncio.create_subprocess_shell('x')\n",
    "os_system": "import os\ndef f():\n    os.system('x')\n",
    "os_spawn": "import os\ndef f():\n    os.spawnv(0, 'x', ['x'])\n",
    "pty": "import pty\ndef f():\n    pty.spawn(['x'])\n",
    "multiprocessing": "import multiprocessing\ndef f():\n    multiprocessing.Process(target=print)\n",
    "helper_name": "def f():\n    _spawn_popen(['x'])\n",
    "helper_attr": "def f(p):\n    p.spawn_subprocess_exec_fail_closed('x')\n",
    "controller_variable_receiver": "from kriya.tools.process import ProcessController\ndef f(c):\n    c.run(['x'], cwd='.', timeout=1)\n",
    "controller_self_attribute": "from kriya.tools.process import ProcessController\nclass A:\n    def f(self):\n        self._process.run(['x'], cwd='.', timeout=1)\n",
    "controller_call_result": "from kriya.tools.process import ProcessController\ndef f():\n    ProcessController().run_async(['x'], cwd='.', timeout=1)\n",
    "controller_module_import": "import kriya.tools.process\ndef f(c):\n    c.start_managed(['x'], cwd='.')\n",
    "controller_from_package": "from kriya.tools import process\ndef f(c):\n    c.run(['x'], cwd='.', timeout=1)\n",
}


@pytest.mark.parametrize("name", sorted(_PLANTED))
def test_the_scanner_finds_every_launch_shape(name):
    found = launches(_PLANTED[name])
    assert list(found) and all(found.values()), f"launch shape {name!r} was not found"


def test_a_git_argv_launch_is_flagged_as_git_and_other_shapes_are_not():
    git = launches("import subprocess\nfrom kriya.platform import git_argv\ndef f():\n    subprocess.run(git_argv(['git', 'status']))\n")
    assert [launch.git for launch in git["f"]] == [True]
    plain = launches("import subprocess\ndef f(argv):\n    subprocess.run(argv)\n")
    assert [launch.git for launch in plain["f"]] == [False]


def test_calls_that_are_not_launches_are_not_found():
    assert not launches("def f(reviewer):\n    reviewer.run('goal')\n")  # no cwd keyword
    assert not launches("def f(c):\n    c.run(['x'], cwd='.', timeout=1)\n")  # the module cannot reach ProcessController
    assert not launches("import shutil\ndef f():\n    shutil.which('git')\n")
    assert not launches("def f():\n    '''subprocess.run(x)'''\n    # os.system(y)\n")  # prose is not code


# --- the census ---------------------------------------------------------------------------------------------------------------------

def test_every_launching_function_is_classified_and_no_entry_is_stale():
    scanned = set(NON_GIT)
    classified = set(inv.INVENTORY)
    unclassified = sorted(scanned - classified)
    stale = sorted(classified - scanned)
    assert not unclassified, "UNCLASSIFIED launching functions (classify them in execution_site_inventory.INVENTORY): " + "; ".join(
        f"{path}::{function} {[launch.kind for launch in NON_GIT[(path, function)]]}" for path, function in unclassified)
    assert not stale, f"inventory entries whose launching function no longer exists (delete them): {stale}"


def test_git_launches_match_the_closed_per_file_count():
    counted: Dict[str, int] = {}
    for path, functions in production_launches().items():
        n = sum(1 for found in functions.values() for launch in found if launch.git)
        if n:
            counted[path] = n
    assert counted == inv.GIT_LAUNCHES, (
        f"git launches changed: new/more {sorted(k for k in counted if counted[k] > inv.GIT_LAUNCHES.get(k, 0))}, "
        f"fewer/removed {sorted(k for k in inv.GIT_LAUNCHES if counted.get(k, 0) < inv.GIT_LAUNCHES[k])}")


def test_every_entry_is_completely_classified():
    for key, site in inv.INVENTORY.items():
        assert site.cls in inv.CLASSES, key
        assert all(isinstance(value, str) and value.strip() for value in (
            site.executable, site.project_code, site.cwd, site.authority_today, site.owner_today, site.trust, site.evidence)), key
        assert site.slices and all(s in inv.SLICES for s in site.slices), key
        if site.slices == ("EXEMPT",):
            assert len(site.evidence) > 20, f"{key}: an exemption needs traced or measured evidence"
        if site.cls == inv.WORKLOAD:
            assert site.slices[0] != "EXEMPT", f"{key}: project code is never exempt"
            assert site.project_code in ("yes", "config") or site.project_code.startswith("no"), key


# --- the executable facts behind the exemptions -------------------------------------------------------------------------------------

def _function(path: str, qualname: str) -> Optional[ast.AST]:
    tree = ast.parse((REPO / path).read_text(encoding="utf-8"))
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def name_of(node: ast.AST) -> str:
        names: List[str] = []
        current = node
        while True:
            if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(current.name)
            if current not in parents:
                break
            current = parents[current]
        return ".".join(reversed(names))

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and name_of(node) == qualname:
            return node
    return None


def test_docker_run_helpers_mount_no_workspace():
    """``_observed_version``, ``_ensure_image`` and ``_check_net_admin_available`` run a fixed command in a Kriya-selected image: none of them
    may mount anything (a mount would make them workload launches)."""
    for name in ("_observed_version", "_ensure_image", "_check_net_admin_available"):
        node = _function("kriya/tools/containment_oci.py", name)
        assert node is not None, name
        source = ast.unparse(node)
        for forbidden in ("bind_mount_args", '"-v"', "'-v'", '"--mount"', "'--mount'", "--volume", "workspace"):
            assert forbidden not in source, f"{name} mentions {forbidden!r}: it would no longer be a mount-free fixed command"


def test_runnable_check_never_runs_a_jvm_build_tool():
    """``_first_runnable`` executes ``<tool> --version`` for the tools in ``_RUNNABLE_CHECK`` from the inherited cwd. ``mvn -version`` there
    honors a project's ``.mvn/jvm.config`` (MEASURED 2026-10-24), so no JVM build tool may ever be in that tuple."""
    from kriya.platform import toolchain_locator

    assert set(toolchain_locator._RUNNABLE_CHECK) <= {"python3", "git", "node"}, toolchain_locator._RUNNABLE_CHECK  # pylint: disable=protected-access


def test_dependency_execution_has_no_production_caller():
    """The four launchers in ``dependency_execution`` are classified S2 only because nothing calls them. If a caller appears this fails and the
    caller must be classified (and, for a writable workspace, must create the authority namespace first)."""
    names = ("maven_acquire_dependencies", "maven_execute_offline", "python_acquire_dependencies", "python_execute_offline",
             "_acquisition_profile", "_execution_profile")
    pattern = re.compile(r"\b(" + "|".join(names) + r")\b")
    offenders = []
    for root in ROOTS:
        for path in (REPO / root).rglob("*.py"):
            if path.relative_to(REPO).as_posix() == "kriya/tools/dependency_execution.py":
                continue
            if pattern.search(path.read_text(encoding="utf-8")):
                offenders.append(path.relative_to(REPO).as_posix())
    assert not offenders, f"dependency_execution is now referenced by {offenders}: classify those callers"


# --- the profile builders and the S1 obligation --------------------------------------------------------------------------------------

def _profile_builders() -> Dict[Tuple[str, str], int]:
    found: Dict[Tuple[str, str], int] = {}
    for root in ROOTS:
        for path in sorted((REPO / root).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if "ContainmentProfile(" not in source:
                continue
            tree = ast.parse(source)
            parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                        (isinstance(node.func, ast.Name) and node.func.id == "ContainmentProfile")
                        or (isinstance(node.func, ast.Attribute) and node.func.attr == "ContainmentProfile")):
                    names, current = [], node
                    while current in parents:
                        current = parents[current]
                        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            names.append(current.name)
                    found[(path.relative_to(REPO).as_posix(), ".".join(reversed(names)) or "<module>")] = node.lineno
    return found


def test_every_containment_profile_construction_is_classified():
    scanned = set(_profile_builders())
    classified = set(inv.PROFILE_BUILDERS)
    assert scanned == classified, f"unclassified: {sorted(scanned - classified)}; stale: {sorted(classified - scanned)}"


def _calls(node: ast.AST, name: str) -> bool:
    return any(isinstance(call, ast.Call) and (
        (isinstance(call.func, ast.Name) and call.func.id == name)
        or (isinstance(call.func, ast.Attribute) and call.func.attr == name)) for call in ast.walk(node))


def test_every_oci_workspace_bind_creates_the_authority_namespace_first():
    """S1: the OCI backend refuses to leave the authority namespace to the workload, and it does so in ONE place per workspace bind, so no consumer
    (ShellTool, the validator, MCP, the doctor's probe, a future caller) can be missed: every function in ``containment_oci`` that binds the
    workspace (``bind_mount_args(<host>, _CONTAINER_WORKSPACE, ...)``) also calls ``ensure_authority_namespace``."""
    tree = ast.parse((REPO / "kriya/tools/containment_oci.py").read_text(encoding="utf-8"))
    binders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # direct statements of this function only (nested functions are their own scope)
        own = [call for call in ast.walk(node) if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "bind_mount_args"
               and len(call.args) >= 2 and isinstance(call.args[1], ast.Name) and call.args[1].id == "_CONTAINER_WORKSPACE"]
        if own:
            binders.append(node)
    names = sorted(node.name for node in binders)
    assert names == ["_prepare_registry_scoped", "prepare"], f"workspace-binding functions changed: {names}"
    for node in binders:
        assert _calls(node, "ensure_authority_namespace"), f"{node.name} binds the workspace without ensure_authority_namespace"
        assert _calls(node, "authority_state_mask_args"), f"{node.name} binds the workspace without the anchor and leaf masks"
