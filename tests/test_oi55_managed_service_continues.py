"""OI-55 (Wave 2 Step 9; owner decision 2026-10-18; KERNEL-MANAGED-SERVICE-GATES-RETURN-EARLY-001): a managed-service runtime verification is ONE gate of the attempt, not its end.

MEASURED at 5f77cbe (the E9 goldens engine_managed_service_approved / _blocked_optional and the gate case runtime_managed_service_returns_early PINNED it): after
``_execute_managed_service_verification`` the executing gates ``return``ed, so a managed service that PASSED (or whose OPTIONAL check was declined) never reached the migration-completion gate,
the goal-spec compliance gate or ``candidate_gates_succeeded``: the run reported ``candidate_gates_passed`` / ``quality_gates_passed`` False and ``failure_category quality_gates_exhausted``
although every gate that ran had passed (``overall_attempt_passed`` True), and the gates after it were silently never evaluated.

Now: a managed PASS and an optional decline CONTINUE to the later required gates, exactly once and without a second run of the ordinary runtime-command path; a REQUIRED decline / unsupported,
a failed managed verification and every later failing gate still stop the attempt, and ``candidate_gates_succeeded`` is True only when every applicable required gate passed.
The managed-service function's own raise paths (REQUIRED + human-in-the-loop = MANAGED_SERVICE_APPROVAL_UNSUPPORTED, environment stop, failed run) are unit-tested in
tests/test_managed_service_hitl_fail_closed.py and tests/test_runtime_verification.py; here the seam is scripted to do each of them.
"""
import sys
from pathlib import Path
from unittest.mock import AsyncMock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_gate_orchestrator as tg  # noqa: E402

from kriya.workflow.failure import Failure, QualityGateFailure  # noqa: E402

# commands the ORDINARY runtime path WOULD run (python app.py, with app.py in the candidate - see control 10): so a missing neutralisation is observable as a second run
MANAGED = {**tg.JUDGE_APP, "execution_mode": "managed_service"}
CANDIDATE = {"calc.py": tg.CALC_NEW, "app.py": tg.PY_APP}
NON_COMPLIANT = {"compliant": False, "reasoning": "mul is missing", "missing_requirements": ["mul(a, b) exists"], "likely_files": ["calc.py"]}
UNSUPPORTED = "MANAGED_SERVICE_APPROVAL_UNSUPPORTED"


def _runtime_cfg():
    return tg._config(run_verification_enabled=True, spec_compliance_enabled=True)


def _pass(_state, _ctx):
    """The default: the managed service verification passed (nothing raised, nothing flagged)."""


def _optional_decline(state, _ctx):
    state.run_verification_declined = True


def _raise(kind, message):
    def effect(state, _ctx):
        failure = Failure(type=kind, message=message, raw_output=message, attempt=state.attempt_number, diagnostics={"reason_code": message.split(":")[0]})
        state.record_gate_outcome(failure.to_gate_outcome())
        raise QualityGateFailure(failure)
    return effect


def _run(tmp_path, *, effect=_pass, judge=MANAGED, spec=None, java_migration=False, required=False, state=None, candidate=None):
    """The real _run_executing_candidate_gates over a scripted seam. Returns (form, spec_checks_awaited, judge_dict_seen_by_the_cache)."""
    holder = {}
    answers = list(spec) if spec is not None else None

    async def case(state_arg, ctx, script_arg):
        ctx.run_verifier.judge = AsyncMock(return_value=judge)
        if answers is not None:
            ctx.spec_compliance.check = AsyncMock(side_effect=list(answers))
        holder["ctx"], holder["state"] = ctx, state_arg
        await tg._executing(state_arg, ctx, script_arg)
    if java_migration:
        kwargs = dict(script=tg._Script(stack="java"), files={"pom.xml": tg.GSON_POM, tg.JSON_SERVICE: tg.GSON_SERVICE},
                      candidate={tg.JSON_SERVICE: tg.GSON_SERVICE.replace("serialize", "toJson")}, ctx_overrides=tg._migration_ctx)
    else:
        kwargs = dict(script=tg._Script(), ctx_overrides=dict(runtime_verification_required=required), candidate=candidate or CANDIDATE)
    form = tg.run_gates(tmp_path, case, cfg=_runtime_cfg(), managed_effect=effect, state=state, **kwargs)
    return form, holder


def _seam_calls(form, name):
    return [call for call in form["seams"] if call.get("seam") == name or call.get("name") == name]


def _called(form, name):
    return len([c for c in form["seams"] if name in str(c)])


def _spec_checks(holder):
    check = holder["ctx"].spec_compliance.check
    return check.await_count if hasattr(check, "await_count") else None


# -------------------------------------------------------------------------------------- 1-3: a PASS continues, exactly once, to success

