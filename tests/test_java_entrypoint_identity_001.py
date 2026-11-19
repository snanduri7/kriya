"""JAVA-ENTRYPOINT-IDENTITY-001 and RETRY-ACTIVE-FAILURE-TRUTH-001 (Stage C run c1, 20261119T101125-d5e300d1; owner rulings 2026-11-19).

MEASURED in the live run:

    s1 wrote pom.xml with <exec.mainClass>com.example.ProtocolApplication</exec.mainClass> before any class existed;
    s5 wrote src/main/java/ProtocolApplication.java in the default package (it does not own pom.xml);
    s7 owned pom.xml, answered NO CHANGE ("already correct") seven times, and each retry request still showed attempt 1's
    edit-protocol error instead of the refusal Kriya had just ruled. Eight attempts, the run failed, nothing was applied.

A. The Maven-configured entry point must name the application's real main class (package declaration plus type name). Kriya already
   corrects this for a unit that wrote pom.xml (file_resolution.correct_exec_main_class_property); it compared nothing for a unit that
   did not. Now the same two facts are compared without writing: a known mismatch is recorded, and it is a deterministic failure for
   the unit the plan lets write pom.xml. A unit that may not write the pom is given no failure and mutates nothing.
B. A deterministic statement about the candidate becomes the active failure the next request names (retry_strategy). The open-finding
   and failure-family predicate (open_findings.replaces_open_finding) is deliberately unchanged: that discrepancy stays OPEN.

The final contents are judged at the shared commit function: tests/test_java_entrypoint_final_commit_001.py.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from test_enforce_verified_no_change import CONTROLLER, CONTROLLER_SRC, JUDGMENT_CRITERION, _refused, _run

from kriya.analyzer.graph import DependencyGraph
from kriya.policy.filesystem import WriteScopeMode
from kriya.workflow import open_findings
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.file_resolution import (
    ENTRYPOINT_MISMATCH,
    ENTRYPOINT_PENDING,
    ENTRYPOINT_UNVERIFIED,
    ENTRYPOINT_VERIFIED,
    correct_exec_main_class_property,
    exec_main_class_identity,
)
from kriya.workflow.gate_orchestrator import _apply_candidate_pom_corrections, _enforce_entrypoint_identity
from kriya.workflow.state import GenerationState

# ------------------------------------------------------------------ run c1's two artifacts

APP = "src/main/java/ProtocolApplication.java"
C1_CONFIGURED, C1_ACTUAL = "com.example.ProtocolApplication", "ProtocolApplication"


def _pom(main_class_property: str = f"<exec.mainClass>{C1_CONFIGURED}</exec.mainClass>") -> str:
    return ("<project>\n  <modelVersion>4.0.0</modelVersion>\n  <properties>\n    <maven.compiler.source>17</maven.compiler.source>\n"
            f"    {main_class_property}\n  </properties>\n  <build><plugins><plugin>\n    <artifactId>exec-maven-plugin</artifactId>\n"
            "    <configuration><executable>java</executable><arguments><argument>${exec.mainClass}</argument></arguments></configuration>\n"
            "  </plugin></plugins></build>\n</project>\n")


C1_POM = _pom()
C1_APP_SOURCE = ("import jakarta.jms.*;\n\npublic class ProtocolApplication {\n    public static void main(String[] args) throws Exception {\n"
                 "        System.out.println(\"run\");\n    }\n}\n")


def _main_class(tmp_path, path: str, source: str):
    """The fully qualified main class Kriya's own analyzer reads from ``source`` (the one detector; nothing is re-implemented here)."""
    graph = DependencyGraph(str(tmp_path / "graph.db"))
    try:
        return graph.find_java_main_class(path, source)
    finally:
        graph.close()


# ------------------------------------------------------------------ A1. the pure comparison

