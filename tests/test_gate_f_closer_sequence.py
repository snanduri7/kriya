"""Gate F (Wave 6): one owner of the original-requirement closer sequence, and the authority pins the audit relies on.

The audit (handover/WAVE3_LEDGER_2026-10-30.md section 34) found the same 11-closer sequence, in the same order, assembled
twice: ``development_loop._close_and_check_requirements`` (the direct/milestone pre-apply gate and each enforce unit's gate)
and ``TerminalGateService._requirement_gap`` (the enforce terminal gate). The leaf closers were already shared; the
SEQUENCE, its order and the concatenation order were not. ``requirement_closers.run_requirement_closers`` is now the one
owner of those three things. Each call site still supplies its own scope-specific arguments (revision, candidate paths,
toolchain authority, tree binding, baseline source) and its own closer callables, so the documented patch seams do not move,
and each site keeps its own blocking decision and result contract.

Also pinned here (cheap structural negative controls for the rest of the audit's classification):

- the real-workspace transaction has exactly two callers (the enforce ``commit_service`` and the direct ``stage_apply``),
  and ``commit_verified_candidate`` has exactly one (``enforce_loop``);
- the non-requirement terminal-required obligations the terminal service aggregates are created only by enforce-only
  modules, so the direct/milestone path (which has no structured plan) cannot silently carry one it never checks.
"""
import asyncio
import re
from pathlib import Path

import pytest

from kriya.workflow import requirement_closers
from kriya.workflow.requirement_closers import (
    CLOSER_SEQUENCE,
    CONTRACT_CLOSERS,
    closer_call,
    no_closures,
    run_requirement_closers,
)

REPO = Path(__file__).resolve().parent.parent


def _run(coro):
    return asyncio.run(coro)


def _recording_bindings(log, *, results=None):
    """Bindings whose thunk and closer both leave a trace, so evaluation timing and order are observable."""
    results = results or {}

    def binding(name):
        def thunk():
            log.append(("evaluate", name))
            return closer_call(closer, name, extra=name)

        def closer(label, extra=None):
            log.append(("run", label))
            return list(results.get(name, [{"requirement": f"{name}-req", "closed": True, "extra": extra}]))
        return thunk
    return {name: binding(name) for name in CLOSER_SEQUENCE}


def test_the_sequence_is_the_eleven_closers_in_the_order_both_sites_ran_them():
    assert CLOSER_SEQUENCE == (
        "mutation_scope", "acceptance", "authority_bundle", "derived_examples", "api_preservation", "test_addition",
        "documentation", "named_tests", "test_immutability", "file_immutability", "suite_preservation")
    assert CONTRACT_CLOSERS == ("authority_bundle", "derived_examples", "api_preservation", "test_addition", "documentation")
    assert set(CONTRACT_CLOSERS) <= set(CLOSER_SEQUENCE)


def test_closers_run_in_sequence_and_the_attempts_concatenate_in_that_order():
    log = []
    run = _run(run_requirement_closers(_recording_bindings(log)))
    assert [name for kind, name in log if kind == "run"] == list(CLOSER_SEQUENCE)
    assert [attempt["requirement"] for attempt in run.attempts] == [f"{name}-req" for name in CLOSER_SEQUENCE]
    assert list(run.by_closer) == list(CLOSER_SEQUENCE)


def test_each_closers_arguments_are_read_immediately_before_it_runs_not_when_the_bindings_are_built():
    """The suite closer reads run state an earlier closer can change (the lazy baseline capture): evaluating every
    argument up front would silently change what it sees."""
    log = []
    state = {"baseline": "not-captured"}
    bindings = _recording_bindings(log)

    def early():
        state["baseline"] = "captured-by-an-earlier-closer"
        return []

    seen = []

    def late_thunk():
        seen.append(state["baseline"])
        return closer_call(lambda: [])

    bindings["mutation_scope"] = lambda: closer_call(early)
    bindings["suite_preservation"] = late_thunk
    _run(run_requirement_closers(bindings))
    assert seen == ["captured-by-an-earlier-closer"]


def test_a_thunk_is_evaluated_after_the_previous_closer_finished():
    log = []
    _run(run_requirement_closers(_recording_bindings(log)))
    expected = []
    for name in CLOSER_SEQUENCE:
        expected += [("evaluate", name), ("run", name)]
    assert log == expected


@pytest.mark.parametrize("dropped", CLOSER_SEQUENCE)
def test_a_site_that_forgets_a_closer_fails_loudly(dropped):
    bindings = _recording_bindings([])
    del bindings[dropped]
    with pytest.raises(ValueError, match=dropped):
        _run(run_requirement_closers(bindings))


def test_a_site_that_adds_an_unknown_closer_fails_loudly():
    bindings = _recording_bindings([])
    bindings["invented"] = lambda: closer_call(lambda: [])
    with pytest.raises(ValueError, match="invented"):
        _run(run_requirement_closers(bindings))


def test_a_closer_exception_reaches_the_caller_as_itself_and_no_later_closer_runs():
    log = []
    bindings = _recording_bindings(log)

    class Refused(RuntimeError):
        pass

    def broken():
        raise Refused("typed refusal")

    bindings["acceptance"] = lambda: closer_call(broken)
    with pytest.raises(Refused):
        _run(run_requirement_closers(bindings))
    assert [name for kind, name in log if kind == "run"] == ["mutation_scope"]  # nothing after the failure ran


def test_on_closed_sees_each_group_right_after_its_closer():
    log = []
    seen = []
    _run(run_requirement_closers(_recording_bindings(log), on_closed=lambda name, closures: seen.append(
        (name, len(closures), len(log)))))
    assert [name for name, _, _ in seen] == list(CLOSER_SEQUENCE)
    assert [at for _, _, at in seen] == [2 * (index + 1) for index in range(len(CLOSER_SEQUENCE))]