def test_1_a_managed_pass_continues_to_the_later_gates_and_succeeds(tmp_path):
    form, holder = _run(tmp_path)
    assert form["outcome"] == "returned"
    assert form["state"]["candidate_gates_succeeded"] is True                                  # was False: the early return never reached it
    assert [o["type"] for o in form["gate_outcomes"] if o.get("type") == "goal_spec_compliance"] == ["goal_spec_compliance"]   # the later gate really ran


def test_2_the_ordinary_runtime_command_path_does_not_run_a_second_time(tmp_path):
    form, _ = _run(tmp_path)
    assert _called(form, "_execute_managed_service_verification") == 1                          # the managed verification: once
    assert _called(form, "_approve_and_execute_runtime_verification") == 0                      # and never again through the finite-command path
    seen = next(call for call in form["seams"] if "_execute_managed_service_verification" in str(call))
    assert seen["judgment"]["should_run"] is True and seen["judgment"]["execution_mode"] == "managed_service"   # the service verification got the ORIGINAL judgment


def test_3_the_cached_judgment_is_untouched_by_the_local_neutralisation(tmp_path):
    form, _ = _run(tmp_path)
    cached = form["state"]["cached_run_verification_judgment"]
    assert cached["should_run"] is True and cached["execution_mode"] == "managed_service"      # only the attempt's local copy was neutralised


# -------------------------------------------------------------------------------------------------- 4-5: an optional decline is recorded and continues

def test_4_an_optional_decline_is_recorded_and_the_attempt_continues_to_success(tmp_path):
    state = tg._state("calc.py")
    form, holder = _run(tmp_path, effect=_optional_decline, state=state)
    assert form["outcome"] == "returned" and form["state"]["candidate_gates_succeeded"] is True
    assert holder["state"].run_verification_declined is True                                    # the decline is still recorded ...
    assert [o["type"] for o in form["gate_outcomes"] if o.get("type") == "goal_spec_compliance"]      # ... and did not skip the later gate


def test_5_an_optional_decline_does_not_excuse_a_later_failing_required_gate(tmp_path):
    form, holder = _run(tmp_path, effect=_optional_decline, java_migration=True)          # the authoritative later gate: migration completion
    assert form["outcome"] != "returned" and form["outcome"]["raised"] == "QualityGateFailure"
    assert form["state"]["candidate_gates_succeeded"] is False
    assert holder["state"].run_verification_declined is True


# ------------------------------------------------------------------------------------------ 6-7: a REQUIRED decline and a failed run stay fail-closed

def test_6_a_required_decline_or_unsupported_approval_still_stops_the_attempt_before_any_later_gate(tmp_path):
    form, holder = _run(tmp_path, effect=_raise("verification_infrastructure_failure", f"{UNSUPPORTED}: no way to ask you to approve a service"), required=True)
    assert form["outcome"]["raised"] == "QualityGateFailure" and form["outcome"]["type"] == "verification_infrastructure_failure"
    assert form["state"]["candidate_gates_succeeded"] is False
    assert not [o for o in form["gate_outcomes"] if o.get("type") == "goal_spec_compliance"]     # the later gate never ran


def test_7_a_failed_managed_verification_still_stops_the_attempt(tmp_path):
    form, _ = _run(tmp_path, effect=_raise("run_verification", "RUN_VERIFICATION_FAILED: the service did not become ready"))
    assert form["outcome"]["raised"] == "QualityGateFailure" and form["state"]["candidate_gates_succeeded"] is False
    assert not [o for o in form["gate_outcomes"] if o.get("type") == "goal_spec_compliance"]


# ------------------------------------------------------------------------- 8-9: the later required gates now bind after a managed PASS

def test_8_the_spec_compliance_gate_is_evaluated_after_a_managed_pass_and_its_model_verdict_stays_advisory(tmp_path):
    """GR-R1B: a model-only spec verdict fails no attempt, so a non-compliant answer does not stop the run - but the gate RAN (before the fix it was never reached)."""
    form, holder = _run(tmp_path, spec=[NON_COMPLIANT, NON_COMPLIANT])
    assert holder["ctx"].spec_compliance.check.await_count >= 1                                   # the later gate was actually evaluated
    assert form["outcome"] == "returned" and form["state"]["candidate_gates_succeeded"] is True


def test_9_a_managed_pass_followed_by_an_incomplete_migration_cannot_produce_success(tmp_path):
    form, _ = _run(tmp_path, java_migration=True)
    assert form["outcome"] != "returned" and form["outcome"]["raised"] == "QualityGateFailure"
    assert form["state"]["candidate_gates_succeeded"] is False                                   # the migration-completion gate ran after the managed service and refused


# ---------------------------------------------------------------------------------------------------------- 10: the ordinary path is unchanged

def test_10_a_finite_command_verification_is_unchanged(tmp_path):
    form, _ = _run(tmp_path, judge=tg.JUDGE_APP)
    assert form["outcome"] == "returned" and form["state"]["candidate_gates_succeeded"] is True
    assert _called(form, "_approve_and_execute_runtime_verification") == 1 and _called(form, "_execute_managed_service_verification") == 0
