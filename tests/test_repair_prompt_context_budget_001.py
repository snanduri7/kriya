"""REPAIR-PROMPT-CONTEXT-BUDGET-001 (Scenario-A run 20261113T235143-0762989b): what a unit's request carries.

MEASURED in run 10: every later unit was shown every earlier unit's files - the test unit s6 (depends on s3, which depends on s2)
carried pom.xml, App.java and ignite-config.xml in each request, 19617 characters of mandatory text; and the CURRENT failure was cut
blindly at 6000 characters, the same cut that lost the assertion from a carried finding (OPEN-FINDING-EVIDENCE-001).

Two corrections are pinned here:
- the earlier-unit source a unit is shown is what its plan-declared dependencies established, directly or through a chain. The plan's
  ``depends_on`` is the authority; nothing is inferred from a file name. The workspace, the gates and ``established_files`` still cover
  the whole project;
- the current failure is excerpted around the validator's own locations, and only falls back to both ends when the output reports none.

NOT corrected here (registered): the whole selected skill is still rendered into every request (SKILL-RELEVANCE-CONTEXT-SCOPE-001), and the
retry package still repeats established files as bounded reference excerpts.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_shared_decision_authority_001 import (  # noqa: F401 - skills_root is a pytest fixture
    _run_enforce,
    _unit,
    skills_root,
)

from kriya.workflow.enforce_loop import established_context_for_dependencies
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.retry_package import _bounded_text, build_retry_package
from kriya.workflow.triage import ChangeKind

RUN10_OUTPUT = (Path(__file__).parent / "fixtures/open_finding_evidence_001/run10_s6_attempt2_maven_test_output.txt").read_text(
    encoding="utf-8")
ASSERTION = "expected: <9999999999> but was: <1410065407>"
# Run 10's plan shape (MEASURED from its unit prompts): s3 depends on s2, s4 on s1, s5 on s3 and s4, s6 on s3.
RUN10_SHAPE = (("s1", "pom.xml", ()), ("s2", "Protocol.java", ()), ("s3", "ProtocolParser.java", ("s2",)),
               ("s4", "ignite-config.xml", ("s1",)), ("s5", "App.java", ("s3", "s4")), ("s6", "ProtocolParserTest.java", ("s3",)))
WRITERS = {path: unit for unit, path, _deps in RUN10_SHAPE}
ESTABLISHED = {path: f"content of {path}" for _unit_id, path, _deps in RUN10_SHAPE}


def _shape_plan(shape=RUN10_SHAPE):
    return SimpleNamespace(subtasks=[SimpleNamespace(id=unit, depends_on=list(deps)) for unit, _path, deps in shape])


def _shown(unit_id, shape=RUN10_SHAPE, established=None, writers=None):
    return sorted(established_context_for_dependencies(
        ESTABLISHED if established is None else established, WRITERS if writers is None else writers, _shape_plan(shape), unit_id))


# --- the dependency rule ---------------------------------------------------------------------------------------------------------

def test_run10_the_test_unit_is_shown_its_direct_and_transitive_dependencies_and_its_own_file_only():
    assert _shown("s6") == ["Protocol.java", "ProtocolParser.java", "ProtocolParserTest.java"]


def test_a_direct_dependency_is_shown():
    assert "ProtocolParser.java" in _shown("s6") and "pom.xml" in _shown("s4")


def test_a_transitive_dependency_is_shown():
    assert "Protocol.java" in _shown("s6")                      # s6 -> s3 -> s2
    assert _shown("s5") == ["App.java", "Protocol.java", "ProtocolParser.java", "ignite-config.xml", "pom.xml"]   # through s3 and s4


def test_an_unrelated_earlier_sibling_is_not_shown():
    shown = _shown("s6")
    assert "pom.xml" not in shown and "ignite-config.xml" not in shown and "App.java" not in shown
    assert _shown("s2") == ["Protocol.java"]                    # no dependency: nothing from s1


def test_a_file_with_no_recorded_writer_is_kept():
    """Fail safe: what nobody is recorded to have written is not assumed unrelated."""
    assert "legacy.txt" in _shown("s6", established={**ESTABLISHED, "legacy.txt": "x"})


def test_the_decision_does_not_depend_on_file_names():
    renamed = {"fieldA.bin": "s1", "sequenceNumber.dat": "s2", "flags.cfg": "s3"}
    shape = (("s1", "fieldA.bin", ()), ("s2", "sequenceNumber.dat", ()), ("s3", "flags.cfg", ("s2",)), ("s4", "t.txt", ("s3",)))
    shown = established_context_for_dependencies({path: "c" for path in renamed}, renamed, _shape_plan(shape), "s4")
    assert sorted(shown) == ["flags.cfg", "sequenceNumber.dat"]


def test_an_unknown_unit_and_a_dependency_cycle_are_harmless():
    assert _shown("s99") == []
    cyclic = (("a", "a.txt", ("b",)), ("b", "b.txt", ("a",)))
    shown = established_context_for_dependencies({"a.txt": "1", "b.txt": "2"}, {"a.txt": "a", "b.txt": "b"}, _shape_plan(cyclic), "a")
    assert sorted(shown) == ["a.txt", "b.txt"]


def test_the_input_is_not_changed():
    established, writers = dict(ESTABLISHED), dict(WRITERS)
    established_context_for_dependencies(established, writers, _shape_plan(), "s6")
    assert established == ESTABLISHED and writers == WRITERS


# --- through the real enforce controller ----------------------------------------------------------------------------------------

@pytest.mark.usefixtures("bound_workspace")
@pytest.mark.asyncio
async def test_enforce_shows_a_unit_only_its_dependencies_source_while_the_whole_project_stays_established(tmp_path, skills_root):  # noqa: F811 - pytest fixture
    plan = EngineeringPlan(plan_id="run1", kind=ChangeKind.TASK,
                           subtasks=[_unit(unit, path.replace(".java", ".py").replace(".xml", ".py"), deps)
                                     for unit, path, deps in RUN10_SHAPE])
    paths = {unit.id: unit.planned_files[0].path for unit in plan.subtasks}
    _request, calls = await _run_enforce(tmp_path, skills_root, plan)
    by_unit = {call["current_subtask_id"]: call for call in calls}
    assert list(by_unit) == ["s1", "s2", "s3", "s4", "s5", "s6"]

    def shown(unit):
        return sorted(sid for sid, path in paths.items() if f"=== {path} (already built by an earlier milestone" in by_unit[unit]["supplementary_context"])

    assert shown("s6") == ["s2", "s3"]                # direct + transitive; s1, s4, s5 not shown
    assert shown("s5") == ["s1", "s2", "s3", "s4"]
    assert shown("s4") == ["s1"] and shown("s3") == ["s2"] and shown("s2") == [] and shown("s1") == []
    # The project itself is whole: every earlier file is still an established file of the unit and is on disk for its gates.
    assert by_unit["s6"]["established_files"] == sorted(paths[sid] for sid in ("s1", "s2", "s3", "s4", "s5"))
    assert all(os.path.exists(os.path.join(by_unit["s6"]["workspace_path"], paths[sid])) for sid in ("s1", "s4", "s5"))
    # The global goal still reaches the unit.
    assert "Overall request constraints" in by_unit["s6"]["supplementary_context"]


# --- the current failure --------------------------------------------------------------------------------------------------------

PARSER_TEST = "src/test/java/com/example/ProtocolParserTest.java"


def _package(tmp_path, failure, max_error_chars):
    target = tmp_path / PARSER_TEST
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("class ProtocolParserTest {}\n", encoding="utf-8")
    return build_retry_package(failure=failure, worktree_path=str(tmp_path), all_files=[PARSER_TEST], target_files=[PARSER_TEST],
                               source_context=None, max_chars=4000, max_error_chars=max_error_chars)


def _failure(raw, locations=((PARSER_TEST, 75),)):
    return Failure(type="targeted_test", message="TEST FAILURE", raw_output=raw,
                   file_locations=[FileLocation(filepath=path, line=line) for path, line in locations], likely_files=[PARSER_TEST])


def test_run10_output_within_the_bound_is_shown_whole(tmp_path):
    assert _package(tmp_path, _failure(RUN10_OUTPUT), 12000).authoritative_error == RUN10_OUTPUT


@pytest.mark.parametrize("bound", [6000, 3000, 1500])
def test_the_current_failure_keeps_its_assertion_when_it_must_be_cut(tmp_path, bound):
    """The default bound is 6000. In run 10 the assertion happened to sit in the kept head and tail; under a tighter bound, or with it
    deeper in the output, the blind cut loses it."""
    padded = "[INFO] build output line\n" * 400 + RUN10_OUTPUT + "[ERROR] help text line\n" * 400
    error = _package(tmp_path, _failure(padded), bound).authoritative_error
    assert ASSERTION in error and "(ProtocolParserTest.java:75)" in error and len(error) <= bound
    assert ASSERTION not in _bounded_text(padded, bound)       # the cut it replaces


def test_a_current_failure_with_no_reported_location_keeps_the_existing_cut(tmp_path):
    padded = "HEAD\n" + "noise\n" * 5000 + "TAIL"
    assert _package(tmp_path, _failure(padded, locations=()), 6000).authoritative_error == _bounded_text(padded, 6000)
    assert _package(tmp_path, _failure(padded), 6000).authoritative_error == _bounded_text(padded, 6000)   # location not in the output
