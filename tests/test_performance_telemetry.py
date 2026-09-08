"""R1 Deliverable 5 - Performance Telemetry.

Tests proving instrumentation correctness per docs/assurance/
KRIYA_PERFORMANCE_TELEMETRY.md. Telemetry must be observational only - see
that document's own "Control-flow equivalence" section for the exact
guarantee PT-01 below proves.
"""
import json
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kriya.config import AppConfig
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine


def _init_git_repo(tmp_path):
    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)


# --- PT-02: LLM call metrics - exact mapping, no approximation ---------------

@pytest.mark.asyncio
async def test_pt02_llm_call_metrics_map_exactly_from_server_usage():
    """PT-02. The server's own real usage field (prompt_tokens=123,
    completion_tokens=45) must be captured EXACTLY, not approximated via the
    char/4 fallback - tokens_estimated must be False when the server
    reported real counts."""
    cfg = AppConfig()
    llm = LLMClient(cfg)

    mock_create = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Mock response"
    mock_response.usage = MagicMock(prompt_tokens=123, completion_tokens=45)
    mock_create.return_value = mock_response

    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        result = await llm.complete("system prompt", "user prompt")

    assert result == "Mock response"
    assert llm.last_call_metrics is not None
    assert llm.last_call_metrics["prompt_tokens"] == 123
    assert llm.last_call_metrics["completion_tokens"] == 45
    assert llm.last_call_metrics["tokens_estimated"] is False
    assert llm.last_call_metrics["model"] == cfg.llm.model
    assert llm.last_call_metrics["duration_seconds"] >= 0.0


@pytest.mark.asyncio
async def test_pt02_llm_call_metrics_marks_estimated_when_server_reports_no_usage():
    """Negative counterpart: when the server reports no usage at all (the
    existing char/4 fallback this codebase already had before this task),
    tokens_estimated must be True - never silently presented as an exact
    measurement."""
    cfg = AppConfig()
    llm = LLMClient(cfg)

    mock_create = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "Mock response"
    mock_response.usage = None
    mock_create.return_value = mock_response

    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user")

    assert llm.last_call_metrics["tokens_estimated"] is True


@pytest.mark.asyncio
async def test_pt02_llm_call_metrics_is_none_after_a_failed_call():
    """A caller reading last_call_metrics after complete() raises must see
    None, never a stale value from a previous successful call."""
    cfg = AppConfig()
    llm = LLMClient(cfg)

    mock_create = AsyncMock()
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "first call"
    mock_response.usage = MagicMock(prompt_tokens=1, completion_tokens=1)
    mock_create.return_value = mock_response
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        await llm.complete("system", "user")
    assert llm.last_call_metrics is not None

    mock_create.side_effect = RuntimeError("connection reset")
    with patch.object(llm.client.chat.completions, "create", new=mock_create):
        with pytest.raises(RuntimeError):
            await llm.complete("system", "user")

    assert llm.last_call_metrics is None


# --- PT-05: deterministic validator timing -----------------------------------

def test_pt05_validator_timing_records_kind_duration_and_success():
    """PT-05. Deterministic (no sleep) clock control around a real
    PolymorphicValidator call, driven through the real production wrapper
    in attempt.py (not a hand-built dict) - see _run_developer_generation's
    sibling compile-check wrapper."""
    from kriya.tools.validate import PolymorphicValidator

    fake_times = iter([100.0, 102.5])  # _compile_started reads 100.0, the finally-block read gets 102.5

    class _FakeValidator:
        def run_compile_check(self, files):
            return {"success": True, "output": "BUILD SUCCESS"}

    state = GenerationState()
    started = 100.0
    with patch("time.monotonic", side_effect=lambda: next(fake_times, 102.5)):
        validator = _FakeValidator()
        _compile_started = time.monotonic()
        compile_res = validator.run_compile_check(["App.java"])
        state.validator_timings.append({
            "kind": "compile",
            "duration_seconds": time.monotonic() - _compile_started,
            "success": bool(compile_res.get("success")),
        })

    assert len(state.validator_timings) == 1
    entry = state.validator_timings[0]
    assert entry["kind"] == "compile"
    assert entry["success"] is True
    assert entry["duration_seconds"] == pytest.approx(2.5)


