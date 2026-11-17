"""S7 (C-03 / R-03): APPROVAL MUST BIND TO THE EXACT FINAL MUTATION SUBJECT.

MEASURED before the fix (evidence ~/kriya-evidence/s7-7370d76/reproducer_pre_fix_7370d76.txt): in a human-in-the-loop enforce run a file approved
for one unit was changed in the plan candidate afterwards and the terminal commit applied the never-shown bytes; no approval carried an identity
and the terminal commit asked nobody.

The rule under test (owner ruling 2026-11-17): an approval is sufficient iff an existing apply-capable approval is bound to the exact final
mutation subject being applied. One owner decides it (kriya/policy/approval.py::MutationApproval / mutation_approval_refusal), the commit seam
enforces it for every shape (kriya/workflow/terminal_commit.py), the enforce terminal obtains or reuses it for the cumulative batch
(kriya/workflow/commit_service.py::approve_terminal_mutation).
"""
import asyncio
import dataclasses
import inspect
import os
import subprocess

import _enforce_unit_harness as harness
import pytest
from _fake_static_analysis import DISABLED_STATIC_ANALYSIS, no_approval_needed
from test_s6_enforce_closer_parity import (  # noqa: F401 - scripted_validator is a pytest fixture
    PYTHON,
    UNQUOTED,
    _autonomy,
    _repo_and_candidate,
    _terminal_outcomes,
    scripted_validator,
)

from kriya.config.config import AppConfig
from kriya.control.run_coordinator import (
    begin_mutating_run,
    current_run_context,
    owning_run_commits,
    transition_mutating_run,
)
from kriya.control.run_record import RunLifecycle
from kriya.policy.approval import (
    APPROVAL_NOT_APPLY_CAPABLE,
    APPROVAL_REQUIRED_NOT_GRANTED,
    APPROVAL_REVIEW_EXPIRED,
    APPROVAL_SUBJECT_MISSING,
    APPROVAL_SUBJECT_STALE,
    APPROVAL_WRONG_RUN_OR_ROOT,
    SCOPE_REVIEW_ONLY,
    ApprovalOutcome,
    MutationApproval,
    MutationApprovalLedger,
)
from kriya.workflow import commit_service, requirement_closers
from kriya.workflow.commit_service import approve_terminal_mutation
from kriya.workflow.edit_safety import read_file_revision
from kriya.workflow.plan_schema import EngineeringPlan
from kriya.workflow.terminal_commit import CandidateFile, commit_terminal_candidate, materialize_candidate
from kriya.workflow.verification_binding import bind_candidate

SERVICE, OTHER = "shop/service.py", "shop/other.py"
BASE = "def page_size():\n    return 5\n"
APPROVED = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE\n"
NEVER_SHOWN = "PAGE_SIZE = 5\n\n\ndef page_size():\n    return PAGE_SIZE * 1000  # never shown to anyone\n"


# ------------------------------------------------------------------------------------------------ the application gate (every commit shape)

def _batch(tmp_path, content="v1\n"):
    workspace, candidate = tmp_path / "ws", tmp_path / "candidate"
    for folder in (workspace, candidate):
        folder.mkdir(exist_ok=True)
    (workspace / "app.py").write_text("v0\n")
    (candidate / "app.py").write_text(content)
    writes = materialize_candidate(str(candidate), str(workspace), [CandidateFile("app.py", read_file_revision(str(workspace / "app.py")))])
    return str(workspace), writes


def _granted(writes, workspace, **changes):
    binding = bind_candidate(writes, workspace)
    approval = MutationApproval(subject_digest=binding.digest, root=os.path.realpath(workspace), run_id="run-1", required=True,
                                triggers=("human_in_the_loop",), outcome=ApprovalOutcome.approve(), approval_id="a1")
    return dataclasses.replace(approval, **changes)


def _commit(workspace, writes, approval, run_id="run-1"):
    return commit_terminal_candidate(writes, workspace_path=workspace, transaction_id="tx", static_analysis=DISABLED_STATIC_ANALYSIS,
                                     verified_candidate=bind_candidate(writes, workspace), approval=approval, run_id=run_id)


