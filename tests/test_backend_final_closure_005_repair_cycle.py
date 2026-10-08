"""BACKEND-FINAL-CLOSURE-005 Phase 14 systemic repair cycle: the three Kriya mechanisms the primary cohort measured
from the certified main 41c5b10, each with a repository-independent reproducer.

- GRADLE-SUBPROJECT-BUILD-FILE-001 (P5-T3, FALSE NEGATIVE): JavaHamcrest's settings rename every subproject's build
  file after its directory (hamcrest/hamcrest.gradle); the Gradle project predicate looked for build.gradle[.kts] only,
  so hamcrest/build/test-results was never cleared nor read: the suite's structured evidence was
  STRUCTURED_REPORT_MISSING and the suite-preservation requirement could not close on a candidate the sealed external
  oracle had already accepted (hidden 2/2 pass, COMPAT PASS, 489 regressions pass on the exported candidate).
- PLAN-DOCUMENTATION-UNIT-VERIFICATION-001 (P4-T5-r2 and P5-T5): a subtask editing only the documentation the sealed
  contract judges deterministically had no legal verification shape (judgment: no evidence producer; runtime:
  unjustified; none: acceptance path missing) - the Planner circled through every rejection twice.
- PROTOCOL-FEEDBACK-EDIT-OPENING-001 (P5-T2): six of eight Developer answers began with a SEARCH line (once with a
  path) and the diagnostic named only the symptom; it now names the missing EDIT opening line. Parse outcome and wire
  protocol unchanged (feedback text only).
"""
import asyncio
import os

from kriya.agents.response_protocol import INVALID, parse_structured
from kriya.capabilities.gradle import GradleBuildAdapter
from kriya.tools import test_execution
from kriya.tools.validate import gradle_project_dirs
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.plan_validation import validate_plan
from kriya.workflow.planner_repair import build_structured_plan_repair_prompt

JUNIT = ('<?xml version="1.0" encoding="UTF-8"?>\n<testsuite name="org.example.ATest" tests="1" skipped="0" failures="0" '
         'errors="0" timestamp="2026-10-08T00:00:00" time="0.01">\n  <testcase name="works" classname="org.example.ATest" '
         'time="0.001"/>\n</testsuite>\n')


def _renamed_build_files(tmp_path):
    root = tmp_path / "ws"
    (root / "hamcrest").mkdir(parents=True)
    (root / "build.gradle").write_text("// root\n")
    (root / "settings.gradle").write_text(
        "include 'hamcrest'\nrootProject.children.each { c -> c.buildFileName = \"${c.name}.gradle\" }\n")
    (root / "hamcrest" / "hamcrest.gradle").write_text("plugins { id 'java' }\n")  # the renamed subproject build file
    (root / "plain").mkdir()
    (root / "plain" / "notes.txt").write_text("not a project\n")
    return root


def test_a_subproject_whose_build_file_is_named_after_its_directory_is_a_gradle_project(tmp_path):
    root = _renamed_build_files(tmp_path)
    dirs = sorted(os.path.relpath(d, root) for d in gradle_project_dirs(str(root)))
    assert dirs == [".", "hamcrest"]
    assert sorted(os.path.relpath(d, root) for d in test_execution._jvm_report_dirs(str(root), "gradle")) == [  # pylint: disable=protected-access
        "build/test-results", "hamcrest/build/test-results"]
    roots = GradleBuildAdapter().output_roots(["./gradlew", "test"], str(root))
    assert os.path.join(str(root), "hamcrest", "build") in roots and os.path.join(str(root), ".gradle") in roots


def test_the_subprojects_junit_report_is_cleared_before_and_read_after_the_gradle_gate(tmp_path):
    """The measured shape end to end on the report binding: a stale report is cleared, the run's own report read."""
    root = _renamed_build_files(tmp_path)
    reports = root / "hamcrest" / "build" / "test-results" / "test"
    reports.mkdir(parents=True)
    (reports / "TEST-stale.xml").write_text(JUNIT)
    binding = test_execution.prepare(str(root), "gradle")
    assert not (reports / "TEST-stale.xml").exists() and binding.cleared_reports == 1  # the subproject's dir is known
    (reports / "TEST-org.example.ATest.xml").write_text(JUNIT)  # what the gate's ./gradlew test wrote
    binding.observe({"returncode": 0, "stdout": "BUILD SUCCESSFUL", "stderr": ""})
    report = test_execution.collect(binding)
    assert report.complete and report.reason is None, (report.completeness, report.reason)
    assert [c.name for c in report.cases] == ["works"]


def _plan(documentation_verification):
    return EngineeringPlan.model_validate({
        "plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
        "acceptance_criteria": [{"id": "ac1", "description": "functions work", "method": "tool", "tool_name": "test"}],
        "subtasks": [
            {"id": "s1", "description": "add the functions", "execution_method": "model",
             "planned_files": [{"path": "pkg/functions.py", "action": "modify"}], "provides": ["functions"],
             "relevant_global_invariant_ids": ["gi1"], "acceptance_criteria_ids": ["ac1"],
             "verification": [{"type": "tool", "tool_name": "test", "description": "run the tests"}]},
            {"id": "s2", "description": "document them in the README's function list", "execution_method": "model",
             "planned_files": [{"path": "README.rst", "action": "modify"}], "depends_on": ["s1"], "requires": ["functions"],
             "relevant_global_invariant_ids": ["gi1"], "verification": documentation_verification},
        ]})