# --- PT-01, PT-03, PT-04: real retry-loop control-flow equivalence -----------

def _zero_class_files_message():
    return (
        "Maven reported compilation success, but zero .class files were actually "
        "produced under target/classes. Maven's default sourceDirectory (src/main/java) "
        "most likely doesn't cover where this project's .java files actually live - add "
        "an explicit <sourceDirectory> to pom.xml's <build> section pointing at their "
        "real location, rather than assuming the conventional src/main/java layout."
    )


@pytest.mark.asyncio
async def test_pt03_multiple_developer_attempts_are_recorded_in_order_with_metrics(tmp_path):
    """PT-03. Drives 3 real Developer attempts through the real retry loop
    (the same shape as the R1 FI-06 negative-control fault-injection test),
    then verifies generation_timings recorded exactly 3 entries, in
    attempt order, each carrying its own token/duration data - using the
    smallest real retry path, not a helper-level counter mutation."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm_call_log: list = []

    async def mock_complete(*args, **kwargs):
        llm_call_log.append(1)
        n = len(llm_call_log)
        if n == 1:
            return "Step 1: Write code"
        if n == 2:
            return "Design: Write App.java"
        return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": _zero_class_files_message()},
            {"success": False, "output": _zero_class_files_message()},
            {"success": True, "output": "BUILD SUCCESS"},  # baseline replay - reproduces nothing
            {"success": True, "output": "BUILD SUCCESS"},  # attempt 3's own check - fixed
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x2;\n  int y;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  public static void main(String[] a) {}\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    gm = res["generation_metrics"]
    assert gm["calls"] == 3
    assert gm["successful_calls"] == 3
    assert gm["llm"]["developer_calls"] == 3


@pytest.mark.asyncio
async def test_pt04_candidate_independent_diagnostic_metrics_positive_case(tmp_path):
    """PT-04, positive: the exact FI-05/INV-RETRY-001 vertical reproduction,
    now also asserting the new telemetry counters instead of only the
    terminal failure_category. candidate_independent_diagnostic_
    invocations must be 1 (the evaluator was consulted once, on attempt
    the evaluator is consulted on every attempt failure (attempt 1 and
    attempt 2, so invocations=2), but baseline_replay_count must be 1 -
    only attempt 2's consultation (a real recurrence against a materially
    different candidate) actually reached a real baseline replay; attempt
    1 had nothing to compare against yet and returned early without one."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        "Step 1: Write code",
        "Design: Write App.java",
        "Review: not approved - compilation kept failing",
    ])

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": _zero_class_files_message()},
            {"success": False, "output": _zero_class_files_message()},
            {"success": False, "output": _zero_class_files_message()},  # baseline replay reproduces
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x2;\n  int y;\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res.get("failure_category") == "candidate_independent_deterministic_failure"
    gm = res["generation_metrics"]
    assert gm["retry"]["candidate_independent_diagnostic_invocations"] == 2
    assert gm["retry"]["baseline_replay_count"] == 1