def test_a_granted_approval_of_exactly_this_batch_applies_it_and_the_commit_cycle_keeps_the_decision(tmp_path):
    workspace, writes = _batch(tmp_path)
    with begin_mutating_run(workspace) as context:
        transition_mutating_run(current_run_context(), RunLifecycle.RUNNING)
        approval = _granted(writes, workspace, run_id=context.run_id)
        outcome = _commit(workspace, writes, approval, run_id=context.run_id)
        _run_id, cycles = owning_run_commits(workspace)
    assert outcome.committed and open(os.path.join(workspace, "app.py")).read() == "v1\n"
    assert cycles[-1]["approval"] == {"approval_id": "a1", "subject_digest": bind_candidate(writes, workspace).digest, "required": True,
                                      "decision": "approved", "scope": "apply", "triggers": ["human_in_the_loop"]}


def _other_batch_approval(tmp_path, writes, workspace):
    """An approval granted for candidate A (other bytes of the same file) while B is what is committed."""
    other = tmp_path / "candidate_a"
    other.mkdir()
    (other / "app.py").write_text("the approved bytes\n")
    approved_writes = materialize_candidate(str(other), workspace, [CandidateFile("app.py", read_file_revision(os.path.join(workspace, "app.py")))])
    return _granted(approved_writes, workspace)


REFUSALS = {
    "missing": (lambda tmp_path, writes, workspace: None, APPROVAL_SUBJECT_MISSING),
    "no_subject": (lambda tmp_path, writes, workspace: _granted(writes, workspace, subject_digest=None), APPROVAL_SUBJECT_MISSING),
    "approved_a_applying_b": (_other_batch_approval, APPROVAL_SUBJECT_STALE),
    "not_required_decided_for_another_batch": (
        lambda tmp_path, writes, workspace: dataclasses.replace(_other_batch_approval(tmp_path, writes, workspace), required=False, outcome=None),
        APPROVAL_SUBJECT_STALE),
    "required_and_nobody_approved": (lambda tmp_path, writes, workspace: _granted(writes, workspace, outcome=None), APPROVAL_REQUIRED_NOT_GRANTED),
    "required_and_declined": (lambda tmp_path, writes, workspace: _granted(writes, workspace, outcome=ApprovalOutcome.decline()), APPROVAL_REQUIRED_NOT_GRANTED),
    "required_and_unavailable": (
        lambda tmp_path, writes, workspace: _granted(writes, workspace, outcome=ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED)),
        APPROVAL_REQUIRED_NOT_GRANTED),
    "review_only_scope": (lambda tmp_path, writes, workspace: _granted(writes, workspace, scope=SCOPE_REVIEW_ONLY), APPROVAL_NOT_APPLY_CAPABLE),
    "another_run": (lambda tmp_path, writes, workspace: _granted(writes, workspace, run_id="another-run"), APPROVAL_WRONG_RUN_OR_ROOT),
    "another_project_root": (lambda tmp_path, writes, workspace: _granted(writes, workspace, root=str(tmp_path / "elsewhere")), APPROVAL_WRONG_RUN_OR_ROOT),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_the_commit_refuses_every_approval_that_is_not_a_granted_apply_capable_decision_for_exactly_this_batch(tmp_path, case):
    workspace, writes = _batch(tmp_path)
    build, code = REFUSALS[case]
    outcome = _commit(workspace, writes, build(tmp_path, writes, workspace))
    assert (outcome.committed, outcome.workspace_state, outcome.reason_code) == (False, "UNCHANGED", code)
    assert open(os.path.join(workspace, "app.py")).read() == "v0\n", "zero mutation before the gate succeeds"
    assert not os.path.isdir(os.path.join(workspace, ".kriya", "commits")) or not os.listdir(os.path.join(workspace, ".kriya", "commits"))


def test_a_decision_that_no_approval_is_required_applies_only_the_batch_it_was_made_for(tmp_path):
    workspace, writes = _batch(tmp_path)
    assert _commit(workspace, writes, no_approval_needed(writes, workspace, run_id="run-1")).committed


def test_every_production_commit_hands_the_seam_its_approval_decision_and_run():
    for module, owner in ((commit_service, commit_service.commit_verified_candidate),
                          (__import__("kriya.workflow.stage_apply", fromlist=["x"]), None)):
        source = inspect.getsource(owner or module.apply_terminal_candidate)
        call = source[source.index("commit_terminal_candidate("):]
        assert "approval=" in call and "run_id=" in call, module.__name__


# ------------------------------------------------------------------------------------------------ the canonical subject (one digest per mutation)

