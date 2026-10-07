"""FS-1C0: a named pre-existing test closes a requirement only as an oracle
independent of the candidate (kriya/workflow/named_test_oracle.py).

End to end (real git base, real PolymorphicValidator, real pytest, the
production ``close_requirements_with_named_tests``): every candidate-controlled
channel measured to forge a pass before the fix
(handover/evidence/fs1c0/prefix_pins_3be1245.txt) is refused with its own
reason, and a correct candidate still closes with every binding recorded.
Then the surface's precision and the judge's rules in isolation."""
import pytest
from _named_test_oracle_harness import BASE, FLIP_HOOK, GOAL, NAMED, SCENARIOS, close, git, make_base, scenario, write

import kriya.workflow.workflow as workflow_module
from kriya.config.config import AutonomyConfig
from kriya.tools.test_execution import COMPLETE, INDETERMINATE, TestCaseResult, TestExecutionReport
from kriya.workflow.named_test_oracle import (
    CLOSURE_METHOD,
    ORACLE_BASE_UNAVAILABLE,
    ORACLE_BASELINE_INDETERMINATE,
    ORACLE_CHANGED_DURING_RUN,
    ORACLE_DEPENDENCY_CHANGED,
    ORACLE_EVIDENCE_INDETERMINATE,
    ORACLE_IDENTITY_NOT_EXECUTED,
    ORACLE_IDENTITY_NOT_PASSED,
    ORACLE_NOT_AT_BASE,
    ORACLE_PASSED,
    ORACLE_RUN_FAILED,
    ORACLE_RUNNER_UNSUPPORTED,
    BaseTree,
    OracleSurface,
    baseline_inventory,
    changed_surface,
    judge_candidate_run,
)
from kriya.workflow.requirements import (
    RequirementOutcome,
    blocking_requirements,
    derive_requirements,
    requirement_closure_id,
)

# ---------------------------------------------------------------- end to end: the measured forging channels

EXPECTED = {
    "wrong": (ORACLE_IDENTITY_NOT_PASSED, "tests.test_legacy::test_legacy"),
    "conftest_hook": (ORACLE_DEPENDENCY_CHANGED, "tests/conftest.py"),
    "support_module": (ORACLE_DEPENDENCY_CHANGED, "tests/helpers.py"),
    "runner_config": (ORACLE_DEPENDENCY_CHANGED, "pytest.ini"),
    "identity_skipped": (ORACLE_IDENTITY_NOT_PASSED, "tests.test_legacy::test_enabled"),
    "identity_missing": (ORACLE_IDENTITY_NOT_EXECUTED, "tests.test_legacy::test_defined"),
    "rewritten_during_run": (ORACLE_CHANGED_DURING_RUN, "tests/helpers.py"),
}


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_a_wrong_candidate_never_closes_through_a_candidate_controlled_channel(tmp_path, name):
    [attempt], outcome, ledger = scenario(tmp_path, name)
    code, detail = EXPECTED[name]
    assert attempt["closed"] is False and outcome is RequirementOutcome.UNVERIFIED
    assert attempt["reason_code"] == code and detail in attempt["reason"]
    assert ledger.current(requirement_closure_id("REQ-1")) is None
    reqs = derive_requirements(GOAL)
    assert [r.id for r, _ in blocking_requirements(ledger, reqs, unverified_policy="block")] == ["REQ-1"]


def test_a_correct_candidate_closes_with_every_binding_recorded(tmp_path):
    [attempt], outcome, ledger = scenario(tmp_path, "correct")
    assert attempt["closed"] is True and attempt["reason_code"] == ORACLE_PASSED
    assert outcome is RequirementOutcome.CLOSED_BY_EVIDENCE
    evidence = ledger.current(requirement_closure_id("REQ-1")).evidence
    head = git(tmp_path / "repo", "rev-parse", "HEAD").strip()
    assert evidence["method"] == CLOSURE_METHOD and evidence["evidence_id"] == "cand-1"
    assert evidence["base_revision"] == head and evidence["runner"] == "pytest"
    assert evidence["expected_cases"] == ["tests.test_legacy::test_defined", "tests.test_legacy::test_enabled",
                                          "tests.test_legacy::test_legacy"]
    assert evidence["provenance"] == "KRIYA_CONTROLLED"
    assert len(evidence["surface_digest"]) == 64 and evidence["surface_entries"] > 0
    assert evidence["report"]["gate_id"] and evidence["report"]["report_files"][0]["sha256"]
    assert evidence["baseline_report"][0]["sha256"]