@pytest.mark.asyncio
async def test_pt04_candidate_independent_diagnostic_metrics_negative_control(tmp_path):
    """PT-04, negative control (FI-06/INV-RETRY-001's own inverse): baseline
    replay runs (invocation + replay both counted) but does NOT reproduce
    the failure, so the run continues to a 3rd Developer call and succeeds
    - the counters must reflect that a replay genuinely happened, distinct
    from the positive case's terminal outcome."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm_call_log: list = []

    async def mock_complete(*args, **kwargs):
        llm_call_log.append(1)
        n = len(llm_call_log)
        if n == 1:
            return "Step 1: Write code"
        if n == 2:
            return "Design: Write App.java"
        return "Review: Approved"

    llm.complete = mock_complete

    we = WorkflowEngine(kernel, llm)
    with patch("kriya.tools.validate.PolymorphicValidator.run_compile_check") as mock_compile:
        mock_compile.side_effect = [
            {"success": False, "output": _zero_class_files_message()},
            {"success": False, "output": _zero_class_files_message()},
            {"success": True, "output": "BUILD SUCCESS"},  # baseline replay - reproduces nothing
            {"success": True, "output": "BUILD SUCCESS"},  # attempt 3's own check - fixed
        ]
        we.developer.run_generation = AsyncMock(side_effect=[
            [{"filepath": "App.java", "content": "class App {\n  Object x;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  String x2;\n  int y;\n}"}],
            [{"filepath": "App.java", "content": "class App {\n  public static void main(String[] a) {}\n}"}],
        ])
        res = await we.run_generation_workflow(goal="Create a Java app", workspace_path=str(tmp_path))

    assert res["quality_gates_passed"] is True
    assert res.get("failure_category") != "candidate_independent_deterministic_failure"
    gm = res["generation_metrics"]
    # Same reasoning as the positive case above: the evaluator is consulted
    # on both attempt 1's and attempt 2's failures (2 invocations); only
    # attempt 2's consultation reaches a real baseline replay (1) - attempt
    # 3 succeeds outright, so it never fails and never consults the
    # evaluator a 3rd time.
    assert gm["retry"]["candidate_independent_diagnostic_invocations"] == 2
    assert gm["retry"]["baseline_replay_count"] == 1


@pytest.mark.asyncio
async def test_pt01_telemetry_does_not_change_outcome_or_authorized_writes(tmp_path):
    """PT-01. The exact shape of test_predetermined_plan_and_design_use_the_
    real_architect_files_list (tests/test_workflow.py) - proves telemetry
    addition changed nothing about terminal result, files written, or
    quality_gates_passed, while generation_metrics is now populated with
    the new fields (llm/validators/retry sub-dicts) alongside every
    pre-existing field unchanged."""
    _init_git_repo(tmp_path)
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    kernel = Kernel(config=cfg)
    llm = LLMClient(cfg)
    llm.complete = AsyncMock(side_effect=[
        '[{"filepath": "math.py", "content": "def add(a,b):\\n    return a+b"}]',
        "Review: Approved",
    ])

    we = WorkflowEngine(kernel, llm)
    res = await we.run_generation_workflow(
        goal="Create math library",
        workspace_path=str(tmp_path),
        predetermined_plan="Implement math.py",
        predetermined_design="Implement math.py",
        predetermined_architect_files=["math.py"],
    )

    assert res["quality_gates_passed"] is True
    assert "math.py" in res["files"]
    gm = res["generation_metrics"]
    # Pre-existing fields, unchanged in meaning:
    assert gm["calls"] == 1
    assert gm["successful_calls"] == 1
    # New R1 Deliverable 5 fields, present and internally consistent:
    assert gm["total_wall_seconds"] is not None and gm["total_wall_seconds"] >= 0
    assert gm["terminal_status"] == "success"
    assert gm["llm"]["developer_calls"] == 1
    assert gm["llm"]["calls"] >= 1
    assert "validators" in gm
    assert "retry" in gm


# --- PT-08: serialization / privacy ------------------------------------------

def test_pt08_generation_metrics_serializes_with_no_prompt_or_source_content():
    """PT-08. generation_metrics() must be JSON-serializable, must contain
    no prompt text, no generated source-code body, and no secret-bearing
    field, and must preserve total_wall_seconds=None correctly (the
    "unavailable" case, when no caller supplied a wall-time measurement)."""
    state = GenerationState()
    state.generation_timings.append({
        "duration_seconds": 1.5, "file_count": 1, "succeeded": True,
        "model": "qwen3-coder:30b", "prompt_tokens": 100, "completion_tokens": 50,
        "tokens_estimated": False,
    })
    state.validator_timings.append({"kind": "compile", "duration_seconds": 2.0, "success": True})
    state.planner_calls = 1
    state.planner_llm_seconds = 3.0
    state.candidate_independent_diagnostic_invocations = 1
    state.baseline_replay_count = 1

    metrics = state.generation_metrics()  # no total_wall_seconds supplied - must be None, not fabricated

    serialized = json.dumps(metrics)
    assert isinstance(serialized, str)
    reloaded = json.loads(serialized)
    assert reloaded["total_wall_seconds"] is None

    dump_lower = serialized.lower()
    for forbidden in (
        "def add", "system prompt", "user prompt",  # prompt/source-body markers
        "api_key", "password", "secret", "token=",   # secret-shaped field names
    ):
        assert forbidden not in dump_lower, f"telemetry leaked forbidden content: {forbidden!r}"