def _shape(tmp_path, name, *, base, content="new\n", declared, mode=0o644, relpath="app.py", delete=False):
    """One write as the UNIT path builds it (base existence not declared: ``declared=None``) or as the TERMINAL builds it (declared)."""
    workspace, candidate = tmp_path / name / "ws", tmp_path / name / "candidate"
    for folder in (workspace, candidate):
        (folder / relpath).parent.mkdir(parents=True, exist_ok=True)
    if base is not None:
        (workspace / relpath).write_text(base)
        os.chmod(workspace / relpath, mode)
    if not delete:
        (candidate / relpath).write_text(content)
        os.chmod(candidate / relpath, mode)
    writes = materialize_candidate(str(candidate), str(workspace), [
        CandidateFile(relpath, read_file_revision(str(workspace / relpath)), expected_base_exists=declared, delete=delete)])
    return bind_candidate(writes, str(workspace))


@pytest.mark.parametrize("base,operation", [("old\n", "modify"), (None, "create")])
def test_the_same_logical_mutation_has_one_digest_at_the_unit_and_at_the_terminal(tmp_path, base, operation):
    unit = _shape(tmp_path, "unit", base=base, declared=None)
    terminal = _shape(tmp_path, "terminal", base=base, declared=base is not None)
    assert unit.digest == terminal.digest and [entry[1] for entry in unit.entries] == [operation] == [entry[1] for entry in terminal.entries]


def test_every_material_difference_is_another_subject(tmp_path):
    subject = _shape(tmp_path, "subject", base="old\n", declared=None).digest
    others = {
        "a byte": _shape(tmp_path, "byte", base="old\n", declared=None, content="new!\n"),
        "the path": _shape(tmp_path, "path", base="old\n", declared=None, relpath="pkg/app.py"),
        "the mode": _shape(tmp_path, "mode", base="old\n", declared=None, mode=0o755),
        "the base revision": _shape(tmp_path, "base", base="older\n", declared=None),
        "create instead of modify (no base)": _shape(tmp_path, "create", base=None, declared=None),
        "create instead of modify (an existing EMPTY base has the missing file's revision)": _shape(tmp_path, "empty", base="", declared=None),
        "delete": _shape(tmp_path, "delete", base="old\n", declared=True, delete=True),
    }
    digests = {name: binding.digest for name, binding in others.items()}
    assert subject not in digests.values() and len(set(digests.values())) == len(digests), digests
    # the last two differ ONLY by the semantic operation: same bytes, same base revision (empty == missing), same mode
    create, empty = others["create instead of modify (no base)"].entries[0], others["create instead of modify (an existing EMPTY base has the missing file's revision)"].entries[0]
    assert (create[1], empty[1]) == ("create", "modify") and create[2:5] == empty[2:5]


def test_a_declared_base_existence_is_the_authoritative_fact_the_root_is_only_read_when_nothing_declares_it(tmp_path):
    """The terminal declares base existence from the plan's final action. A plan that CREATES a path which exists in the root is a create
    subject (the commit then refuses the existence conflict); it is never silently rebound as the modify the root would suggest."""
    declared_create = _shape(tmp_path, "declared_create", base="old\n", declared=False)
    declared_modify = _shape(tmp_path, "declared_modify", base="old\n", declared=True)
    assert [entry[1] for entry in declared_create.entries] == ["create"] and [entry[1] for entry in declared_modify.entries] == ["modify"]
    assert declared_create.digest != declared_modify.digest
    # and the other direction: a plan that MODIFIES a path the root does not hold is a modify subject (the commit refuses the missing base),
    # never the create the root alone would suggest
    declared_modify_of_missing = _shape(tmp_path, "declared_modify_of_missing", base=None, declared=True)
    undeclared_on_missing = _shape(tmp_path, "undeclared_on_missing", base=None, declared=None)
    assert [entry[1] for entry in declared_modify_of_missing.entries] == ["modify"] and [entry[1] for entry in undeclared_on_missing.entries] == ["create"]
    assert declared_modify_of_missing.digest != undeclared_on_missing.digest


def test_the_ledger_matches_the_whole_subject_digest_and_nothing_less(tmp_path):
    workspace, writes = _batch(tmp_path)
    approval = _granted(writes, workspace)
    ledger = MutationApprovalLedger()
    ledger.record(approval)
    digest = approval.subject_digest
    assert ledger.covering(digest, root=approval.root) is approval
    for near in (digest[:-1] + ("0" if digest[-1] != "0" else "1"), digest[:32] + "0" * 32, digest[0] + "0" * 63, digest[:-1], ""):
        assert near != digest and ledger.covering(near, root=approval.root) is None, near
    assert ledger.covering(digest, root=approval.root + "-elsewhere") is None