def _validate(plan, ws, **kw):
    return asyncio.run(validate_plan(plan, workspace_path=str(ws), require_model_planned_files=True, **kw))


def test_a_documentation_only_unit_declares_no_verification_and_is_valid_only_with_the_contracts_referent(tmp_path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "pkg" / "functions.py").write_text("x = 1\n")
    (ws / "README.rst").write_text("Functions\n=========\n\n- one\n")
    no_verification = _plan([])
    ok = _validate(no_verification, ws, documentation_paths=["README.rst"])
    assert ok.valid is True, ok.errors
    without = _validate(no_verification, ws)  # no documentation claim bound: the old rule stands
    assert "MUTATION_UNIT_ACCEPTANCE_PATH_MISSING" in without.reason_codes
    judgment = _plan([{"type": "judgment", "description": "README documents the functions"}])
    refused = _validate(judgment, ws, documentation_paths=["README.rst"])
    assert "VERIFICATION_EVIDENCE_PATH_MISSING" in refused.reason_codes
    assert "declare verification: []" in "".join(refused.errors) and "README.rst" in "".join(refused.errors)
    # a unit that edits documentation AND code is not documentation-only
    mixed = _plan([])
    mixed.subtasks[1].planned_files.append(type(mixed.subtasks[1].planned_files[0])(path="pkg/other.py", action="create"))
    assert "MUTATION_UNIT_ACCEPTANCE_PATH_MISSING" in _validate(mixed, ws, documentation_paths=["README.rst"]).reason_codes
    prompt = build_structured_plan_repair_prompt("g", "{}", refused.errors, refused.reason_codes, 1)
    assert "declare verification: []" in prompt and "documentation predicate" in prompt


def test_a_search_line_outside_an_edit_block_names_the_missing_opening_line():
    for opening in ("<<<KRIYA:SEARCH>>>", '<<<KRIYA:SEARCH path="a/B.java">>>', "<<<KRIYA:REPLACE>>>"):
        text = f"The fix.\n{opening}\nold\n<<<KRIYA:REPLACE>>>\nnew\n<<<KRIYA:END_EDIT>>>\n"
        parsed = parse_structured(text, "a/B.java")
        assert parsed.kind == INVALID
        assert 'begins with <<<KRIYA:EDIT path="a/B.java">>>' in parsed.error and "outside an EDIT block" in parsed.error
    other = parse_structured("x\n<<<KRIYA:BOGUS path=\"a/B.java\">>>\n", "a/B.java")
    assert other.kind == INVALID and "unexpected protocol line" in other.error


def test_the_documentation_referent_paths_are_the_files_the_sealed_predicate_judges(tmp_path):
    """Review of the repair, finding 1: a docs-directory referent's binding lists every file under docs/ (code
    included); only the files whose named list section the predicate judges (its headings) are planning inputs."""
    from types import SimpleNamespace

    from kriya.workflow.contract_compilation import compile_verification_contract
    from kriya.workflow.requirements import derive_requirements, statement_origins
    from kriya.workflow.workflow import documentation_referent_paths

    assert documentation_referent_paths(None) == []
    # a real compiled README contract: the README is the judged file
    root = tmp_path / "ws"
    root.mkdir()
    (root / "README.rst").write_text("Functions\n=========\n\n- ``one``\n")
    (root / "pkg").mkdir()
    (root / "pkg" / "f.py").write_text("x = 1\n")
    goal = "Add two built-in functions: lower and upper.\n\nDocument them in the README's function list.\n"
    reqs = derive_requirements(goal)
    tracked = ["README.rst", "pkg/f.py"]
    contract = compile_verification_contract(
        reqs, origins=statement_origins(goal), test_files=[], tracked_paths=tracked, project_language="python",
        tracked_file_reader=lambda rel: (root / rel).read_bytes() if (root / rel).exists() else None)
    assert documentation_referent_paths(contract) == ["README.rst"]
    # the docs-directory shape, as the sealed binding records it: judged headings name docs/index.rst only
    class _Binding(SimpleNamespace):
        pass
    fake = SimpleNamespace(binding_closers=lambda closer: [("REQ-2", _Binding(detail={
        "paths": ["docs/conf.py", "docs/examples/run.py", "docs/index.rst"], "list_noun": "functions",
        "headings": ["docs/index.rst: Functions"]}))])
    assert documentation_referent_paths(fake) == ["docs/index.rst"]  # never docs/conf.py
    no_binding = SimpleNamespace(binding_closers=lambda closer: [])
    assert documentation_referent_paths(no_binding) == []


def test_a_code_file_under_a_docs_directory_is_never_a_documentation_only_unit(tmp_path):
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "conf.py").write_text("x = 1\n")
    (ws / "docs" / "index.rst").write_text("Functions\n=========\n")
    (ws / "pkg").mkdir()
    (ws / "pkg" / "functions.py").write_text("x = 1\n")
    plan = _plan([])
    plan.subtasks[1].planned_files[0] = type(plan.subtasks[1].planned_files[0])(path="docs/conf.py", action="modify")
    refused = _validate(plan, ws, documentation_paths=["docs/index.rst"])
    assert "MUTATION_UNIT_ACCEPTANCE_PATH_MISSING" in refused.reason_codes
    # a one-shot iterable of documentation paths still exempts every documentation unit, not just the first
    plan_docs = _plan([])
    plan_docs.subtasks[1].planned_files[0] = type(plan_docs.subtasks[1].planned_files[0])(path="docs/index.rst", action="modify")
    assert _validate(plan_docs, ws, documentation_paths=iter(["docs/index.rst"])).valid is True