def test_a_correct_candidate_that_also_adds_other_tests_still_closes(tmp_path):
    """Precision: a new test module the named test never imports cannot
    change its outcome (pytest runs only the named file; hooks come only
    from conftests and plugins, which are guarded)."""
    repo = make_base(tmp_path / "repo")
    write(repo, {"app.py": BASE["app.py"] + "\n\ndef added():\n    return 3\n",
                 "tests/test_added.py": "import app\n\n\ndef test_added():\n    assert app.added() == 3\n"})
    [attempt], outcomes, _ = close(repo, modified=["app.py", "tests/test_added.py"])
    assert attempt["closed"] is True and outcomes["REQ-1"] is RequirementOutcome.CLOSED_BY_EVIDENCE


def test_the_run_base_not_the_head_is_the_authorized_revision(tmp_path, monkeypatch):
    """An earlier unit of the same run committed a conftest hook: HEAD
    contains it, but it is still the run's own change, not the oracle's."""
    repo = make_base(tmp_path / "repo")
    run_base = git(repo, "rev-parse", "HEAD").strip()
    write(repo, {"tests/conftest.py": FLIP_HOOK})
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "earlier unit")
    write(repo, SCENARIOS["wrong"])
    monkeypatch.setattr(workflow_module, "_run_committed_paths",
                        lambda workspace: ("run-1", run_base, ["tests/conftest.py"]))
    [attempt], outcomes, _ = close(repo, modified=["app.py"])
    assert attempt["reason_code"] == ORACLE_DEPENDENCY_CHANGED and "tests/conftest.py" in attempt["reason"]
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_without_a_git_base_nothing_closes(tmp_path):
    write(tmp_path, BASE)
    [attempt], outcomes, _ = close(tmp_path, modified=[])
    assert attempt["reason_code"] == ORACLE_BASE_UNAVAILABLE and outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_named_test_the_candidate_created_is_not_an_oracle_even_if_unreported(tmp_path):
    repo = make_base(tmp_path / "repo", {k: v for k, v in BASE.items() if k != NAMED})
    write(repo, {NAMED: BASE[NAMED]})
    [attempt], outcomes, _ = close(repo, modified=[])  # the caller did not list it
    assert attempt["reason_code"] == ORACLE_NOT_AT_BASE and outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_runner_outside_the_boundary_is_refused(tmp_path):
    repo = make_base(tmp_path / "repo", {"Gemfile": "", "lib/foo.rb": "def foo; 1; end\n",
                                          "spec/foo_spec.rb": "describe 'foo' do\nend\n"})
    [attempt], outcomes, _ = close(repo, modified=[], goal="Behaviour stays compatible with foo_spec\n")
    assert attempt["reason_code"] == ORACLE_RUNNER_UNSUPPORTED and outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_a_named_test_that_does_not_run_at_the_base_has_no_inventory_and_closes_nothing(tmp_path):
    """Disclosed limit: a test written ahead of its implementation (it errors
    at the base) gives no base inventory of its cases, so it never closes."""
    files = dict(BASE)
    files[NAMED] = "from app import not_yet\n\n\ndef test_legacy():\n    assert not_yet() == 1\n"
    repo = make_base(tmp_path / "repo", files)
    write(repo, {"app.py": BASE["app.py"] + "\n\ndef not_yet():\n    return 1\n"})
    [attempt], outcomes, _ = close(repo, modified=["app.py"])
    assert attempt["closed"] is False and outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED
    assert attempt["reason_code"] in (ORACLE_IDENTITY_NOT_EXECUTED, ORACLE_BASELINE_INDETERMINATE)


def test_contained_execution_settings_reach_both_validators(tmp_path, monkeypatch):
    """The base export and the candidate run under the caller's autonomy
    configuration (containment included) - never a looser one."""
    from kriya.tools.validate import PolymorphicValidator

    seen = []
    real_init = PolymorphicValidator.__init__

    def init(validator, *args, **kwargs):
        real_init(validator, *args, **kwargs)
        seen.append(validator.autonomy_cfg)

    monkeypatch.setattr(PolymorphicValidator, "__init__", init)
    autonomy = AutonomyConfig()
    repo = make_base(tmp_path / "repo")
    write(repo, SCENARIOS["correct"])
    close(repo, modified=["app.py"], autonomy=autonomy)
    assert len(seen) == 2 and all(cfg is autonomy for cfg in seen)


