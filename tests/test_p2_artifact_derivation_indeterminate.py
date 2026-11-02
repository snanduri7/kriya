"""ARTIFACT-DERIVATION-INDETERMINATE-MANIFEST-001 (Wave 7, PRD-032 task 1).

MEASURED before the fix (handover/evidence/PRD-032-W7/p2_prefix_real_engine*.json, real engine, only the model scripted): a
package.json that is PRESENT but not valid JSON let both milestones complete with an EMPTY artifact registry (and a
milestone that committed such a manifest completed with zero records for itself while its earlier record stayed), because
the derivers were fail-soft for every kind of "no record". "Artifact not applicable" (no manifest) and "artifact expected
but derivation indeterminate" (manifest present, underivable) were indistinguishable.

The contract pinned here:

* NOT APPLICABLE stays an honest empty derivation: the marker file is absent, or it parses and carries no artifact identity
  (a pyproject.toml without ``[project]``, a package.json without ``name``). A milestone completes normally.
* INDETERMINATE is the typed ``ArtifactDerivationIndeterminate`` (reason ``ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE``):
  a present manifest that is unreadable, not parseable, the wrong shape, or lacks the identity its own format requires.
  ``ArtifactRegistry.derive_from_workspace`` raises it, so the enforce terminal, the milestone pre-commit gate and settlement
  (all of which derive through it) stop; the milestone never commits from it and its dependents never run.
* ``ArtifactRegistry.validate`` stays tolerant: a recorded artifact whose manifest later became underivable is still reported
  as ``ARTIFACT_DRIFT`` (never a raise, never "no drift").
* A manifest that PARSES but carries no identity is NOT APPLICABLE (a well-formed pom without ``artifactId``, a skeleton
  ``<project/>`` marker used throughout the suite, a package.json without ``name``) - UNLESS an artifact is EXPECTED: the registry
  already records it (an earlier derivation of the same ecosystem and module) and the descriptor is still present but no longer
  derives it. That is indeterminate too (PRD-032 final review: measured silent completion when M2 committed a pom without
  ``artifactId`` after M1 recorded the Maven artifact). A milestone's ``provides`` is a LOGICAL capability, so the recorded registry
  entry is the only existing authoritative "artifact expected" signal. Strictening beyond that is a separate owner choice.
* Python/Maven from-start manifests were already stopped by the containment toolchain resolver (CONTAINMENT_SETUP_FAILED),
  a different owner that runs first under these tests' configuration; those branches of the new code are covered at the
  derivation level here, not through the real engine (said so, not hidden).
"""
import json
import os
from pathlib import Path
from typing import Any, List

import pytest
from test_gate_f_milestone_artifact_registry import (
    CALC,
    INTEGRATION,
    PYPROJECT,
    WITH_DIV,
    WITH_MUL,
    Run,
    _committed_transactions,
    _registry,
)
from test_prd030_terminal_services import _request, _run, _validators

from kriya.control.artifacts import (
    ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE,
    ArtifactDerivationIndeterminate,
    ArtifactRegistry,
    derive_maven_artifact,
    derive_npm_artifact,
    derive_python_artifact,
)
from kriya.workflow import development_loop as development_loop_module
from kriya.workflow.stage_apply import QualityGateFailure

GOOD_JSON = '{"name": "myapp", "version": "0.1.0"}\n'
BAD_JSON = '{"name": "myapp", "version": '
GOOD_POM = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><modelVersion>4.0.0</modelVersion>"
            "<groupId>g</groupId><artifactId>myapp</artifactId><version>1</version></project>\n")


def _write(root: Path, name: str, text: str) -> None:
    (root / name).write_text(text)


# --- derivation level: the semantics line, per ecosystem ---------------------------------------------------------------

NOT_APPLICABLE = [
    pytest.param({}, id="no-manifest-at-all"),
    pytest.param({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}, id="pyproject-without-project-table"),
    pytest.param({"package.json": '{"private": true}\n'}, id="package-json-without-name"),
    pytest.param({"pom.xml": "<project xmlns='http://maven.apache.org/POM/4.0.0'><groupId>g</groupId></project>"},
                 id="well-formed-pom-without-artifactid"),
    pytest.param({"pom.xml": "<project></project>"}, id="skeleton-pom-marker"),
    pytest.param({"pyproject.toml": "[project]\n"}, id="skeleton-pyproject-marker"),
    pytest.param({"pyproject.toml": '[project]\nversion = "1"\n'}, id="pyproject-project-without-name"),
]

