"""Stage 4 of a work unit, the Developer + Quality Gates loop (full modularization W4, E5), characterized from aad9ab1
BEFORE it moved out of ``WorkflowEngine.run_generation_workflow``: the loop logs through the facade's
``kriya.workflow.workflow.logger`` (patched by tests), and the prelude's third outcome (no sandbox at all) raises.

The end-to-end preservation proof is the kernel goldens (``tests/state_machine/test_sm_kernel_golden.py``: every
scenario runs through stage 4; ``legacy_baseline_indeterminate`` and ``legacy_worktree_sync_refused`` were written
from aad9ab1 before the prelude moved) and ``tests/test_early_exit_traces.py`` (the prelude stops' rows).
"""
import asyncio
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from kriya.config import AppConfig
from kriya.core.state_paths import trace_db_path

# The scenario harness lives in tests/state_machine/ (imported by bare name, exactly as the state-machine tier does).
sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

W = "kriya.workflow.workflow"


@pytest.fixture
def _git_identity(monkeypatch):
    for name, value in kg.GIT_ENV.items():
        monkeypatch.setenv(name, value)


def test_the_developer_loop_logs_through_the_facades_patched_logger(tmp_path, _git_identity):
    """The ``kriya.workflow.workflow.logger`` guard for stage 4: the prelude, the loop and the terminal regression log
    through the facade's logger (a test patching it sees them)."""
    with patch(f"{W}.logger") as facade_logger:
        outcome = kg._apply_run(tmp_path)
    assert outcome.result.legacy_result["quality_gates_passed"] is True
    infos = [call.args[0] for call in facade_logger.info.call_args_list]
    assert "Developer Agent implementing source files..." in infos                       # prelude
    assert any(text.startswith("Isolated sandbox worktree created at: ") for text in infos)  # prelude (sandbox)
    assert "Quality Gates: Running full test suite regression check..." in infos          # terminal regression


def test_a_sandbox_that_cannot_be_created_raises_and_leaves_the_exception_row(tmp_path, _git_identity):
    """The prelude's third outcome: a sandbox failure other than a sync refusal is never generated in place - the run
    raises RuntimeError from it (MODEL-EVIDENCE-HARDENING-001 leaves the <trace>.exception row), nothing applied."""
    def unavailable(workspace):
        raise OSError("no space left for the sandbox")

    with patch(f"{W}.create_git_worktree", new=unavailable), pytest.raises(RuntimeError) as raised:
        kg._apply_run(tmp_path, controller=False)
    assert str(raised.value) == ("Failed to create an isolated generation sandbox: no space left for the sandbox. "
                                 "Refusing to generate directly in the application workspace.")
    assert isinstance(raised.value.__cause__, OSError)
    with sqlite3.connect(trace_db_path(AppConfig())) as db:   # the test's isolated state directory (conftest)
        rows = db.execute("SELECT run_id, status, failure_category FROM runs").fetchall()
    assert [(status, category) for _, status, category in rows] == [("error", "RuntimeError")]
    assert rows[0][0].endswith(".exception")
    assert (Path(tmp_path) / "ws" / "calc.py").read_text() == kg.BASE


def test_each_iteration_starts_from_a_clean_candidate_state(tmp_path, _git_identity):
    """Every iteration resets the previous candidate's pre-approval review, verified binding and held executing gates
    (and their guard) BEFORE the attempt runs: a stale one would describe or authorize a superseded candidate. The
    attempt (the facade's ``run_attempt``, as the loop calls it) leaves all four behind and fails; each next attempt
    must start without them."""
    from kriya.workflow.failure import Failure, QualityGateFailure

    seen = []

    async def leaves_stale_state_and_fails(state, ctx):
        seen.append((state.pre_approval_review, state.verified_candidate_binding, ctx.deferred_executing_gates,
                     ctx.post_source_review_guard))
        state.attempt_number += 1
        state.pre_approval_review, state.verified_candidate_binding = "stale review", "stale binding"
        ctx.deferred_executing_gates, ctx.post_source_review_guard = object(), object()
        failure = Failure(type="compile", message=f"compile failed {state.attempt_number}",
                          raw_output=f"error {state.attempt_number}", attempt=state.attempt_number)
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)

    with patch(f"{W}.run_attempt", new=leaves_stale_state_and_fails):
        outcome = kg._apply_run(tmp_path)
    assert outcome.result.legacy_result["quality_gates_passed"] is False
    assert len(seen) >= 2 and all(entry == (None, None, None, None) for entry in seen), seen