# ---------------------------------------------------------------- the trust surface

PY = {"requirements.txt": "", "app.py": "def f():\n    return 1\n", "tests/__init__.py": "",
      "tests/test_x.py": "import app\n\n\ndef test_x():\n    assert app.f() == 1\n"}


def _changed(tmp_path, candidate, *, base=PY, named=("tests/test_x.py",), modified=()):
    repo = make_base(tmp_path / "repo", base)
    write(repo, candidate)
    surface = OracleSurface(BaseTree(str(repo), "HEAD"), str(repo), list(named))
    base_digests, candidate_digests = surface.digests()
    return changed_surface(base_digests, candidate_digests) + surface.output_root_writes(modified)


@pytest.mark.parametrize("candidate, changed", [
    # runner configuration: the pytest sections, wherever pytest looks for them
    ({"pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-p x\"\n"}, ["pyproject.toml"]),
    ({"tests/pyproject.toml": "[tool.pytest.ini_options]\naddopts = \"-p x\"\n"}, ["tests/pyproject.toml"]),
    ({"setup.cfg": "[tool:pytest]\naddopts = -p x\n"}, ["setup.cfg"]),
    ({"tox.ini": "[pytest]\naddopts = -p x\n"}, ["tox.ini"]),
    ({".pytest.ini": "[pytest]\n"}, [".pytest.ini"]),
    ({"tests/pytest.ini": "[pytest]\n"}, ["tests/pytest.ini"]),
    # declared dependencies (installed packages load as pytest plugins)
    ({"requirements.txt": "pytest-forge==1\n"}, ["requirements.txt"]),
    ({"pyproject.toml": "[project]\nname = \"x\"\ndependencies = [\"pytest-forge\"]\n"}, ["pyproject.toml"]),
    # conftests and packages on the named test's path
    ({"conftest.py": "x = 1\n"}, ["conftest.py"]),
    ({"tests/__init__.py": "import os\n"}, ["tests/__init__.py"]),
    # test resources
    ({"tests/data/expected.json": "{}"}, ["tests/data/expected.json"]),
    # the named test itself
    ({"tests/test_x.py": "def test_x():\n    pass\n"}, ["tests/test_x.py"]),
])
def test_every_execution_affecting_python_file_is_on_the_surface(tmp_path, candidate, changed):
    assert _changed(tmp_path, candidate) == changed


CONFIGURED = dict(PY, **{"pyproject.toml": "[tool.black]\nline-length = 88\n",
                          "setup.cfg": "[flake8]\nmax-line-length = 88\n"})


@pytest.mark.parametrize("candidate", [
    {"app.py": "def f():\n    return 2\n"},                                   # production code: the subject
    {"pyproject.toml": "[tool.black]\nline-length = 99\n"},                    # no pytest/dependency table
    {"setup.cfg": "[flake8]\nmax-line-length = 99\n"},
    {"tests/test_other.py": "def test_other():\n    pass\n"},                 # not imported by the oracle
    {"other/conftest.py": "x = 1\n"},                                          # not on the named test's path
    {"README.md": "docs\n"},
])
def test_files_that_cannot_change_the_named_tests_outcome_are_not_on_the_surface(tmp_path, candidate):
    assert _changed(tmp_path, candidate, base=CONFIGURED) == []


def test_a_new_config_file_on_the_path_counts_even_without_a_pytest_section(tmp_path):
    """Its presence moves pytest's rootdir (and so confcutdir: which base
    conftests load), whatever it contains."""
    assert _changed(tmp_path / "a", {"tests/pyproject.toml": "[tool.black]\n"}) == ["tests/pyproject.toml"]
    assert _changed(tmp_path / "b", {"setup.cfg": "[flake8]\n"}) == ["setup.cfg"]


def test_the_import_closure_follows_test_side_modules_including_plugins_and_shadowing(tmp_path):
    base = dict(PY)
    base["tests/test_x.py"] = "from tests.support import util\n\n\ndef test_x():\n    assert util.ok()\n"
    base["tests/support/__init__.py"] = ""
    base["tests/support/util.py"] = "from . import deeper\n\n\ndef ok():\n    return deeper.ok()\n"
    base["tests/support/deeper.py"] = "def ok():\n    return True\n"
    base["tests/conftest.py"] = "pytest_plugins = [\"tests.plugin_mod\"]\n"
    base["tests/plugin_mod.py"] = "x = 1\n"
    assert _changed(tmp_path / "a", {"tests/support/deeper.py": "def ok():\n    return 1\n"}, base=base) == [
        "tests/support/deeper.py"]
    assert _changed(tmp_path / "b", {"tests/plugin_mod.py": "x = 2\n"}, base=base) == ["tests/plugin_mod.py"]
    # A file that newly shadows a module the oracle imports counts as a change.
    shadow = dict(PY)
    shadow["tests/test_x.py"] = "import fixtures_lib\n\n\ndef test_x():\n    assert fixtures_lib\n"
    assert _changed(tmp_path / "c", {"tests/fixtures_lib.py": "x = 1\n"}, base=shadow) == ["tests/fixtures_lib.py"]