def test_run_c1_the_configured_entry_point_is_not_the_real_main_class(tmp_path):
    actual = _main_class(tmp_path, APP, C1_APP_SOURCE)
    assert actual == C1_ACTUAL, "a class with no package declaration is in the default package: its name is its fully qualified name"
    identity = exec_main_class_identity(C1_POM, {APP: actual})
    assert (identity.status, identity.configured, identity.actual, identity.source_file) == (ENTRYPOINT_MISMATCH, C1_CONFIGURED, C1_ACTUAL, APP)


def test_a_matching_packaged_class_and_a_matching_default_package_class_are_verified(tmp_path):
    packaged = "package com.example;\n\npublic class ProtocolApplication {\n    public static void main(String[] args) { }\n}\n"
    path = "src/main/java/com/example/ProtocolApplication.java"
    assert exec_main_class_identity(C1_POM, {path: _main_class(tmp_path, path, packaged)}).status == ENTRYPOINT_VERIFIED
    default_pom = _pom("<exec.mainClass>ProtocolApplication</exec.mainClass>")
    assert exec_main_class_identity(default_pom, {APP: _main_class(tmp_path, APP, C1_APP_SOURCE)}).status == ENTRYPOINT_VERIFIED


def test_no_package_convention_is_assumed_the_file_name_decides_nothing(tmp_path):
    source = "package org.demo.tools;\n\npublic class Launcher {\n    public static void main(String[] args) { }\n}\n"
    actual = _main_class(tmp_path, "src/main/java/App.java", source)     # the file is not named after the class or its package
    assert actual == "org.demo.tools.Launcher"
    assert exec_main_class_identity(_pom("<exec.mainClass>org.demo.tools.Launcher</exec.mainClass>"), {"src/main/java/App.java": actual}).status \
        == ENTRYPOINT_VERIFIED
    assert exec_main_class_identity(_pom("<exec.mainClass>App</exec.mainClass>"), {"src/main/java/App.java": actual}).status == ENTRYPOINT_MISMATCH


def test_a_main_class_that_does_not_exist_yet_is_pending_not_a_failure(tmp_path):
    no_main = "public class Protocol {\n    private int protocolVersion;\n}\n"
    assert _main_class(tmp_path, "src/main/java/Protocol.java", no_main) is None
    identity = exec_main_class_identity(C1_POM, {})
    assert (identity.status, identity.configured, identity.actual) == (ENTRYPOINT_PENDING, C1_CONFIGURED, None)


@pytest.mark.parametrize("pom, mains", [
    (_pom(""), {APP: C1_ACTUAL}),                                                                    # no <exec.mainClass> property at all
    (_pom("<exec.mainClass>A</exec.mainClass>\n    <exec.mainClass>B</exec.mainClass>"), {APP: "A"}),   # set twice
    (_pom("<exec.mainClass>${app.main}</exec.mainClass>"), {APP: C1_ACTUAL}),                         # itself a property reference
    (_pom("<exec.mainClass>  </exec.mainClass>"), {APP: C1_ACTUAL}),                                  # empty
])
def test_what_cannot_be_decided_from_the_text_is_unverified_never_verified(pom, mains):
    identity = exec_main_class_identity(pom, mains)
    assert identity.status == ENTRYPOINT_UNVERIFIED and identity.reason


TOOL = "src/main/java/Tool.java"


def test_several_main_classes_none_of_which_has_the_configured_name_is_a_known_mismatch():
    """Owner review 2026-11-19: several main classes must not hide a configured name that is none of them."""
    identity = exec_main_class_identity(C1_POM, {APP: C1_ACTUAL, TOOL: "Tool"})
    assert (identity.status, identity.configured, identity.main_class_count) == (ENTRYPOINT_MISMATCH, C1_CONFIGURED, 2)
    assert identity.actual == "ProtocolApplication, Tool" and identity.source_file == f"{APP}, {TOOL}"
    described = identity.actual_description()
    assert "none of the application's 2 classes with a main method" in described and "Tool" in described and APP in described


def test_several_main_classes_one_of_which_has_the_configured_name_is_verified_not_rejected():
    identity = exec_main_class_identity(C1_POM, {APP: C1_CONFIGURED, TOOL: "Tool"})
    assert (identity.status, identity.actual, identity.source_file, identity.main_class_count) == (ENTRYPOINT_VERIFIED, C1_CONFIGURED, APP, 2)


