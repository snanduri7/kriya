"""OWNER-RECOVERY-LOCAL-SUCCESS-KNOWLEDGE-PROMOTED-BEFORE-DEPENDENT-VERIFICATION-001 (owner rulings 2026-11-13 after Scenario-A run 9): an
owner reopened inside a recovery episode is only LOCALLY accepted when its own gates pass; nothing is learned from that success.

Run 20261113T060030-40a14267, MEASURED: the reopened s3 passed its own gates at 01:14:03Z; the lesson extraction call ran at 01:14:05Z; the
dependent verifier replay ran afterwards and failed. The extracted "fact" came from Kriya's own scope error text. It was not staged only because
the model answered a JSON object where an array is parsed (class (a), by accident); a well-formed answer is staged for human promotion.

The contract: for the owner-reopen roles the stage-7 learning (skill accrual, lesson extraction) is not performed. An ordinary unit keeps its
behaviour. First the unit boundary, then the REAL controller and unit with the lesson channel recorded.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, List
from unittest.mock import patch

import pytest
import test_enforce_owner_recovery_verifier_replay_001 as r7
import test_owner_recovery_implicated_scope_001 as scope
import test_verifier_replay_unit_001 as real

from kriya.knowledge.channels.live_failure import LiveFailureChannel
from kriya.workflow import stage_apply
from kriya.workflow.enforce_loop import DEPENDENT_VERIFICATION_PENDING_ROLES

WRONG_FILE = (scope.PROTO, "package com.example;\n\npublic class Proto { /* not asked for */ }\n")


# ------------------------------------------------------------------------------------------------------------ the unit boundary

@pytest.mark.asyncio
async def test_only_the_reopened_owner_is_marked_dependent_verification_pending(tmp_path):
    shape = scope.Run9Shape(["putInt+putLong", "three bytes + four bytes"], scope._fixed_after_one_repair)
    await r7._run(tmp_path, shape, scope.run9_plan())

    assert shape.roles() == ["s3:planned", "s5:planned", "s3:owner_recovery", "s5:verifier_replay"]
    assert [call["dependent_verification_pending"] for call in shape.calls] == [False, False, True, False]


def test_the_pending_roles_are_the_owner_reopens():
    assert DEPENDENT_VERIFICATION_PENDING_ROLES == {"owner_recovery", "may_change_owner_recovery", "verification_owner_recovery"}


# ------------------------------------------------------------------------------------------------------------ the real controller and unit

def _run_with_recorded_lessons(tmp_path: Path, lib_answers: List[Any]) -> Any:
    """The real run with (1) the lesson channel recorded and (2) the "hard-won" trigger forced to hold for EVERY unit that reaches stage 7
    (two full-set retries on its budget): which attempts a scripted model needs is not what is under test; whether a unit that qualifies is
    allowed to learn is. ``run.learning``: (role, did the stage decide to extract) per unit that reached stage 7."""
    extractions: List[Any] = []
    learning: List[Any] = []
    real_prepare = stage_apply.prepare_lessons

    async def record(_channel, context):
        extractions.append(sorted(context.file_contents))
        return []

    def prepare(ctx, deps):
        ctx.state.budgets.retry_count = max(ctx.state.budgets.retry_count, 2)
        outcome = real_prepare(ctx, deps)
        learning.append((ctx.execution_scope.split("role=")[1], outcome[1]))
        return outcome

    with patch.object(LiveFailureChannel, "extract", new=record), patch("kriya.workflow.development_loop.prepare_lessons", new=prepare):
        run = scope._real_run(tmp_path, lib_answers)
    run.extractions, run.learning = extractions, learning
    return run


def test_real_run9_shape_no_lesson_is_extracted_from_a_local_owner_success_that_the_dependent_verifier_then_refuses(tmp_path):
    """The run-9 chronology: the reopened owner passes its OWN gates, and the carried verifier then fails against it - at every reopen."""
    run = _run_with_recorded_lessons(tmp_path, [real.BROKEN, real.STILL_BROKEN, real.STILL_BROKEN + "// 2\n", real.STILL_BROKEN + "// 3\n"])
    legacy = run.result.legacy_result

    assert legacy["status"] != "success"
    events = legacy["plan_recovery_events"]
    assert len(events) == 3 and [event["owner_recovery_passed"] for event in events] == [False, False, False]   # each local success disproved
    # the planned owner qualified and learned (the ordinary behaviour, kept); none of its three recoveries did
    assert run.learning == [("planned", True), ("owner_recovery", False), ("owner_recovery", False), ("owner_recovery", False)]
    assert len(run.extractions) == 1


def test_real_no_lesson_is_extracted_from_the_local_owner_success_even_when_the_recovery_later_settles(tmp_path):
    """A local owner success is never the moment of learning, whether the dependent verification then passes or not. The consumer whose
    replay settles the recovery is an ordinary successful unit and keeps its behaviour."""
    run = _run_with_recorded_lessons(tmp_path, [real.BROKEN, real.FIXED])

    assert run.result.legacy_result["status"] == "success"
    assert run.learning == [("planned", True), ("owner_recovery", False), ("verifier_replay", True)]
    assert run.extractions == [[scope.LIB, scope.PROTO], [scope.LIBTEST]]          # the planned owner, then the settled consumer: never the repair


def test_real_an_ordinary_unit_keeps_its_hard_won_lesson_without_the_forced_trigger(tmp_path):
    """Positive control with nothing forced: four refused answers in the PLANNED generation make the unit's success hard-won (MEASURED in this
    harness: retry_count 2), and its lesson is extracted exactly as before."""
    extractions: List[Any] = []

    async def record(_channel, context):
        extractions.append(sorted(context.file_contents))
        return []

    with patch.object(LiveFailureChannel, "extract", new=record):
        run = scope._real_run(tmp_path, [WRONG_FILE] * 4 + [real.FIXED])

    assert run.result.legacy_result["status"] == "success"
    assert extractions == [[scope.LIB, scope.PROTO]]