def test_requirement_includes_and_local_installs_are_followed(tmp_path):
    base = dict(PY, **{"requirements.txt": "-r requirements/test.txt\n", "requirements/test.txt": "pytest\n"})
    assert _changed(tmp_path / "a", {"requirements/test.txt": "pytest\npytest-forge\n"}, base=base) == [
        "requirements/test.txt"]
    local = dict(PY, **{"requirements.txt": "-e .\n", "setup.py": "x = 1\n"})
    assert _changed(tmp_path / "b", {"setup.py": "x = 2\n"}, base=local) == ["setup.py"]


def test_a_gitignored_or_symlinked_conftest_is_still_seen(tmp_path):
    assert _changed(tmp_path / "a", {".gitignore": "conftest.py\n", "tests/conftest.py": FLIP_HOOK}) == [
        "tests/conftest.py"]
    repo = make_base(tmp_path / "b" / "repo", PY)
    (repo / "elsewhere.py").write_text(FLIP_HOOK)
    (repo / "tests" / "conftest.py").symlink_to(repo / "elsewhere.py")
    surface = OracleSurface(BaseTree(str(repo), "HEAD"), str(repo), ["tests/test_x.py"])
    assert changed_surface(*surface.digests()) == ["tests/conftest.py"]


JVM = {"pom.xml": "<project/>", "src/main/java/a/App.java": "class App {}",
       "src/test/java/a/AppTest.java": "class AppTest {}"}


@pytest.mark.parametrize("candidate, changed", [
    ({"pom.xml": "<project><build/></project>"}, ["pom.xml"]),
    ({"src/test/java/a/Helper.java": "class Helper {}"}, ["src/test/java/a/Helper.java"]),
    ({"src/test/resources/junit-platform.properties": "x=1"}, ["src/test/resources/junit-platform.properties"]),
    ({"src/main/resources/META-INF/services/org.junit.jupiter.api.extension.Extension": "a.Swallow"},
     ["src/main/resources/META-INF/services/org.junit.jupiter.api.extension.Extension"]),
    ({".mvn/maven.config": "-DskipTests"}, [".mvn/maven.config"]),
    ({"build.gradle": "test {}"}, ["build.gradle"]),
])
def test_the_jvm_surface_is_the_build_and_the_whole_scanned_test_classpath(tmp_path, candidate, changed):
    assert _changed(tmp_path, candidate, base=JVM, named=("src/test/java/a/AppTest.java",)) == changed


def test_every_non_main_source_set_of_every_module_is_on_the_jvm_surface(tmp_path):
    """Another module's tests (a test-jar dependency) and other source sets
    (integration tests, test fixtures) load on the same scanned classpath."""
    base = {"pom.xml": "<project/>", "a/pom.xml": "<project/>", "b/pom.xml": "<project/>",
            "a/src/test/java/a/AppTest.java": "class AppTest {}", "b/src/main/java/b/B.java": "class B {}"}
    named = ("a/src/test/java/a/AppTest.java",)
    assert _changed(tmp_path / "a", {"b/src/test/java/b/Helper.java": "class Helper {}"}, base=base,
                    named=named) == ["b/src/test/java/b/Helper.java"]
    assert _changed(tmp_path / "b", {"a/src/integrationTest/java/a/It.java": "class It {}"}, base=base,
                    named=named) == ["a/src/integrationTest/java/a/It.java"]
    assert _changed(tmp_path / "c", {"b/src/main/java/b/B.java": "class B { int x; }"}, base=base,
                    named=named) == []


def test_a_python_src_layout_is_not_mistaken_for_a_jvm_source_set(tmp_path):
    base = dict(PY, **{"src/pkg/__init__.py": "", "src/pkg/mod.py": "x = 1\n"})
    assert _changed(tmp_path, {"src/pkg/mod.py": "x = 2\n"}, base=base) == []