def test_an_unrelated_class_with_a_main_method_does_not_satisfy_the_configured_entry_point():
    identity = exec_main_class_identity(C1_POM, {"src/main/java/tools/Migrate.java": "tools.Migrate"})
    assert identity.status == ENTRYPOINT_MISMATCH and identity.actual == "tools.Migrate"


def test_the_existing_in_scope_correction_is_unchanged_and_agrees_with_the_comparison():
    corrected = correct_exec_main_class_property(C1_POM, {APP: C1_ACTUAL})
    assert corrected == C1_POM.replace(C1_CONFIGURED, C1_ACTUAL)
    assert exec_main_class_identity(corrected, {APP: C1_ACTUAL}).status == ENTRYPOINT_VERIFIED
    assert correct_exec_main_class_property(corrected, {APP: C1_ACTUAL}) is None              # already equal: no rewrite
    assert correct_exec_main_class_property(C1_POM, {}) is None                               # PENDING: no rewrite
    assert correct_exec_main_class_property(_pom(""), {APP: C1_ACTUAL}) is None               # no property: not this mechanism


# ------------------------------------------------------------------ A2. the candidate gate

def _tree(tmp_path, pom: str = C1_POM):
    (tmp_path / "src/main/java").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pom.xml").write_text(pom)
    (tmp_path / APP).write_text(C1_APP_SOURCE)
    return tmp_path


def _gate(tmp_path, *, scope_mode, allowed, mains=None, pom: str = C1_POM):
    _tree(tmp_path, pom)
    state = GenerationState()
    state.attempt_number = 2
    ctx = SimpleNamespace(worktree_path=str(tmp_path), write_scope_mode=scope_mode, allowed_write_relpaths=list(allowed))
    deps = SimpleNamespace(_build_java_main_class_map=lambda java_files, _ctx: dict(mains if mains is not None else {APP: C1_ACTUAL}))
    return deps, state, ctx


def _mismatch_events(state):
    return [event for event in state.run_events if event.kind == "candidate.entrypoint_identity_mismatch"]


def test_before_the_correction_alone_is_silent_for_a_unit_that_did_not_write_the_pom(tmp_path):
    """Run c1's defect, characterized on the unchanged seam: s7's NO CHANGE attempt wrote nothing, so the correction returned at once."""
    deps, state, ctx = _gate(tmp_path, scope_mode=WriteScopeMode.ALLOWLIST, allowed=["pom.xml"])
    assert state.all_files_written == set() or not state.all_files_written
    _apply_candidate_pom_corrections(deps, state, ctx, ["pom.xml", APP])        # no error, no event: the mismatch is not looked at
    assert (tmp_path / "pom.xml").read_text() == C1_POM and state.run_events == [] and state.gate_outcomes == []


def test_the_unit_that_may_write_the_pom_gets_a_deterministic_failure_naming_both_names(tmp_path):
    """s7 of run c1: pom.xml is its planned file and it wrote nothing. NO CHANGE cannot hide the mismatch."""
    deps, state, ctx = _gate(tmp_path, scope_mode=WriteScopeMode.ALLOWLIST, allowed=["pom.xml"])
    with pytest.raises(QualityGateFailure) as raised:
        _enforce_entrypoint_identity(deps, state, ctx, ["pom.xml", APP])
    failure = raised.value.failure
    assert failure.type == "static_rule_violation" and failure.authority == "authoritative"
    for expected in ("[java_entrypoint_identity]", "pom.xml", f"'{C1_CONFIGURED}'", f"'{C1_ACTUAL}'", APP):
        assert expected in failure.message, expected
    assert failure.authoritative_files == ["pom.xml"], "the validator's own subject is the file this unit may repair"
    assert (tmp_path / "pom.xml").read_text() == C1_POM, "a check, never a mutation"
    (event,) = _mismatch_events(state)
    assert event.details == {"path": "pom.xml", "configured": C1_CONFIGURED, "actual": C1_ACTUAL, "source_file": APP, "main_class_count": 1, "pom_writable_by_this_unit": True}
    assert [outcome["type"] for outcome in state.gate_outcomes] == ["static_rule_violation"]


