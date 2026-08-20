import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.workflow.attempt import (
    _ensure_generation_time_budget,
    _estimated_generation_seconds,
    _run_developer_generation,
)
from kriya.workflow.failure import QualityGateFailure
from kriya.workflow.state import GenerationState


def _context(config, developer=None):
    return SimpleNamespace(
        kernel=Kernel(config=config),
        developer=developer,
        expected_files_upfront=["A.java", "B.java"],
    )


def test_generation_estimate_learns_median_per_file_duration():
    state = GenerationState(generation_timings=[
        {"duration_seconds": 20, "file_count": 2},
        {"duration_seconds": 120, "file_count": 4},
        {"duration_seconds": 40, "file_count": 2},
    ])

    assert _estimated_generation_seconds(
        state, file_count=3, configured_per_file=90,
    ) == 60


def test_generation_estimate_does_not_apply_primary_timing_to_fallback_model():
    state = GenerationState(generation_timings=[
        {"duration_seconds": 20, "file_count": 2, "model": "fast-local"},
        {"duration_seconds": 180, "file_count": 2, "model": "slow-local"},
    ])

    assert _estimated_generation_seconds(
        state, file_count=2, configured_per_file=45, active_model="slow-local",
    ) == 180
    assert _estimated_generation_seconds(
        state, file_count=2, configured_per_file=45, active_model="unseen-local",
    ) == 90


def test_time_budget_rejects_doomed_generation_before_model_call():
    config = AppConfig()
    config.autonomy.generation_time_budget_seconds = 100
    config.autonomy.generation_gate_reserve_seconds = 20
    config.autonomy.generation_seconds_per_file_estimate = 15
    state = GenerationState(
        generation_started_monotonic=time.monotonic() - 60,
    )

    with pytest.raises(QualityGateFailure) as exc_info:
        _ensure_generation_time_budget(state, _context(config), file_count=2)

    assert exc_info.value.failure.type == "time_budget_exhausted"
    assert "40.0s remaining" in exc_info.value.failure.message


def test_timed_generation_records_duration_without_prompt_content():
    config = AppConfig()
    developer = SimpleNamespace(run_generation=AsyncMock(return_value=[{
        "filepath": "A.java", "content": "class A {}",
    }]))
    state = GenerationState()
    ctx = _context(config, developer)

    result = asyncio.run(_run_developer_generation(
        state, ctx,
        task_description="proprietary task",
        known_target_files=["A.java"],
    ))

    assert result[0]["filepath"] == "A.java"
    assert state.generation_timings[0]["file_count"] == 1
    assert state.generation_timings[0]["succeeded"] is True
    assert "proprietary task" not in repr(state.generation_timings)
    assert state.run_events[-1].kind == "generation.completed"
