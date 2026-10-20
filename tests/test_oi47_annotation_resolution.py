"""OI-47 (QX-13, TIP-CLOSE A): the PRD-001 contract - every annotation of a class, method or function defined in kriya/
resolves at runtime (tests/test_bootstrap_contract.py::test_every_production_annotation_resolves_at_runtime) - holds for
the three owners that named TYPE_CHECKING-only types.

MEASURED on the unfixed bytes (03954d8): typing.get_type_hints raised NameError for 45 callables - every port method and
KriyaClient.context in kriya.client.contract (RequestContext first; AppConfig and the 13 application value types behind
it), kriya.application.evidence.EvidenceService.manifest (Snapshot), kriya.application.compose (ApplicationServices.__init__,
compose: ConfigService). TRACED: no guard was there for an import cycle (each guarded import was already a top-level edge
of tests/architecture/module_map.py, and none of the modules is in an import cycle); compose and evidence guarded for
import cost, the contract to keep its import free of the application's service modules.

The fix keeps that: RequestContext, AppConfig, Snapshot and ConfigService are imported (the same statements, so the
import graph is unchanged); the contract names the application's value types through the PEP 562 package alias
``_application``, which imports a service module only when an annotation is evaluated. Each check runs in a fresh
interpreter, so no module another test imported decides the result.
"""
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OWNERS = ("kriya.client.contract", "kriya.application.evidence", "kriya.application.compose")

# Every function and method each owner defines (the bootstrap scan's selection), with its resolved hints spelled by the
# owner of each type, so a hint that resolves to the wrong object fails too.
_RESOLVE = """
import importlib, inspect, sys, typing
resolved = 0
for name in {owners!r}:
    module = importlib.import_module(name)
    for attribute, obj in vars(module).items():
        if getattr(obj, '__module__', None) != name:
            continue
        targets = [obj] if inspect.isfunction(obj) else []
        if inspect.isclass(obj):
            targets += [v for v in vars(obj).values() if inspect.isfunction(v) and v.__module__ == name]
        for target in targets:
            typing.get_type_hints(target)
            resolved += 1
print(resolved)
"""


def _python(code, *, imports=()):
    script = "".join(f"import {name}\n" for name in imports) + code
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_every_function_of_the_three_owners_resolves_its_annotations():
    resolved = int(_python(_RESOLVE.format(owners=OWNERS)))
    assert resolved >= 45, "the 45 callables that failed at 03954d8, at least"


def test_the_hints_resolve_to_the_owners_types():
    _python("""
import typing
from typing import Optional
from kriya.application.compose import ApplicationServices, compose
from kriya.application.evidence import EvidenceService
from kriya.application.history import HistoryLocation, SnapshotAnswer
from kriya.application.run import RunArtifacts, RunHandle, RunServiceDeps, ToolVisibility
from kriya.client import contract
from kriya.config import AppConfig
from kriya.config.service import ConfigService
from kriya.context.model import RequestContext
from kriya.control.history import Snapshot

def hints(target):
    return typing.get_type_hints(target)

assert hints(contract.KriyaClient.context)['return'] is RequestContext
preflight = hints(contract.RunsPort.preflight)
assert preflight['ctx'] is RequestContext and preflight['cfg'] is AppConfig and preflight['tool_visibility'] is ToolVisibility
start = hints(contract.RunsPort.start)
assert (start['artifacts'], start['deps'], start['return']) == (RunArtifacts, RunServiceDeps, RunHandle)
assert hints(contract.EvidencePort.manifest)['snapshot'] == Optional[Snapshot]
assert hints(contract.HistoryPort.location)['return'] is HistoryLocation
assert hints(contract.HistoryPort.acquire)['return'] is SnapshotAnswer
assert hints(contract.HistoryPort.list)['snapshot'] is Snapshot
assert hints(EvidenceService.manifest)['snapshot'] == Optional[Snapshot]
assert hints(ApplicationServices.__init__)['config'] == Optional[ConfigService]
assert hints(compose)['config'] == Optional[ConfigService] and hints(compose)['return'] is ApplicationServices
""")


def test_importing_the_contract_or_the_composition_imports_no_application_service():
    """Preservation (passes at 03954d8 too): the contract's runtime-resolvable names must not cost the application's
    service modules at import - only evaluating an annotation imports the module of its type."""
    out = _python("""
import sys
import kriya.client.contract, kriya.application.compose
print(sorted(m for m in sys.modules if m.startswith('kriya.application.') and m != 'kriya.application.compose'))
""")
    assert out.strip() == "[]", out


PAIRS = [("kriya.client.contract", "kriya.application"), ("kriya.client.contract", "kriya.client.local"),
         ("kriya.application.evidence", "kriya.control.history"), ("kriya.application.compose", "kriya.config.service"),
         ("kriya.application.compose", "kriya.application.evidence")]


@pytest.mark.parametrize("pair", [order for pair in PAIRS for order in itertools.permutations(pair)],
                         ids=lambda order: " then ".join(order))
def test_the_owners_load_and_resolve_whichever_side_loads_first(pair):
    """Preservation (cycle safety): each owner and the module it now imports at runtime, in both orders, in a fresh
    interpreter; then every annotation of the three owners resolves."""
    assert int(_python(_RESOLVE.format(owners=OWNERS), imports=pair)) >= 45