@pytest.mark.parametrize("scope_mode, allowed", [
    (WriteScopeMode.ALLOWLIST, [APP]),            # s5 of run c1: it owns the class, not the pom
    (WriteScopeMode.DENY_ALL, []),                # a verification-only unit
])
def test_a_unit_that_may_not_write_the_pom_records_the_mismatch_and_changes_nothing(tmp_path, scope_mode, allowed):
    deps, state, ctx = _gate(tmp_path, scope_mode=scope_mode, allowed=allowed)
    _enforce_entrypoint_identity(deps, state, ctx, ["pom.xml", APP])           # no failure: it has no authority over pom.xml
    (event,) = _mismatch_events(state)
    assert event.details["pom_writable_by_this_unit"] is False and (event.details["configured"], event.details["actual"]) == (C1_CONFIGURED, C1_ACTUAL)
    assert state.gate_outcomes == [] and (tmp_path / "pom.xml").read_text() == C1_POM and (tmp_path / APP).read_text() == C1_APP_SOURCE


@pytest.mark.parametrize("pom, mains", [
    (_pom("<exec.mainClass>ProtocolApplication</exec.mainClass>"), {APP: C1_ACTUAL}),     # VERIFIED
    (C1_POM, {}),                                                                         # PENDING: the class is not written yet
    (C1_POM, {APP: C1_CONFIGURED, "src/main/java/Tool.java": "Tool"}),                    # VERIFIED: one of two main classes has the configured name
    (_pom("<exec.mainClass>${app.main}</exec.mainClass>"), {APP: C1_ACTUAL}),             # UNVERIFIED: dynamic
])
def test_verified_pending_and_unverified_are_neither_recorded_as_a_mismatch_nor_failed(tmp_path, pom, mains):
    deps, state, ctx = _gate(tmp_path, scope_mode=WriteScopeMode.ALLOWLIST, allowed=["pom.xml"], mains=mains, pom=pom)
    _enforce_entrypoint_identity(deps, state, ctx, ["pom.xml", APP])
    assert state.run_events == [] and state.gate_outcomes == []


def test_a_project_without_a_pom_is_not_this_checks_subject(tmp_path):
    deps, state, ctx = _gate(tmp_path, scope_mode=WriteScopeMode.ALLOWLIST, allowed=["pom.xml"])
    (tmp_path / "pom.xml").unlink()
    _enforce_entrypoint_identity(deps, state, ctx, [APP])
    assert state.run_events == [] and state.gate_outcomes == []


# ------------------------------------------------------------------ B. the active failure the next request names

MALFORMED = f'<<<KRIYA:FILE path="{CONTROLLER}">>\n{CONTROLLER_SRC}<<<KRIYA:END_FILE>>>\n'      # run c1's attempt-1 shape: ">>" for ">>>"
NO_CHANGE = f'The controller already calls the cached service.\n<<<KRIYA:NO_CHANGE path="{CONTROLLER}">>>\n'


def _c1_retry_sequence(tmp_path):
    """Run c1's s7 sequence through the real unit workflow: a malformed first answer, then NO CHANGE on every retry, for a unit whose only
    criterion is judgment (so the no-change proof is impossible, as in c1)."""
    answers = []

    def answer(_system_prompt):
        answers.append(1)
        return MALFORMED if len(answers) == 1 else NO_CHANGE

    workspace, prompts, events, result = _run(tmp_path, criterion=JUDGMENT_CRITERION, developer_answer=answer)
    failed = [event.details["failure_type"] for event in events if event.kind == "attempt.failed"]
    return workspace, prompts, events, result, failed


