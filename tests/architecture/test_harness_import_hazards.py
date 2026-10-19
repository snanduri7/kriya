"""Two order-dependent test hazards of the kernel harness (OPEN-ITEMS OI-34, E14 preflight), as tripwires.

1. A golden's scenario that imports another TEST module makes that test file part of the golden: editing the test changes
   the golden (MEASURED before the fix: changing PRODUCED_SOURCE in tests/test_lr_r1_p4_verification_only_retry_reproducer.py
   broke the ``enforce_verification_only_failed`` golden). The harness modules never import a ``test_*`` module.
2. The first-import patch hazard: a module imported for the FIRST time while a test patches one of its dependencies binds
   the patch object permanently through its module-level ``from X import N`` (MEASURED in E1 for workflow.py under a
   patched ``attempt.run_attempt``; the golden harness pre-imports the facades for it). Importing the three kernel facades
   loads every kernel owner module except the two the controller reaches through call-time accessors (E12, 21034e4):
   those two are the only kernel modules a test can import first, mid-run. MEASURED unreachable today: no test patches a
   name either binds at import. The tripwire pins both facts, so a new lazily imported kernel module, or a new patch of a
   name a lazy module binds, fails here instead of silently binding a mock for the rest of a session.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Set, Tuple
from unittest.mock import patch

from tests.architecture import patch_sites as ps
from tests.architecture.test_patch_points import KERNEL_OWNER_MODULES

TESTS = Path(__file__).resolve().parents[1]
REPO = TESTS.parent
HARNESS_MODULES = ("tests/state_machine/_kernel_golden.py", "tests/state_machine/_scripted_run.py",
                   "tests/_enforce_unit_harness.py", "tests/_milestone_proof_harness.py", "tests/_lr_r1_p4_run.py")
# MEASURED (E14 preflight at 2e2ffaa): not loaded by `import` of the three facades; first imported at the first
# enforce / shadow run (kriya/workflow/workflow_controller.py `_enforce_loop()` / `_shadow_mode()`).
LAZY_KERNEL_MODULES = frozenset({"kriya.workflow.enforce_loop", "kriya.workflow.shadow_mode"})


def imported_test_modules(source: str) -> List[str]:
    """Every ``test_*`` module a source imports, at any depth."""
    found = []
    for node in ast.walk(ast.parse(source)):
        names = ([alias.name for alias in node.names] if isinstance(node, ast.Import)
                 else [node.module or ""] if isinstance(node, ast.ImportFrom) else [])
        found.extend(name for name in names if name.split(".")[-1].startswith("test_"))
    return found


def test_no_harness_module_imports_a_test_module():
    offenders = {path: imported_test_modules((REPO / path).read_text(encoding="utf-8")) for path in HARNESS_MODULES}
    assert {path: names for path, names in offenders.items() if names} == {}
    planted = "def scenario():\n    from test_lr_r1_p4_verification_only_retry_reproducer import _enforce\n"
    assert imported_test_modules(planted) == ["test_lr_r1_p4_verification_only_retry_reproducer"]


def lazily_imported_kernel_modules() -> Set[str]:
    """The kernel owner modules a fresh interpreter has NOT loaded after importing the three kernel facades."""
    probe = ("import json, sys\n"
             "import kriya.workflow.attempt, kriya.workflow.workflow, kriya.workflow.workflow_controller\n"
             f"print(json.dumps(sorted(m for m in {list(KERNEL_OWNER_MODULES)!r} if m not in sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", probe], cwd=str(REPO), capture_output=True, text=True, check=True)
    return set(json.loads(out.stdout))


def test_the_lazily_imported_kernel_modules_are_exactly_the_known_ones():
    assert lazily_imported_kernel_modules() == LAZY_KERNEL_MODULES


def import_bindings(source: str) -> Set[Tuple[str, str]]:
    """(module, name) of every module-level ``from kriya... import name`` (what the module binds at first import)."""
    return {(node.module, alias.name) for node in ast.parse(source).body
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("kriya") and node.level == 0
            for alias in node.names}


def patches_of(bindings: Iterable[Tuple[str, str]], sources: Mapping[str, str]) -> List[str]:
    """Every test patch (any form patch_sites recognizes) of a bound (module, name)."""
    bindings = set(bindings)
    modules = tuple(sorted({module for module, _name in bindings}))
    with patch.object(ps, "KERNEL_MODULES", modules):
        sites = ps.patch_sites(sources)
    return sorted(f"{site.file}:{site.line} {site.module}.{site.target}" for site in sites
                  if (site.module, site.target.split(".")[0]) in bindings)


def _lazy_bindings() -> Dict[str, Set[Tuple[str, str]]]:
    return {module: import_bindings((REPO / (module.replace(".", "/") + ".py")).read_text(encoding="utf-8"))
            for module in sorted(LAZY_KERNEL_MODULES)}


def test_no_test_patches_a_name_a_lazily_imported_kernel_module_binds_at_import():
    bindings = _lazy_bindings()
    assert all(bindings.values())   # the scan sees each lazy module's own imports (never vacuous)
    every = set().union(*bindings.values())
    assert patches_of(every, ps.test_sources()) == []
    # the detector is not vacuous: a planted patch of one bound name, in each recognized form, is found
    module, name = sorted(every)[0]
    planted = {"tests/planted.py": (f"from unittest.mock import patch\nimport {module} as m\n"
                                    f"def test_x(monkeypatch):\n    with patch('{module}.{name}'): pass\n"
                                    f"    with patch.object(m, '{name}'): pass\n"
                                    f"    monkeypatch.setattr('{module}.{name}', None)\n")}
    assert len(patches_of(every, planted)) == 3