@pytest.mark.parametrize(("stop_loop", "attempts_after"), [(True, 0), (False, 1)])
def test_a_failure_best_of_n_recorded_ends_the_loop_only_when_it_says_so(stop_loop, attempts_after, tmp_path,
                                                                        _git_identity):
    """STATE-BEST-OF-N-HANDOFF-001: the first attempt runs Best-of-N (configured, sandboxed); a failure it already
    recorded with ``stop_loop`` ends the loop with no further attempt even though budgets remain, and without it the
    loop goes on to an ordinary attempt (the facade's ``run_attempt``) - neither records the failure again."""
    from kriya.workflow import best_of_n
    from kriya.workflow.failure import Failure, QualityGateFailure

    async def recorded(state, ctx, n):
        state.attempt_number += 1
        raise best_of_n.BestOfNFailureRecorded(QualityGateFailure(Failure(type="compile", message="x", raw_output="x")),
                                               stop_loop=stop_loop)

    ordinary = []

    async def next_attempt(state, ctx):
        ordinary.append(state.attempt_number)
        state.environment_failure = "scripted stop"   # end the run after the one ordinary attempt

    def configure(cfg):
        cfg.autonomy.best_of_n_first_attempt = 2

    with patch.object(best_of_n, "run_attempt_with_best_of_n", new=recorded), \
            patch(f"{W}.run_attempt", new=next_attempt), patch(f"{W}.handle_attempt_failure") as handled:
        outcome = kg._apply_run(tmp_path, configure=configure, controller=False)
    assert len(ordinary) == attempts_after and handled.call_count == 0
    assert outcome.result["quality_gates_passed"] is False


def _first_attempt_context(tmp_path, *, configure=None, run_kwargs=None):
    """The AttemptContext the prelude built, as the first attempt (the facade's run_attempt) receives it."""
    seen = []

    async def capture_and_stop(state, ctx):
        seen.append(ctx)
        state.environment_failure = "scripted stop"

    with patch(f"{W}.run_attempt", new=capture_and_stop):
        kg._apply_run(tmp_path, configure=configure, controller=False, run_kwargs=run_kwargs)
    return seen[0]


@pytest.mark.parametrize(("chain_length", "max_retries"), [(0, 4), (1, 4), (3, 4), (4, 5), (6, 7)])
def test_the_full_set_budget_grows_with_the_fallback_chain(chain_length, max_retries, tmp_path, _git_identity):
    """max_retries = max(4, 1 + len(chain)) with a chain, 4 without; the targeted budget is 3 either way."""
    from kriya.config.config import FallbackModelConfig

    def chain(cfg):
        cfg.llm_chain = [FallbackModelConfig(model=f"fallback-{i}", base_url=cfg.llm.base_url) for i in range(chain_length)]
    ctx = _first_attempt_context(tmp_path, configure=chain)
    assert (ctx.max_retries, ctx.targeted_max_retries, len(ctx.chain)) == (max_retries, 3, chain_length)


@pytest.mark.parametrize(("run_kwargs", "expected"), [
    ({}, {"write_scope_mode": "unrestricted", "no_change_verifier_downstream": False, "exit_authority_goal": kg.GOAL,
          "runtime_verification_required": False}),
    ({"allowed_write_relpaths": ["calc.py"], "milestone_group_id": "mg", "grounding_goal": "the grounding goal",
      "runtime_verification_required": True},
     {"write_scope_mode": "allowlist", "no_change_verifier_downstream": True, "exit_authority_goal": kg.GOAL,
      "runtime_verification_required": True}),
    ({"goal": "Add mul to calc.py and run the program to show it works.", "grounding_goal": "ground"},
     {"write_scope_mode": "unrestricted", "no_change_verifier_downstream": False,
      "exit_authority_goal": "Add mul to calc.py and run the program to show it works.",
      "runtime_verification_required": True}),
])
def test_the_prelude_resolves_the_attempt_contexts_run_wide_decisions(run_kwargs, expected, tmp_path, _git_identity):
    """Resolved once, before the loop: the write scope (ALLOWLIST when write paths are given and no mode, else
    UNRESTRICTED), whether a downstream verifier decides a zero-change result (a milestone run), the exit-authority goal
    (the unit's authoritative goal, else the grounding goal) and whether runtime behaviour must be verified (the goal's
    own words unless the caller decided)."""
    ctx = _first_attempt_context(tmp_path, run_kwargs=run_kwargs)
    assert {"write_scope_mode": ctx.write_scope_mode.value, "no_change_verifier_downstream": ctx.no_change_verifier_downstream,
            "exit_authority_goal": ctx.exit_authority_goal,
            "runtime_verification_required": ctx.runtime_verification_required} == expected