INDETERMINATE = [
    pytest.param({"package.json": BAD_JSON}, "package.json", id="npm-not-json"),
    pytest.param({"package.json": "[]\n"}, "package.json", id="npm-not-an-object"),
    pytest.param({"pyproject.toml": '[project\nname = "x"\n'}, "pyproject.toml", id="python-not-toml"),
    pytest.param({"pyproject.toml": 'project = "x"\n'}, "pyproject.toml", id="python-project-not-a-table"),
    pytest.param({"pom.xml": "<project><artifactId>x</artifactId>"}, "pom.xml", id="maven-not-xml"),
]


@pytest.mark.parametrize("files", NOT_APPLICABLE)
def test_not_applicable_stays_an_honest_empty_derivation(tmp_path, files):
    for name, text in files.items():
        _write(tmp_path, name, text)
    assert ArtifactRegistry().derive_from_workspace(str(tmp_path), "M1") == ()


@pytest.mark.parametrize("files, named", INDETERMINATE)
def test_a_present_manifest_that_cannot_be_derived_is_typed_indeterminate_never_empty(tmp_path, files, named):
    for name, text in files.items():
        _write(tmp_path, name, text)
    with pytest.raises(ArtifactDerivationIndeterminate) as caught:
        ArtifactRegistry().derive_from_workspace(str(tmp_path), "M1")
    assert caught.value.reason_code == ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE
    assert str(caught.value).startswith(ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE)
    assert len(caught.value.problems) == 1 and named in caught.value.problems[0]


def test_a_declared_maven_module_with_an_unparseable_pom_is_indeterminate(tmp_path):
    _write(tmp_path, "pom.xml", "<project xmlns='http://maven.apache.org/POM/4.0.0'><artifactId>root</artifactId>"
                                "<modules><module>core</module></modules></project>")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "pom.xml").write_text("<project><not-closed>")
    with pytest.raises(ArtifactDerivationIndeterminate) as caught:
        ArtifactRegistry().derive_from_workspace(str(tmp_path), "M1")
    assert [problem.split(":")[0] for problem in caught.value.problems] == [os.path.join("core", "pom.xml")]


def test_every_indeterminate_manifest_is_named_not_just_the_first(tmp_path):
    _write(tmp_path, "package.json", BAD_JSON)
    _write(tmp_path, "pyproject.toml", "[project\n")
    with pytest.raises(ArtifactDerivationIndeterminate) as caught:
        ArtifactRegistry().derive_from_workspace(str(tmp_path), "M1")
    assert sorted(problem.split(":")[0] for problem in caught.value.problems) == ["package.json", "pyproject.toml"]


def test_valid_derivation_is_unchanged_for_every_ecosystem(tmp_path):
    _write(tmp_path, "pom.xml", GOOD_POM)
    _write(tmp_path, "pyproject.toml", PYPROJECT)
    _write(tmp_path, "package.json", GOOD_JSON)
    records = ArtifactRegistry().derive_from_workspace(str(tmp_path), "M1", resolved_at_commit="tx")
    assert sorted(record.ecosystem for record in records) == ["maven", "npm", "python"]
    assert all(record.resolved_at_commit == "tx" for record in records)


def test_the_single_manifest_derivers_keep_their_none_contract(tmp_path):
    """The per-ecosystem helpers are unchanged (None for 'no record'); the registry is the authority on WHY."""
    _write(tmp_path, "package.json", BAD_JSON)
    _write(tmp_path, "pyproject.toml", "[project\n")
    _write(tmp_path, "pom.xml", "<project>")
    assert derive_npm_artifact(str(tmp_path), "M1") is None
    assert derive_python_artifact(str(tmp_path), "M1") is None
    assert derive_maven_artifact(str(tmp_path), str(tmp_path / "pom.xml"), "M1") is None


