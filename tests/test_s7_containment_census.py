"""S7 (packaged-default migration): the closed migration census, its tripwire, the shared mount-aware stand-in and the exact-id selection tooling.

Proves:
A. the registry (tests/containment_census.py) is well formed: valid seams/classes, a rule never marks a whole file H, every entry names a file and a test function that exist;
B. the legacy-host list S8 deletes is CLOSED (exactly these ids; none may be added without review);
C. the stand-in is test-only and impossible to select from production configuration, its container path equals the real backend's, and it claims nothing it does not prove
   (a host path outside the mounts stays visible through it - isolation is the real-OCI tier's job);
D. the stand-in translates container-visible paths from the ACTUAL profile (workspace, MountSpec, relative spelling, --flag=value) and runs the command;
E. the exact-id selection tooling: a parametrized id containing a line break survives the round trip, a requested id that is not collected is a usage error (never a silent
   omission);
F. the tripwire: a test that consults the canonical decision without a classification FAILS.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import _census_selection as cs
import containment_census as cc
import pytest
from _containment_standin import CONTAINER_WORKSPACE, NAME, MountAwareStandIn, path_mapping

from kriya.tools import containment, containment_oci
from kriya.tools.containment import (
    BackendUnavailableError,
    ContainmentProfile,
    MountSpec,
    TrustClass,
    resolve_containment_backend,
)
from kriya.tools.process import ProcessController

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent

# B. the closed list S8 deletes (owner decision 2026-10-25: replacement controls now, deletion in S8). Adding to it needs the same review as removing a test.
S8_DELETION_COUNT = 54


# ------------------------------------------------------------------ A. the registry is well formed

def test_the_registry_uses_only_the_closed_vocabularies_and_never_a_whole_file_h():
    for where, entries in (("FILE_RULES", cc.FILE_RULES.items()), ("EXCEPTIONS", cc.EXCEPTIONS.items())):
        for key, (seam, klass) in entries:
            assert seam in cc.SEAMS and klass in cc.CLASSES, (where, key, seam, klass)
    assert all(seam != "H" for seam, _ in cc.FILE_RULES.values()), "legacy-host tests are exact ids, never a file rule that would silently skip a future test"


def _functions(path: Path) -> set:
    return {node.name for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def test_every_registry_entry_names_a_file_and_a_test_function_that_exist():
    cache = {}
    stale = []
    for nodeid in list(cc.EXCEPTIONS) + [f"{f}::" for f in cc.FILE_RULES]:
        file_part, _, rest = nodeid.partition("::")
        path = ROOT / file_part
        if not path.is_file():
            stale.append(nodeid)
            continue
        if rest:
            names = cache.setdefault(file_part, _functions(path))
            if rest.split("::")[-1].split("[")[0] not in names:
                stale.append(nodeid)
    assert stale == [], f"stale census entries (the test or file is gone): {stale[:5]} ..."


def test_every_registry_exception_is_an_exactly_collected_node_id():
    """Exact-id staleness, not just 'the function exists': the registry once carried ids cut at ' (' and ids with xdist's '@docker' suffix, which matched nothing (so a
    census test silently lost its seam). Collect the suite in a subprocess and compare exactly - INCLUDING the markers the default run excludes (live_model, live_static_analysis,
    real_toolchain): their tests consult the owner too (a live tier run fires the same tripwire), so their ids must be exact as well."""
    script = ("import json, sys, pytest\nout = []\nclass P:\n    @pytest.hookimpl(trylast=True)\n    def pytest_collection_modifyitems(self, config, items):\n"
              "        out.extend(i.nodeid.rsplit('@', 1)[0] if i.get_closest_marker('xdist_group') is not None and '@' in i.nodeid else i.nodeid for i in items)\n"
              "pytest.main(['-p', 'no:cacheprovider', '--collect-only', '-q', '-m', 'live_model or live_static_analysis or real_toolchain or not (live_model or live_static_analysis or real_toolchain)', '--ignore=tests/_census_probe_unclassified.py', 'tests'], plugins=[P()])\n"
              "json.dump(out, open(sys.argv[1], 'w'))\n")
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "ids.json"
        done = subprocess.run([sys.executable, "-c", script, str(target)], cwd=ROOT, capture_output=True, text=True, timeout=600, check=False)
        assert target.is_file(), done.stderr[-500:]
        collected = set(json.loads(target.read_text(encoding="utf-8")))
    inexact = sorted(i for i in cc.EXCEPTIONS if i not in collected)
    assert inexact == [], f"census exceptions that are not exactly collected node ids ({len(inexact)}): {inexact[:3]}"


def test_an_unregistered_test_has_no_classification_and_a_registered_one_has_exactly_one():
    assert cc.classify("tests/not_a_registered_file.py::test_x") is None
    assert cc.classify("tests/test_workflow.py::test_workflow_successful_run") is not None
    assert not (set(cc.EXCEPTIONS) & {f"{f}::" for f in cc.FILE_RULES})     # an id is an exception OR covered by its file rule; resolution is exact-then-file


# ------------------------------------------------------------------ B. the legacy-host list is closed

def test_the_legacy_host_list_s8_deletes_is_closed_and_its_tests_are_skipped_not_run():
    ids = cc.legacy_host_ids()
    assert len(ids) == S8_DELETION_COUNT and len(set(ids)) == len(ids)
    assert all(cc.classify(i) == ("H", "H") for i in ids)
    conftest_source = (TESTS / "conftest.py").read_text(encoding="utf-8")
    assert 'entry[0] == "H"' in conftest_source and "deleted in S8" in conftest_source


# ------------------------------------------------------------------ C. the stand-in is test-only and claims nothing

def test_the_stand_in_cannot_be_selected_from_production():
    for path in sorted((ROOT / "kriya").rglob("*.py")) + sorted((ROOT / "plugins").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "containment-test-standin" not in text and "_containment_standin" not in text and "MountAwareStandIn" not in text, path
    with pytest.raises(BackendUnavailableError):
        resolve_containment_backend(NAME)
    assert set(containment._PRODUCTION_BACKEND_REGISTRY) == {"none", "oci"}                 # pylint: disable=protected-access
    # this file is seam N: nothing replaced the production factory for "oci"
    assert containment._PRODUCTION_BACKEND_REGISTRY["oci"] is containment._oci_backend_factory   # pylint: disable=protected-access


def test_the_stand_in_container_path_is_the_real_backends():
    assert CONTAINER_WORKSPACE == containment_oci._CONTAINER_WORKSPACE            # pylint: disable=protected-access


# ------------------------------------------------------------------ D. the stand-in translates the actual profile's paths, and proves nothing else

def _profile(workspace: Path, *mounts: MountSpec) -> ContainmentProfile:
    return ContainmentProfile(trust_class=TrustClass.UNTRUSTED_EXECUTION, workspace_path=str(workspace), additional_mounts=tuple(mounts))


def test_the_mapping_is_taken_from_the_profile_including_the_relative_spelling(tmp_path):
    venv = tmp_path / "origin" / "venv"
    profile = _profile(tmp_path / "view", MountSpec(host_path=str(venv), container_path=f"{CONTAINER_WORKSPACE}/.kriya/venv", writable=True))
    mapping = path_mapping(profile)
    assert mapping[CONTAINER_WORKSPACE] == str(tmp_path / "view")
    assert mapping[f"{CONTAINER_WORKSPACE}/.kriya/venv"] == str(venv) and mapping[".kriya/venv"] == str(venv)


def test_a_contained_command_runs_with_its_container_paths_translated_to_their_host_backing(tmp_path):
    view, origin = tmp_path / "view", tmp_path / "origin_venv"
    view.mkdir()
    origin.mkdir()
    (view / "data.txt").write_text("workspace-content")
    (origin / "marker.txt").write_text("mounted-content")
    profile = _profile(view, MountSpec(host_path=str(origin), container_path=f"{CONTAINER_WORKSPACE}/.kriya/venv", writable=True))
    # the translation applies to ARGV tokens (the command line the production construction produced), not to paths inside a program's own source
    program = "import sys\nfor token in sys.argv[1:]:\n    print(open(token).read() if not token.startswith('--') else token)"
    result = ProcessController().run([sys.executable, "-c", program, ".kriya/venv/marker.txt", f"{CONTAINER_WORKSPACE}/data.txt", f"--root={CONTAINER_WORKSPACE}/data.txt"],
                                     cwd=str(view), timeout=60, containment_profile=profile, containment_backend=MountAwareStandIn())
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["mounted-content", "workspace-content", f"--root={view}/data.txt"]


def test_the_stand_in_proves_no_isolation_so_isolation_claims_stay_with_real_oci(tmp_path):
    """NEGATIVE CONTROL: a host file outside every mount stays readable through the stand-in. A test that asserted filesystem isolation through it would be vacuous - those
    tests are real-OCI / containment-contract tests (seam N / the docker xdist group)."""
    view = tmp_path / "view"
    view.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("visible-to-the-stand-in")
    result = ProcessController().run([sys.executable, "-c", f"print(open({str(outside)!r}).read())"], cwd=str(view), timeout=60,
                                     containment_profile=_profile(view), containment_backend=MountAwareStandIn())
    assert result.returncode == 0 and "visible-to-the-stand-in" in result.stdout


# The contained flow installs pytest into the project environment (``<venv>/bin/python -m pip install -q pytest``); a suite must not need a network for that.

def _offline_run(view: Path, command, monkeypatch):
    monkeypatch.setenv("PIP_NO_INDEX", "1")              # a real pip would fail here: the only registry it could use is unreachable
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:9/simple")
    return ProcessController().run(command, cwd=str(view), timeout=120, containment_profile=_profile(view), containment_backend=MountAwareStandIn())


def test_the_pytest_install_of_the_contained_flow_is_satisfied_offline_and_the_project_environment_can_run_pytest(tmp_path, monkeypatch):
    view = tmp_path / "view"
    view.mkdir()
    assert _offline_run(view, [sys.executable, "-m", "venv", ".kriya/venv"], monkeypatch).returncode == 0
    venv_python = str(view / ".kriya" / "venv" / "bin" / "python")
    installed = _offline_run(view, [venv_python, "-m", "pip", "install", "-q", "pytest"], monkeypatch)
    assert installed.returncode == 0, installed.stderr
    ran = _offline_run(view, [venv_python, "-c", "import pytest, sys; print(pytest.__version__, [p for p in sys.path if 'kriya-standin-site' in p] != [])"], monkeypatch)
    assert ran.returncode == 0 and ran.stdout.split()[-1] == "True", (ran.stdout, ran.stderr)
    # only pytest's own closure is visible: nothing else of the test interpreter leaks into the project environment
    leaked = _offline_run(view, [venv_python, "-c", "import kriya"], monkeypatch)
    assert leaked.returncode != 0 and "ModuleNotFoundError" in leaked.stderr


def test_an_install_of_anything_else_fails_like_an_unreachable_registry_and_installs_nothing(tmp_path, monkeypatch):
    view = tmp_path / "view"
    view.mkdir()
    assert _offline_run(view, [sys.executable, "-m", "venv", ".kriya/venv"], monkeypatch).returncode == 0
    venv_python = str(view / ".kriya" / "venv" / "bin" / "python")
    for argv in (["pip", "install", "-q", "requests"], ["pip", "install", "-q", "-r", "requirements.txt"], ["pip", "install", "-q"]):
        refused = _offline_run(view, [venv_python, "-m", *argv], monkeypatch)
        assert refused.returncode == 1 and "no network" in refused.stderr, (argv, refused.stderr)
    assert not list((view / ".kriya" / "venv").rglob("kriya_standin_offline.pth"))


# ------------------------------------------------------------------ E. exact-id selection: no id escapes silently

NEWLINE_ID = "tests/test_x.py::test_y[First line\nsecond line-True]"


def test_a_parametrized_id_with_a_line_break_survives_the_round_trip_and_is_selected(tmp_path):
    path = tmp_path / "ids.json"
    cs.write_ids(str(path), [NEWLINE_ID, "tests/test_x.py::test_z"])
    assert sorted(cs.read_ids(str(path))) == sorted([NEWLINE_ID, "tests/test_x.py::test_z"])
    items = [SimpleNamespace(nodeid=NEWLINE_ID), SimpleNamespace(nodeid="tests/test_x.py::test_z"), SimpleNamespace(nodeid="tests/test_x.py::other")]
    kept, missing = cs.select(items, cs.read_ids(str(path)))
    assert [i.nodeid for i in kept] == [NEWLINE_ID, "tests/test_x.py::test_z"] and missing == []


def test_a_requested_id_that_is_not_collected_is_reported_never_dropped():
    kept, missing = cs.select([SimpleNamespace(nodeid="tests/a.py::t")], ["tests/a.py::t", "tests/a.py::gone"])
    assert [i.nodeid for i in kept] == ["tests/a.py::t"] and missing == ["tests/a.py::gone"]


def test_the_runner_refuses_a_census_id_file_naming_an_uncollected_test(tmp_path):
    ids = tmp_path / "ids.json"
    cs.write_ids(str(ids), ["tests/test_s7_containment_census.py::test_there_is_no_such_test"])
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--census-ids={ids}", "tests/test_s7_containment_census.py"],
                          cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
    assert done.returncode != 0 and "were not collected" in (done.stdout + done.stderr)


# ------------------------------------------------------------------ F. the tripwire: an unclassified consumer fails

def test_a_test_that_consults_the_owner_without_a_classification_fails():
    probe = "tests/_census_probe_unclassified.py"
    assert cc.classify(probe) is None
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", probe], cwd=ROOT, capture_output=True, text=True, timeout=300, check=False)
    output = done.stdout + done.stderr
    assert done.returncode != 0 and "not classified in tests/containment_census.py" in output, output[-600:]


def test_the_probe_file_is_not_collected_by_the_suite():
    config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "_census_probe" not in config and not (TESTS / "_census_probe_unclassified.py").name.startswith("test_")
    assert json.dumps(sorted(cc.SEAMS)) == json.dumps(["F2", "H", "N", "S"])



# ------------------------------------------------------------------ B3. the tiers the default run EXCLUDES consult the owner too

def test_every_file_in_a_default_excluded_tier_is_registered_in_the_census():
    """The consumer measurement ran the DEFAULT suite; a test under live_model / live_static_analysis / real_toolchain (all excluded by default) that reaches the owner fires the
    tripwire only when that tier is run - found late (2026-10-26: seven semgrep live ids and three real-toolchain ids). Registering the whole file is the rule, so a live run never meets
    an unclassified consumer; and every ``tests/test_live_*.py`` file is registered whatever its marker spelling."""
    import re

    marker = re.compile(r"mark\.(live_model|live_static_analysis|real_toolchain)\b")
    unregistered = sorted(f"tests/{path.name}" for path in TESTS.glob("test_*.py")
                          if (marker.search(path.read_text(encoding="utf-8")) or path.name.startswith("test_live_")) and f"tests/{path.name}" not in cc.FILE_RULES)
    assert unregistered == [], f"default-excluded tier files with no census rule: {unregistered}"