# ------------------------------------------------------------------------------------------------ the enforce terminal decision (cumulative batch)

def _plan(*files):
    subtasks = [{"id": f"s{index}", "description": f"edit {path}", "execution_method": "model", "planned_files": [{"path": path, "action": action}],
                 "depends_on": [f"s{index - 1}"] if index > 1 else [], "provides": [f"p_s{index}"], "relevant_global_invariant_ids": ["gi1"],
                 "verification": [{"type": "tool", "tool_name": "compile", "description": "compile"}]}
                for index, (path, action) in enumerate(files, start=1)]
    return EngineeringPlan.model_validate({"plan_id": "p", "kind": "task", "global_invariants": [{"id": "gi1", "statement": "x"}],
                                           "acceptance_criteria": [], "subtasks": subtasks})


def _terminal(tmp_path, *, mode="human-in-the-loop", threshold=None, candidate=None, ledger=None, callback="record", answer=True, base=BASE):
    """-> (approval, stop, calls): approve_terminal_mutation on a real two-file candidate over a real workspace."""
    workspace, root = tmp_path / "ws", tmp_path / "candidate"
    for folder in (workspace / "shop", root / "shop"):
        folder.mkdir(parents=True, exist_ok=True)
    (workspace / SERVICE).write_text(base)
    for path, text in (candidate or {SERVICE: APPROVED, OTHER: "OTHER = 1\n"}).items():
        (root / path).write_text(text)
    autonomy = AppConfig().autonomy
    autonomy.mode = mode
    if threshold is not None:
        autonomy.risk_threshold_lines = threshold
    calls = []

    def ask(files, reason):
        calls.append(([f["filepath"] for f in files], "".join(f["content"] for f in files), reason, [len(f["content"].splitlines()) for f in files]))
        return answer

    plan = _plan((SERVICE, "modify"), (OTHER, "create"))
    revisions = {path: read_file_revision(str(workspace / path)) for path in (SERVICE, OTHER)}
    approval, stop = asyncio.run(approve_terminal_mutation(
        plan=plan, candidate_root=str(root), workspace_path=str(workspace), original_plan_revisions=revisions, run_id="run-1",
        autonomy=autonomy, process_profile_requires_review=False, ledger=ledger, callback=ask if callback == "record" else callback))
    writes = commit_service.plan_terminal_writes(plan, str(root), str(workspace), revisions)
    return approval, stop, calls, writes, str(workspace), str(root)


def test_human_in_the_loop_asks_once_for_the_complete_change_and_binds_the_answer_to_it(tmp_path):
    approval, stop, calls, writes, workspace, _root = _terminal(tmp_path)
    assert stop is None and [call[0] for call in calls] == [[OTHER, SERVICE]]
    assert "+    return PAGE_SIZE" in calls[0][1] and "+OTHER = 1" in calls[0][1] and "complete change" in calls[0][2]
    assert approval.required and approval.granted and approval.triggers == ("human_in_the_loop",)
    assert approval.subject_digest == bind_candidate(writes, workspace).digest and approval.root == os.path.realpath(workspace)
    assert _commit(workspace, writes, approval).committed


def test_the_size_measure_is_taken_on_the_complete_change_so_a_plan_of_small_units_cannot_lower_it(tmp_path):
    """GAP-A3-05: in guardrails mode each unit's diff is below the threshold; the cumulative change is above it and asks once."""
    _approval, _stop, measured, _writes, _workspace, _root = _terminal(tmp_path / "measure")
    largest_unit = max(measured[0][3])                       # the larger of the two per-file diffs: no single unit exceeds the threshold
    approval, stop, calls, _writes, _workspace, _root = _terminal(tmp_path / "run", mode="guardrails", threshold=largest_unit)
    assert stop is None and len(calls) == 1 and approval.triggers == ("diff_size",)
    assert max(calls[0][3]) <= largest_unit < sum(calls[0][3])


def test_a_mode_and_size_that_require_no_approval_ask_nobody_and_still_bind_the_decision_to_the_batch(tmp_path):
    approval, stop, calls, writes, workspace, _root = _terminal(tmp_path, mode="guardrails")
    assert stop is None and calls == [] and not approval.required and approval.subject_digest == bind_candidate(writes, workspace).digest