def test_no_closures_is_an_empty_call_so_a_site_can_skip_a_group_without_a_closer_call():
    log = []
    bindings = _recording_bindings(log)
    for name in CONTRACT_CLOSERS:
        bindings[name] = lambda: closer_call(no_closures)
    run = _run(run_requirement_closers(bindings))
    assert all(run.by_closer[name] == [] for name in CONTRACT_CLOSERS)
    assert not [name for kind, name in log if kind == "run" and name in CONTRACT_CLOSERS]


# --- structural: both sites go through the one owner ---------------------------------------------------------------

_SITES = ("kriya/workflow/development_loop.py", "kriya/workflow/terminal_gate_service.py")


@pytest.mark.parametrize("site", _SITES)
def test_neither_site_runs_a_requirement_closer_directly(site):
    source = (REPO / site).read_text(encoding="utf-8")
    assert "run_requirement_closers(" in source, site
    assert not re.search(r"to_thread\(\s*(?:deps\.)?close_requirements_", source), (
        f"{site} runs a closer outside the one sequence owner")


@pytest.mark.parametrize("site", _SITES)
def test_each_site_binds_exactly_the_canonical_closer_names(site):
    source = (REPO / site).read_text(encoding="utf-8")
    for name in CLOSER_SEQUENCE:
        assert f'"{name}"' in source, (site, name)


def test_the_sequence_has_one_definition():
    owners = [path.relative_to(REPO).as_posix() for path in (REPO / "kriya").rglob("*.py")
              if re.search(r"^CLOSER_SEQUENCE\b[^\n]*= \(", path.read_text(encoding="utf-8"), re.MULTILINE)]
    assert owners == ["kriya/workflow/requirement_closers.py"]
    assert requirement_closers.CLOSER_SEQUENCE is CLOSER_SEQUENCE


# --- the commit authority pins -------------------------------------------------------------------------------------

def _call_sites(token, definition):
    found = set()
    for path in (REPO / "kriya").rglob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if token in line and definition not in line and not stripped.startswith(("#", '"', "'")):
                found.add(path.relative_to(REPO).as_posix())
    return found


def test_the_real_workspace_transaction_has_exactly_the_two_documented_callers():
    assert _call_sites("commit_terminal_candidate(", "def commit_terminal_candidate") == {
        "kriya/workflow/commit_service.py",     # enforce: after the TerminalGateService report is commit-eligible
        "kriya/workflow/stage_apply.py",        # direct/milestone: after every pre-apply gate passed (PRD-007 shared seam)
    }


def test_the_enforce_commit_entry_has_exactly_one_caller():
    assert _call_sites("commit_verified_candidate(", "def commit_verified_candidate") == {"kriya/workflow/enforce_loop.py"}


# --- the obligation-creator tripwire -------------------------------------------------------------------------------

_ENFORCE_ONLY_CREATORS = frozenset({
    "kriya/workflow/plan_validation.py", "kriya/workflow/planning_request.py", "kriya/workflow/owner_recovery.py",
    "kriya/workflow/enforce_loop.py", "kriya/workflow/terminal_gate_service.py",
})
# The direct engine creates two kinds of terminal-required record. ORIGINAL_REQUIREMENT is excluded from
# ``unresolved_terminal_obligations`` and judged by ``blocking_requirements`` on every path. FUTURE_OWNER_VERIFICATION
# is only recorded under ``structured_plan is not None and current_subtask_id and obligation_ledger is not None``
# (development_loop.py, the resolve_future_owner_verification_deferral call), i.e. under enforce.
# migration.py creates MIGRATION_COMPLETION: one decider (find_migration_incomplete), judged by the attempt-level migration
# gate on the direct path and by the terminal service's migration gate on enforce.
_DIRECT_ENGINE_CREATORS = frozenset({
    "kriya/workflow/requirements.py", "kriya/workflow/workflow.py", "kriya/workflow/migration.py"})


def test_terminal_required_obligations_are_created_only_where_the_terminal_service_or_the_requirement_policy_judges_them():
    creators = {path.relative_to(REPO).as_posix() for path in (REPO / "kriya").rglob("*.py")
                if "terminal_required=True" in path.read_text(encoding="utf-8")}
    assert creators == _ENFORCE_ONLY_CREATORS | _DIRECT_ENGINE_CREATORS, sorted(creators ^ (
        _ENFORCE_ONLY_CREATORS | _DIRECT_ENGINE_CREATORS))


def test_the_enforce_only_planning_modules_are_imported_by_the_controller_alone():
    importers = set()
    for path in (REPO / "kriya").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if re.search(r"from kriya\.workflow\.(planning_request|plan_validation|owner_recovery) import|"
                     r"import kriya\.workflow\.(planning_request|plan_validation|owner_recovery)", source):
            importers.add(path.relative_to(REPO).as_posix())
    direct_path = {"kriya/workflow/development_loop.py", "kriya/workflow/stage_apply.py",
                   "kriya/workflow/gate_orchestrator.py", "kriya/workflow/milestones.py"}
    assert not importers & direct_path, sorted(importers & direct_path)


def test_the_future_owner_deferral_is_gated_on_a_structured_plan():
    source = (REPO / "kriya/workflow/development_loop.py").read_text(encoding="utf-8")
    marker = "if structured_plan is not None and current_subtask_id and obligation_ledger is not None"
    assert marker in source and source.index("resolve_future_owner_verification_deferral") < source.index(marker)