def test_validate_stays_tolerant_and_reports_a_now_underivable_recorded_artifact_as_drift(tmp_path):
    _write(tmp_path, "package.json", GOOD_JSON)
    registry = ArtifactRegistry()
    for record in registry.derive_from_workspace(str(tmp_path), "M1"):
        registry.record(record)
    assert [(v.reason_code, v.drifted) for v in registry.validate(str(tmp_path), "M1")] == [(None, False)]
    _write(tmp_path, "package.json", BAD_JSON)
    results = registry.validate(str(tmp_path), "M1")                    # must not raise
    assert [(v.reason_code, v.drifted, v.current) for v in results] == [("ARTIFACT_DRIFT", True, None)]


# --- enforce terminal: the same derivation, the same stop --------------------------------------------------------------

def test_the_enforce_terminal_gate_blocks_on_an_indeterminate_candidate_manifest_and_passes_the_controls(tmp_path):
    request = _request(tmp_path)
    report, events = _run(request, _validators())
    assert report.artifact_error is None and report.commit_eligible
    assert ("artifact_registry", "passed", None) in events

    _write(Path(request.candidate_root), "package.json", BAD_JSON)
    report, events = _run(request, _validators())
    assert report.artifact_error and report.artifact_error.startswith(ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE)
    assert not report.commit_eligible
    failed = [event for event in events if event[0] == "artifact_registry"]
    assert failed == [("artifact_registry", "failed", report.artifact_error)]


# --- the real milestone engine (only the model is scripted) ------------------------------------------------------------

class GateSpy:
    """Every QualityGateFailure the milestone pre-commit registry gate raised, with ``state.environment_failure`` AT the raise
    (later attempts clear the end-of-run value, so only the raise point can show the category)."""

    def __init__(self) -> None:
        self.failures: List[Any] = []
        self.environment: List[Any] = []


@pytest.fixture
def gate_failures(monkeypatch):
    spy = GateSpy()
    real = development_loop_module.run_artifact_registry_gate

    def wrapped(state, **kwargs):
        try:
            return real(state, **kwargs)
        except QualityGateFailure as error:
            spy.failures.append(error.failure)
            spy.environment.append(state.environment_failure)
            raise

    monkeypatch.setattr(development_loop_module, "run_artifact_registry_gate", wrapped)
    return spy


def _seed(root: Path, files):
    import subprocess

    root.mkdir()
    (root / "calc.py").write_text(CALC)
    for name, text in files.items():
        (root / name).write_text(text)
    for argv in (["init", "-q"], ["config", "user.email", "t@e.invalid"], ["config", "user.name", "T"],
                 ["add", "."], ["commit", "-qm", "seed"]):
        subprocess.run(["git", *argv], cwd=root, check=True)