def test_a_candidate_write_into_an_output_root_refuses_the_closure_end_to_end(tmp_path):
    """A correct candidate that also wrote bytecode next to the oracle's
    support module (loaded without its source being read) never closes."""
    repo = make_base(tmp_path / "repo")
    write(repo, SCENARIOS["correct"])
    write(repo, {"tests/__pycache__/helpers.cpython-314.pyc": "planted"})
    [attempt], outcomes, _ = close(repo, modified=["app.py", "tests/__pycache__/helpers.cpython-314.pyc"])
    assert attempt["reason_code"] == ORACLE_DEPENDENCY_CHANGED and "__pycache__" in attempt["reason"]
    assert outcomes["REQ-1"] is RequirementOutcome.UNVERIFIED


def test_jvm_production_sources_and_build_output_are_not_surface_but_writes_into_output_are(tmp_path):
    named = ("src/test/java/a/AppTest.java",)
    assert _changed(tmp_path / "a", {"src/main/java/a/App.java": "class App { int x; }",
                                     "src/main/java/a/build/Tool.java": "class Tool {}",
                                     "target/classes/META-INF/services/x": "gate output"},
                    base=JVM, named=named) == []
    assert _changed(tmp_path / "b", {"target/test-classes/META-INF/services/x": "planted"}, base=JVM, named=named,
                    modified=["target/test-classes/META-INF/services/x"]) == [
        "target/test-classes/META-INF/services/x"]
    assert _changed(tmp_path / "c", {}, modified=["tests/__pycache__/helpers.cpython-314.pyc"]) == [
        "tests/__pycache__/helpers.cpython-314.pyc"]


# ---------------------------------------------------------------- the judge in isolation

def _result(cases, *, complete=True, runner="pytest", success=True):
    report = TestExecutionReport(gate_id="g", runner=runner, workspace="w",
                                 completeness=COMPLETE if complete else INDETERMINATE,
                                 cases=[TestCaseResult(identity=f"{c}.{n}", classname=c, name=n, status=s)
                                        for c, n, s in cases])
    return {"success": success, "output": "", "test_execution": report.to_dict()}


EXPECTED_CASES = ["t::a", "t::b[1]", "t::b[2]"]
ALL_PASSED = [("t", "a", "passed"), ("t", "b[1]", "passed"), ("t", "b[2]", "passed")]


@pytest.mark.parametrize("result, code", [
    (_result(ALL_PASSED), ORACLE_PASSED),
    (_result(ALL_PASSED, complete=False), ORACLE_EVIDENCE_INDETERMINATE),       # report trusted blindly
    ({"success": True, "output": "3 passed"}, ORACLE_EVIDENCE_INDETERMINATE),    # no report at all
    (_result(ALL_PASSED, runner="maven"), ORACLE_EVIDENCE_INDETERMINATE),        # another runner's report
    (_result(ALL_PASSED, success=False), ORACLE_RUN_FAILED),                     # report alone is not enough
    (_result(ALL_PASSED[:2]), ORACLE_IDENTITY_NOT_EXECUTED),                     # a parameter set shrank
    (_result(ALL_PASSED[1:]), ORACLE_IDENTITY_NOT_EXECUTED),                     # a case deselected/missing
    (_result([("t", "a", "skipped")] + ALL_PASSED[1:]), ORACLE_IDENTITY_NOT_PASSED),   # exit 0, skipped
    (_result([("t", "a", "passed"), ("t", "a", "failed")] + ALL_PASSED[1:]), ORACLE_IDENTITY_NOT_PASSED),
    (_result(ALL_PASSED + [("t", "extra", "failed")]), ORACLE_PASSED),          # only the expected cases count
])
def test_the_candidate_run_must_be_complete_carry_every_expected_case_passed_and_succeed(result, code):
    assert judge_candidate_run(EXPECTED_CASES, result, "pytest")[0] == code


def test_the_baseline_inventory_is_exact_cases_of_a_complete_report_only():
    assert baseline_inventory(_result([("t", "b[1]", "failed"), ("t", "a", "passed")]), "pytest") == [
        "t::a", "t::b[1]"]
    assert baseline_inventory(_result(ALL_PASSED, complete=False), "pytest") is None
    assert baseline_inventory(_result([]), "pytest") is None
    assert baseline_inventory(_result(ALL_PASSED, runner="maven"), "pytest") is None
    assert baseline_inventory({"success": True}, "pytest") is None