def test_a_decline_and_an_unobtainable_decision_stop_with_the_existing_categories_and_no_approval(tmp_path):
    for index, (answer, callback, category) in enumerate((
            (False, "record", "human_rejected"),
            (ApprovalOutcome.unavailable_because(APPROVAL_REVIEW_EXPIRED), "record", "approval_required"),
            ("not a decision", "record", "approval_required"),
            (True, None, "approval_required"))):
        approval, stop, _calls, _writes, _workspace, _root = _terminal(tmp_path / str(index), answer=answer, callback=callback)
        assert approval is None and stop["failure_category"] == category, (answer, callback, stop)


def _unit_approval(tmp_path, base=BASE, **changes):
    """What a unit's gate records: a granted approval of the unit's batch, rooted at the plan candidate it was committed into."""
    approval, _stop, _calls, _writes, _workspace, root = _terminal(tmp_path / "unit", base=base)
    return dataclasses.replace(approval, root=os.path.realpath(root), approval_id="unit-approval", **changes), root


def test_an_exactly_covering_apply_capable_approval_is_reused_and_nobody_is_asked_twice(tmp_path):
    unit, _root = _unit_approval(tmp_path)
    ledger = MutationApprovalLedger()
    ledger.record(dataclasses.replace(unit, root=os.path.realpath(str(tmp_path / "run" / "candidate"))))
    approval, stop, calls, writes, workspace, _root = _terminal(tmp_path / "run", ledger=ledger)
    assert stop is None and calls == [], "the person already approved exactly this change"
    assert approval.approval_id == "unit-approval" and approval.granted and approval.subject_digest == bind_candidate(writes, workspace).digest
    assert _commit(workspace, writes, approval).committed


@pytest.mark.parametrize("case", ["a_byte_changed_after_the_approval", "review_only_scope", "rooted_in_another_candidate", "another_base", "declined"])
def test_an_approval_that_does_not_cover_the_final_subject_exactly_is_not_reused_the_person_is_asked(tmp_path, case):
    unit, _root = _unit_approval(tmp_path, base="def page_size():\n    return 6\n" if case == "another_base" else BASE)
    here = os.path.realpath(str(tmp_path / "run" / "candidate"))
    recorded, candidate = dataclasses.replace(unit, root=here), None
    if case == "a_byte_changed_after_the_approval":
        candidate = {SERVICE: NEVER_SHOWN, OTHER: "OTHER = 1\n"}
    elif case == "review_only_scope":
        recorded = dataclasses.replace(recorded, scope=SCOPE_REVIEW_ONLY)
    elif case == "rooted_in_another_candidate":
        recorded = dataclasses.replace(recorded, root=os.path.realpath(str(tmp_path / "another-run" / "candidate")))
    elif case == "another_base":      # the same final bytes, approved as a change to OTHER base bytes: another subject digest
        pass
    elif case == "declined":
        recorded = dataclasses.replace(recorded, outcome=ApprovalOutcome.decline())
    ledger = MutationApprovalLedger()
    ledger.record(recorded)
    approval, stop, calls, _writes, _workspace, _root = _terminal(tmp_path / "run", ledger=ledger, candidate=candidate)
    assert stop is None and len(calls) == 1, case
    assert approval.approval_id != "unit-approval" and approval.granted
    if candidate is not None:
        assert "never shown to anyone" in calls[0][1], "the changed bytes are what the person is shown"


# ------------------------------------------------------------------------------------------------ through the real enforce controller

def _two_unit_plan():
    return _plan((SERVICE, "modify"), (OTHER, "create"))


def _enforce(tmp_path, *, plan, units, decide):
    """A human-in-the-loop enforce run; ``decide(call_index, shown, plan_candidate)`` answers each approval-callback call."""
    shown = []

    def plan_candidate():
        """The run's plan sandbox: the worktree of the workspace repository that holds the accumulated units and nothing of the unit in flight."""
        listed = subprocess.run(["git", "-C", str(tmp_path / "ws"), "worktree", "list", "--porcelain"], check=True, capture_output=True, text=True).stdout
        roots = [line.split(" ", 1)[1] for line in listed.splitlines() if line.startswith("worktree ")][1:]
        (root,) = [r for r in roots if os.path.exists(os.path.join(r, SERVICE)) and open(os.path.join(r, SERVICE)).read() == APPROVED
                   and not os.path.exists(os.path.join(r, OTHER))] or [None]
        return root

    def callback(files, reason):
        del reason
        shown.append({f["filepath"]: f["content"] for f in files})
        return decide(len(shown), shown[-1], plan_candidate)

    run = harness.run_enforce(tmp_path, plan=plan, units=units, base_files={SERVICE: BASE, "shop/__init__.py": ""}, callback=callback)
    return run, shown