def test_the_retry_request_names_the_current_deterministic_refusal_not_the_stale_first_failure(tmp_path):
    workspace, prompts, events, result, failed = _c1_retry_sequence(tmp_path)
    assert result["quality_gates_passed"] is False
    assert failed and failed[0] != "verified_no_change_refused", f"the sequence must begin with an earlier failure, got {failed}"
    assert "verified_no_change_refused" in failed, f"the no-change refusal must occur, got {failed}"
    first_refusal = failed.index("verified_no_change_refused")
    assert first_refusal < len(failed) - 1, f"a retry must follow the first refusal for its request to be judged, got {failed}"
    with_error = [prompt for prompt in prompts if "=== Previous Error to Fix ===" in prompt]
    naming_refusal = [prompt for prompt in with_error if "Failure type: verified_no_change_refused" in prompt]
    assert naming_refusal, ("no retry request names the current refusal; the 'Previous Error' sections name: "
                            + str([prompt.split("=== Previous Error to Fix ===")[1][:60] for prompt in with_error]))
    last = with_error[-1].split("=== Previous Error to Fix ===")[1]
    assert last.lstrip().startswith("Failure type: verified_no_change_refused"), last[:200]
    # the request renders the refusal's own evidence (its code, detail and per-criterion coverage), not the failure's summary line
    assert "ACCEPTANCE_COVERAGE_INCOMPLETE" in last and "not every acceptance criterion is covered by deterministic evidence" in last, last[:400]
    assert "'ac1': 'UNCOVERED'" in last, "which criterion has no deterministic evidence reaches the Developer"
    assert f"Failure type: {failed[0]}" not in last, "the stale first failure is not presented as the active error"


def test_control_with_the_old_selection_the_developer_is_told_the_stale_first_failure(tmp_path, monkeypatch):
    """Negative control for B: the selection rule as it was (a deterministic failure never becomes the active one) reproduces run c1's
    defect on the same sequence. is_candidate_finding is read in retry_strategy only by the new rule."""
    from kriya.workflow import retry_strategy

    monkeypatch.setattr(retry_strategy, "is_candidate_finding", lambda _failure_type: False)
    _workspace, prompts, _events, _result, failed = _c1_retry_sequence(tmp_path)
    assert "verified_no_change_refused" in failed and failed[0] != "verified_no_change_refused", failed
    with_error = [prompt for prompt in prompts if "=== Previous Error to Fix ===" in prompt]
    assert with_error and not [prompt for prompt in with_error if "Failure type: verified_no_change_refused" in prompt], \
        "with the old rule no request ever names the refusal"
    assert f"Failure type: {failed[0]}" in with_error[-1], "the last request still names the first failure (the c1 symptom)"


def test_the_no_change_refusal_itself_is_unchanged_and_still_fails_closed(tmp_path):
    workspace, _prompts, events, result, _failed = _c1_retry_sequence(tmp_path)
    assert result["quality_gates_passed"] is False and result["completion_kind"] is None
    assert (workspace / CONTROLLER).read_text() == CONTROLLER_SRC
    refusal = _refused(events)[0]["refusal"]
    assert refusal["code"] == "ACCEPTANCE_COVERAGE_INCOMPLETE" and refusal["criteria"] == {"ac1": "UNCOVERED"}


def test_the_open_finding_and_failure_family_predicate_is_deliberately_not_changed():
    """OPEN, not fixed (owner ruling): a deterministic candidate finding still does not replace the open finding or open a failure
    family. Only the failure the next request names changed."""
    refusal = SimpleNamespace(type="verified_no_change_refused", authority="deterministic")
    assert open_findings.is_candidate_finding("verified_no_change_refused") is True
    assert open_findings.replaces_open_finding(refusal) is False and open_findings.may_open_failure_family(refusal) is False
    for authority in ("advisory", "auxiliary", "something-else"):
        other = SimpleNamespace(type="verified_no_change_refused", authority=authority)
        assert open_findings.replaces_open_finding(other) is False
    assert open_findings.is_candidate_finding("plan_scope_conflict") is False, "an operational deterministic failure never becomes the active one"