class ManifestRun(Run):
    """Unit 2's Developer rewrites calc.py AND ``m2`` (a (filename, content) pair): the milestone itself commits a manifest."""

    m2 = None

    async def _complete(self, system_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        unit = self.units[-1] if self.units else None
        if self.m2 is not None and unit == 2:
            name, content = self.m2
            if "Developer Agent" in first:
                self.developer_units.append(unit)
                return json.dumps([{"filepath": "calc.py", "content": WITH_DIV}, {"filepath": name, "content": content}])
            if "Architect Agent" in first:
                return f"Design: edit calc.py and {name}"
            if "Planner Agent" in first:
                return f"Step 1: edit calc.py and {name}"
            if "File List Planner" in first:
                return json.dumps(["calc.py", name])
        return await super()._complete(system_prompt, *args, **kwargs)


def _execute(tmp_path, files, m2=None):
    workspace = tmp_path / "ws"
    _seed(workspace, files)
    run = ManifestRun(tmp_path / "cfg", {1: WITH_MUL, 2: WITH_DIV, INTEGRATION: WITH_DIV})
    run.m2 = m2
    result, state = run.execute(workspace)
    return workspace, run, result.legacy_result, state


@pytest.mark.parametrize("files", [
    pytest.param({}, id="control-A-no-manifest"),
    pytest.param({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}, id="control-valid-pyproject-without-project"),
    pytest.param({"package.json": '{"private": true}\n'}, id="control-valid-package-json-without-name"),
])
def test_control_a_a_milestone_with_no_expected_artifact_completes_normally(tmp_path, files, gate_failures):
    workspace, run, legacy, state = _execute(tmp_path, files)
    assert legacy["status"] == "success" and state.completed_milestone_ids == ["M1", "M2"]
    assert run.units == [1, 2, INTEGRATION]
    assert _registry(workspace).all_records() == ()                      # honest absence: no artifact expected
    assert gate_failures.failures == []


def test_defect_b_a_manifest_present_from_the_start_but_indeterminate_never_completes_a_milestone(tmp_path, gate_failures):
    workspace, run, legacy, state = _execute(tmp_path, {"package.json": BAD_JSON})
    assert legacy["status"] == "milestone_failed" and legacy["milestone_id"] == "M1"
    assert state.completed_milestone_ids == []
    assert run.units == [1]                                              # M2 (the dependent) and the integration unit never ran
    assert (workspace / "calc.py").read_text() == CALC                   # the real workspace is byte-identical
    assert (workspace / "package.json").read_text() == BAD_JSON
    assert _committed_transactions(workspace) == []                      # nothing was committed
    assert _registry(workspace).all_records() == ()
    assert gate_failures.failures, "the milestone pre-commit registry gate must have refused the candidate"
    for failure in gate_failures.failures:
        assert failure.type == "artifact_registry"
        assert failure.diagnostics["reason_code"] == ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE
        assert failure.message.startswith(ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE) and "package.json" in failure.message
    assert gate_failures.environment == [None] * len(gate_failures.failures)   # not an environment fault, at every raise
    inner = legacy["result"]
    assert inner["failure_category"] == "quality_gates_exhausted"        # bounded retry, then a typed stop
    assert any(row["failure_type"] == "artifact_registry" for row in inner["failure_report"])


def test_defect_b_a_milestone_that_commits_an_indeterminate_manifest_is_refused_before_the_commit(tmp_path, gate_failures):
    workspace, run, legacy, state = _execute(tmp_path, {"package.json": GOOD_JSON}, m2=("package.json", BAD_JSON))
    assert legacy["status"] == "milestone_failed" and legacy["milestone_id"] == "M2"
    assert state.completed_milestone_ids == ["M1"]                       # M1 stays complete, M2 is not
    assert run.units == [1, 2]                                           # the integration unit never ran
    assert (workspace / "calc.py").read_text() == WITH_MUL               # byte-identical to the state after M1
    assert (workspace / "package.json").read_text() == GOOD_JSON         # the bad manifest never reached the real workspace
    assert len(_committed_transactions(workspace)) == 1                  # only M1's commit
    registry = _registry(workspace)
    assert len(registry.resolve_for_milestone("M1")) == 1                # M1's record untouched
    assert registry.resolve_for_milestone("M2") == ()                    # never an empty-success record set for M2
    assert [(v.reason_code, v.drifted) for v in registry.validate(str(workspace), "M1")] == [(None, False)]
    assert gate_failures.failures and all(
        failure.diagnostics["reason_code"] == ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE for failure in gate_failures.failures)


# --- an artifact is EXPECTED when the registry already records it ----------------------------------------------------------

LOST_IDENTITY = [
    pytest.param("maven", {"pom.xml": GOOD_POM}, ("pom.xml", "<project xmlns='http://maven.apache.org/POM/4.0.0'><groupId>g</groupId></project>"),
                 "pom.xml", id="maven-pom-loses-artifactid"),
    pytest.param("npm", {"package.json": GOOD_JSON}, ("package.json", '{"private": true}\n'), "package.json", id="npm-loses-name"),
    pytest.param("python", {"pyproject.toml": PYPROJECT}, ("pyproject.toml", "[tool.ruff]\nline-length = 100\n"), "pyproject.toml",
                 id="python-loses-project-table"),
]


def _recorded(tmp_path, files):
    for name, text in files.items():
        _write(tmp_path, name, text)
    registry = ArtifactRegistry()
    for record in registry.derive_from_workspace(str(tmp_path), "M1"):
        registry.record(record)
    assert len(registry.all_records()) == 1
    return registry


@pytest.mark.parametrize("ecosystem, files, lost, named", LOST_IDENTITY)
def test_a_recorded_artifact_whose_present_descriptor_stops_deriving_it_is_indeterminate(tmp_path, ecosystem, files, lost, named):
    registry = _recorded(tmp_path, files)
    _write(tmp_path, *lost)
    with pytest.raises(ArtifactDerivationIndeterminate) as caught:
        registry.derive_from_workspace(str(tmp_path), "M2")
    assert [problem.split(":")[0] for problem in caught.value.problems] == [named]
    assert ecosystem in caught.value.problems[0] and "M1" in caught.value.problems[0]
    assert ArtifactRegistry().derive_from_workspace(str(tmp_path), "M2") == ()       # nothing recorded -> nothing expected: valid empty
    assert [(v.reason_code, v.drifted) for v in registry.validate(str(tmp_path), "M1")] == [("ARTIFACT_DRIFT", True)]   # validate stays tolerant


def test_the_expectation_is_only_about_a_present_descriptor_and_a_changed_identity_still_derives(tmp_path):
    registry = _recorded(tmp_path, {"package.json": GOOD_JSON})
    _write(tmp_path, "package.json", '{"name": "renamed", "version": "2.0.0"}\n')
    assert [r.coordinates["name"] for r in registry.derive_from_workspace(str(tmp_path), "M2")] == ["renamed"]
    (tmp_path / "package.json").unlink()                                                # a removed descriptor is drift for consumers, not flagged here
    assert registry.derive_from_workspace(str(tmp_path), "M2") == ()


def _plan_with_provides():
    return {"group_id": "grp-gatef", "original_goal": "Add mul and div to calc.py.", "milestones": [
        {"id": "M1", "goal": "Add mul(a, b) to calc.py.", "success_criterion": "mul works", "depends_on": [], "provides": [{"name": "lib"}]},
        {"id": "M2", "goal": "Add div(a, b) to calc.py.", "success_criterion": "div works", "depends_on": ["M1"], "consumes": ["lib"]},
    ]}


BARE_POM = ("<project xmlns='http://maven.apache.org/POM/4.0.0'><modelVersion>4.0.0</modelVersion>"
            "<groupId>g</groupId><version>1</version></project>\n")


@pytest.fixture
def maven_gates_pass(monkeypatch):
    """The Maven compile/test/pom gates need a container Maven cache; this characterization is about the registry, so they pass."""
    import test_gate_f_milestone_artifact_registry as gate_f_module

    from kriya.tools.validate import PolymorphicValidator

    ok = {"success": True, "output": "ok"}
    monkeypatch.setattr(gate_f_module, "_plan", _plan_with_provides)
    monkeypatch.setattr(PolymorphicValidator, "run_compile_check", lambda *a, **k: ok)
    monkeypatch.setattr(PolymorphicValidator, "run_tests", lambda *a, **k: {"success": True, "output": "tests/test_x.py .\n1 passed in 0.01s"})
    monkeypatch.setattr(PolymorphicValidator, "run_pom_validate", lambda *a, **k: ok)


def test_control_a_no_artifact_expected_a_parseable_incomplete_pom_completes(tmp_path, maven_gates_pass, gate_failures):
    workspace, run, legacy, state = _execute(tmp_path, {"pom.xml": BARE_POM})
    assert legacy["status"] == "success" and state.completed_milestone_ids == ["M1", "M2"]
    assert _registry(workspace).all_records() == () and gate_failures.failures == []


def test_case_b_an_artifact_recorded_by_m1_cannot_be_lost_by_m2_committing_a_pom_without_artifactid(
        tmp_path, maven_gates_pass, gate_failures):
    workspace, run, legacy, state = _execute(tmp_path, {"pom.xml": GOOD_POM}, m2=("pom.xml", BARE_POM))
    assert legacy["status"] == "milestone_failed" and legacy["milestone_id"] == "M2"
    assert state.completed_milestone_ids == ["M1"] and run.units == [1, 2]
    assert (workspace / "pom.xml").read_text() == GOOD_POM                              # the incomplete pom never reached the real workspace
    assert (workspace / "calc.py").read_text() == WITH_MUL and len(_committed_transactions(workspace)) == 1
    registry = _registry(workspace)
    [m1_record] = registry.resolve_for_milestone("M1")
    assert m1_record.ecosystem == "maven" and registry.resolve_for_milestone("M2") == ()
    assert [(v.reason_code, v.drifted) for v in registry.validate(str(workspace), "M1")] == [(None, False)]   # no stale record left behind
    assert gate_failures.failures and all(
        failure.diagnostics["reason_code"] == ARTIFACT_EXPECTED_BUT_DERIVATION_INDETERMINATE and "pom.xml" in failure.message
        for failure in gate_failures.failures)
    assert gate_failures.environment == [None] * len(gate_failures.failures)