def _change_approved_file_while_unit_two_is_reviewed(decline_the_complete_change):
    def decide(call, _shown, plan_candidate):
        if call == 2:       # s1 was approved; its file now changes in the plan candidate (stands for any later writer: a repair, a reconciliation)
            root = plan_candidate()
            assert root is not None, "the plan candidate holding exactly what was approved for s1 was not found"
            with open(os.path.join(root, SERVICE), "w") as handle:
                handle.write(NEVER_SHOWN)
        return not (call == 3 and decline_the_complete_change)
    return decide


def test_the_measured_stale_approval_bytes_changed_after_a_unit_approval_are_never_applied_under_it(tmp_path):
    """THE REPRODUCER. Before the fix: two approval calls, and the never-shown bytes were committed."""
    run, shown = _enforce(tmp_path, plan=_two_unit_plan(), units={"s1": (SERVICE, APPROVED), "s2": (OTHER, "OTHER = 1\n")},
                          decide=_change_approved_file_while_unit_two_is_reviewed(decline_the_complete_change=True))
    assert [sorted(call) for call in shown] == [[SERVICE], [OTHER], [OTHER, SERVICE]], "the changed final subject is put to the person"
    assert "never shown to anyone" in shown[2][SERVICE] and "never shown to anyone" not in shown[0][SERVICE]
    assert (run.workspace / SERVICE).read_text() == BASE and not (run.workspace / OTHER).exists(), "declined: zero mutation"
    result = run.result.legacy_result
    assert result["status"] == "failed" and result["failure_category"] == "human_rejected" and result["quality_gates_passed"] is False


def test_the_changed_final_subject_is_applied_only_under_an_approval_that_was_shown_exactly_it(tmp_path):
    run, shown = _enforce(tmp_path, plan=_two_unit_plan(), units={"s1": (SERVICE, APPROVED), "s2": (OTHER, "OTHER = 1\n")},
                          decide=_change_approved_file_while_unit_two_is_reviewed(decline_the_complete_change=False))
    assert len(shown) == 3 and "never shown to anyone" in shown[2][SERVICE]
    assert (run.workspace / SERVICE).read_text() == NEVER_SHOWN and (run.workspace / OTHER).read_text() == "OTHER = 1\n"


def test_one_unit_whose_approved_change_is_the_whole_change_asks_exactly_once(tmp_path):
    run, shown = _enforce(tmp_path, plan=_plan((SERVICE, "modify")), units={"s1": (SERVICE, APPROVED)}, decide=lambda *_a: True)
    assert [sorted(call) for call in shown] == [[SERVICE]], "the unit's approval covers the final subject exactly: no second prompt"
    assert (run.workspace / SERVICE).read_text() == APPROVED


# ------------------------------------------------------------------------------------------------ verification authority is not mutation approval

def test_the_sealed_terminal_verifier_run_asks_nobody_in_human_in_the_loop_mode(tmp_path, scripted_validator):  # noqa: F811 - pytest fixture
    """Owner ruling 2026-11-17 (closes ENFORCE-RUNTIME-STDOUT-SUBJECT-AND-REVIEW-LIMITS-001, limit 2): the command is derived by trusted
    verifier logic from the sealed statement and runs isolated on the final candidate, so it needs no second approval. The closer takes no
    approval channel at all; the mode changes nothing about what runs."""
    assert not {"approval_callback", "callback", "approval"} & set(inspect.signature(requirement_closers.terminal_runtime_stdout_outcomes).parameters)
    autonomy = _autonomy()
    autonomy.mode = "human-in-the-loop"
    workspace, root = _repo_and_candidate(tmp_path)
    (outcome,) = _terminal_outcomes(UNQUOTED, workspace, root, autonomy=autonomy)
    assert outcome["success"] is True and scripted_validator.built[0].ran[0]["commands"] == [[PYTHON, "hello.py"]]