def _bypassing_attempt(files):
    """A passing attempt that writes ``files`` into the sandbox WITHOUT the attempt's own per-attempt gates (the facade's
    run_attempt replaced), so only the loop's terminal checks can judge the candidate."""
    async def attempt(state, ctx):
        state.attempt_number += 1
        for rel, content in files.items():
            original = Path(ctx.workspace_path) / rel
            state.all_original_contents.setdefault(rel, original.read_text() if original.exists() else "")
            target = Path(ctx.worktree_path) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
            state.all_files_written.add(rel)
        state.candidate_gates_succeeded = True
    return attempt


def _terminal_check_run(tmp_path, files, prepare=None):
    with patch(f"{W}.run_attempt", new=_bypassing_attempt(files)):
        outcome = kg._apply_run(tmp_path, prepare=prepare)
    import json
    with sqlite3.connect(trace_db_path(outcome.cfg)) as db:
        outcomes = json.loads(db.execute("SELECT gate_outcomes FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    return outcome, [g.get("type") for g in outcomes if g.get("success") is False]


def test_the_terminal_check_rejects_existing_tests_redirected_to_a_parallel_implementation(tmp_path, _git_identity):
    """BROWNFIELD OWNERSHIP at the terminal regression: a new arith.py taking over test_calc.py from calc.py (the suite
    still green) is a deterministic gate failure, never applied - even when the attempt's own gate did not run."""
    arith = "def plus(a, b):\n    return a + b\n\n\ndef minus(a, b):\n    return a - b\n"
    redirected = ("from arith import minus, plus\n\n\ndef test_add():\n    assert plus(1, 2) == 3\n\n\n"
                  "def test_sub():\n    assert minus(3, 2) == 1\n")
    outcome, types = _terminal_check_run(tmp_path, {"arith.py": arith, "test_calc.py": redirected})
    assert outcome.result.legacy_result["quality_gates_passed"] is False
    assert "brownfield_ownership_redirect" in types
    assert set(outcome.extra["workspace_files"]) == {"calc.py", "test_calc.py"}


def test_the_terminal_check_rejects_a_removed_public_api_a_call_site_names(tmp_path, _git_identity):
    """BROWNFIELD PUBLIC API at the terminal regression: removing calc.div, which report.py calls (no test covers it, the
    suite stays green), is a deterministic gate failure, never applied."""
    def with_div(workspace):
        (workspace / "calc.py").write_text(kg.BASE + "\n\ndef div(a, b):\n    return a / b\n")
        (workspace / "report.py").write_text("from calc import div\n\n\ndef ratio(a, b):\n    return div(a, b)\n")
    outcome, types = _terminal_check_run(tmp_path, {"calc.py": kg.GOOD}, prepare=with_div)
    assert outcome.result.legacy_result["quality_gates_passed"] is False
    assert "brownfield_public_api_changed" in types
    assert "def div" in (Path(outcome.workspace) / "calc.py").read_text()


def test_the_terminal_check_rejects_a_change_outside_the_authorized_semantic_regions(tmp_path, _git_identity):
    """CORR-018-P1 at the terminal regression: a run bounded by authorized semantic regions whose candidate creates the
    one region file without a TEST_FILE_ADD grant is a deterministic gate failure, never applied."""
    from kriya.workflow.semantic_region_authority import AuthorizedSemanticRegion, RegionType

    region = AuthorizedSemanticRegion(relpath="helpers.py", region_type=RegionType.METHOD_BODY, member_key="helpers#f()")
    with patch(f"{W}.run_attempt", new=_bypassing_attempt({"calc.py": kg.GOOD, "helpers.py": "def f():\n    return 1\n"})):
        outcome = kg._apply_run(tmp_path, controller=False, run_kwargs={"authorized_semantic_regions": [region]})
    import json
    with sqlite3.connect(trace_db_path(outcome.cfg)) as db:
        outcomes = json.loads(db.execute("SELECT gate_outcomes FROM runs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    assert outcome.result["quality_gates_passed"] is False
    assert "semantic_region_unauthorized" in [g.get("type") for g in outcomes if g.get("success") is False]
    assert not (Path(outcome.workspace) / "helpers.py").exists()


class _Crashed(BaseException):
    """The process dies while the person is asked (a kill, not a failure the run could handle)."""


def test_a_resumed_candidate_comes_back_with_the_ledger_it_was_verified_against(tmp_path, _git_identity):
    """PRD-008: run 1 reaches the candidate checkpoint (its attempt recorded an obligation outcome) and dies during the
    approval; run 2 (resume=True) reuses that candidate, so the prelude restores the checkpoint's effective obligation
    ledger into the run's ledger before the first attempt - never only the fresh requirement seeds - and the REG-R1 pytest stability the run had measured."""
    import dataclasses
    import json

    from kriya.control.checkpoint import list_checkpoints
    from kriya.workflow.obligations import ObligationStatus

    write = _bypassing_attempt({"calc.py": kg.GOOD})

    async def records_an_outcome(state, ctx):
        await write(state, ctx)
        seeded = ctx.obligation_ledger.current("requirement.REQ-1")
        ctx.obligation_ledger.record(dataclasses.replace(seeded, status=ObligationStatus.SATISFIED, revision=1))
        state.pytest_stability_cache["binding-e5"] = {"stable": True}   # REG-R1 stability measured by this run

    def killed(workspace):
        raise _Crashed()

    with patch(f"{W}.run_attempt", new=records_an_outcome), pytest.raises(_Crashed):
        kg._apply_run(tmp_path, controller=False, on_approval=killed)
    workspace = str(tmp_path / "ws")
    [checkpoint] = list_checkpoints(workspace)
    saved = checkpoint["effective_obligation_ledger_snapshot"]
    assert checkpoint["stage"] == "candidate_gates_passed" and '"satisfied"' in json.dumps(saved)

    from _scripted_run import engine

    def isolated(cfg):
        cfg.paths.skills, cfg.paths.memory = str(tmp_path / "skills"), str(tmp_path / "memory")
        cfg.skills.load_global = cfg.skills.load_cwd = False
        cfg.knowledge.offline_mode = True
    eng, llm, _cfg = engine(chain=False, configure=isolated)
    kg._apply_role_script(llm, developer=lambda m, n, t: kg.GOOD, reviewer=lambda n, s, u: "ok", lessons=lambda n: "[]")
    seen = []

    async def capture_and_stop(state, ctx):
        seen.append((ctx.resume_plan, ctx.obligation_ledger.to_snapshot(), dict(state.pytest_stability_cache)))
        state.environment_failure = "scripted stop"

    with patch(f"{W}.run_attempt", new=capture_and_stop):
        asyncio.run(eng.run_generation_workflow(goal=kg.GOAL, workspace_path=workspace, resume=True,
                                                approval_callback=lambda diffs, reason: True))
    [(plan, ledger, stability)] = seen
    assert plan.reuse_candidate is True and ledger == saved
    assert stability["binding-e5"] == {"stable": True}   # the measured stability comes back too


def test_a_plan_scope_reinvocation_keeps_its_grounded_loci_in_the_sandbox(tmp_path, _git_identity):
    """P2: the grounded failure loci a plan-scope re-invocation carries are seeded into the run's edit loci against the
    sandbox, before the first attempt - each only while its file's revision is still the one it was observed on."""
    from kriya.workflow.edit_safety import read_file_revision

    probe = tmp_path / "probe" / "calc.py"
    probe.parent.mkdir()
    probe.write_text(kg.BASE)                      # the sandbox's calc.py holds exactly these bytes
    revision = read_file_revision(str(probe))
    locations = [{"filepath": "calc.py", "line": 2, "revision": revision},
                 {"filepath": "calc.py", "line": 5, "revision": "stale-revision"}]
    seen = []

    async def capture_and_stop(state, ctx):
        seen.append(dict(state.edit_anchor_loci))
        state.environment_failure = "scripted stop"

    with patch(f"{W}.run_attempt", new=capture_and_stop):
        kg._apply_run(tmp_path, controller=False, run_kwargs={"grounded_locations": locations})
    assert seen == [{"calc.py": [2]}]   # the current locus seeded, the stale one dropped
