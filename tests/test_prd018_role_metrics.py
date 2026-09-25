"""PRD-018: role-model independence visibility and per-role metrics."""
import asyncio

import pytest

from kriya.core.role_metrics import (
    UNATTRIBUTED,
    UNAVAILABLE_RUNTIME,
    RoleMetrics,
    current_model_role,
    model_role,
    shared_runtime_groups,
)


def _call(metrics, model="m", digest="d1", exact=True, status="OK", **kw):
    metrics.record_call(model=model, runtime_digest=digest, runtime_exact=exact, status=status,
                        latency_seconds=kw.pop("latency", 2.0), prompt_tokens=kw.pop("prompt", 100),
                        completion_tokens=kw.pop("completion", 10), tokens_estimated=kw.pop("estimated", False), **kw)


def test_calls_are_attributed_to_the_innermost_role_scope():
    metrics = RoleMetrics()
    assert current_model_role() == UNATTRIBUTED
    with model_role("reviewer"):
        _call(metrics)
        with model_role("developer"):
            _call(metrics)
        _call(metrics, status="MALFORMED_STRUCTURED_OUTPUT")
    _call(metrics)
    rows = {row["role"]: row for row in metrics.since(None)}
    assert rows["reviewer"]["calls"] == 2 and rows["reviewer"]["protocol_failures"] == 1
    assert rows["developer"]["calls"] == 1
    assert rows[UNATTRIBUTED]["calls"] == 1


def test_role_scope_survives_awaits_and_does_not_leak_between_tasks():
    metrics = RoleMetrics()

    async def call_as(role):
        with model_role(role):
            await asyncio.sleep(0)
            _call(metrics)

    async def main():
        await asyncio.gather(call_as("planner"), call_as("architect"))

    asyncio.run(main())
    assert sorted(row["role"] for row in metrics.since(None)) == ["architect", "planner"]


def test_metrics_are_keyed_by_exact_runtime_not_alias():
    metrics = RoleMetrics()
    with model_role("developer"):
        _call(metrics, digest="d1")
        _call(metrics, digest="d2")  # same alias, re-pulled tag
        _call(metrics, digest="d3", exact=False)
    digests = sorted(row["runtime_digest"] for row in metrics.since(None))
    assert digests == ["d1", "d2", UNAVAILABLE_RUNTIME]


def test_schema_failures_are_charged_to_the_runtime_that_answered():
    metrics = RoleMetrics()
    with model_role("spec_compliance"):
        _call(metrics, digest="d9")
        metrics.record_schema_failure(model="m")
    row = metrics.since(None)[0]
    assert (row["runtime_digest"], row["schema_failures"]) == ("d9", 1)


def test_attempt_outcomes_record_first_pass_and_retries():
    metrics = RoleMetrics()
    metrics.record_attempt(role="developer", model="m", runtime_digest="d1", runtime_exact=True,
                           attempt_number=1, passed=False)
    metrics.record_attempt(role="developer", model="m", runtime_digest="d1", runtime_exact=True,
                           attempt_number=2, passed=True)
    row = metrics.since(None)[0]
    assert (row["attempts"], row["attempts_passed"], row["retries_triggered"]) == (2, 1, 1)
    assert row["first_pass_success"] is False


def test_since_reports_only_what_a_run_added():
    metrics = RoleMetrics()
    with model_role("reviewer"):
        _call(metrics, prompt=50)
        baseline = metrics.snapshot()
        _call(metrics, prompt=70)
    rows = metrics.since(baseline)
    assert rows[0]["calls"] == 1 and rows[0]["prompt_tokens"] == 70
    assert metrics.since(metrics.snapshot()) == []


def test_shared_runtime_groups():
    groups = shared_runtime_groups({
        "developer": ("m", "d1", True), "reviewer": ("m", "d1", True),
        "planner": ("other", "d2", True), "run_verifier": ("x", "unavailable", False),
    })
    by_roles = {tuple(group["roles"]): group for group in groups}
    assert by_roles[("developer", "reviewer")]["identity"] == "exact"
    assert by_roles[("run_verifier",)]["identity"] == "unverified_alias"


@pytest.mark.parametrize("status,counted", [("OK", 0), ("EMPTY_CONTENT", 1), ("OUTPUT_TRUNCATED", 1),
                                            ("BACKEND_ERROR", 1), ("TIMEOUT", 1), ("CANCELLED", 0)])
def test_protocol_failure_statuses(status, counted):
    metrics = RoleMetrics()
    _call(metrics, status=status)
    assert metrics.since(None)[0]["protocol_failures"] == counted


# --- through the real stack ---------------------------------------------------------------------

import json  # noqa: E402
import sqlite3  # noqa: E402
import subprocess  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from click.testing import CliRunner  # noqa: E402

from kriya.config import AppConfig  # noqa: E402
from kriya.config.config import AgentModelConfig, LLMConfig  # noqa: E402
from kriya.core import model_runtime  # noqa: E402
from kriya.core.llm import LLMClient  # noqa: E402
from kriya.core.model_runtime import ModelRuntimeFingerprint  # noqa: E402

ROLES = ("planner", "architect", "reviewer", "run_verifier", "skill_gap", "spec_compliance")


def _exact_ollama(monkeypatch):
    def probe(**kw):
        return ModelRuntimeFingerprint(
            alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
            artifact_digest=f"sha256:{kw['model']}", tokenizer_digest="sha256:tok", model_context_length=262144,
            configured_context_window=kw["configured_context"], effective_context_window=kw["configured_context"],
            kriya_protocol=kw["kriya_protocol"],
        )

    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)


def _repo(tmp_path):
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "s"], cwd=workspace, check=True)
    return workspace


def _cfg(helper="helper-model"):
    cfg = AppConfig()
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    for role in ROLES:
        setattr(cfg.agent_llms, role, AgentModelConfig(llm=LLMConfig(model=helper)))
    return cfg


def test_a_real_run_persists_per_role_metrics_and_the_role_runtime_assignment(tmp_path, monkeypatch):
    from kriya.cli import main
    from kriya.core.kernel import Kernel
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow import WorkflowEngine

    _exact_ollama(monkeypatch)
    workspace = _repo(tmp_path)
    cfg = _cfg()
    llm = LLMClient(cfg)

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        content = ('{"files": ["mathx.py"]}' if "File List Planner" in first
                   else "Step 1: create mathx.py" if "Planner Agent" in first else "Review: Approved")
        if model == "dev-model" and "File List Planner" not in first:
            content = "def sub(a, b):\n    return a - b\n"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    engine = WorkflowEngine(Kernel(config=cfg), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    asyncio.run(engine.run_generation_workflow(goal="create mathx.py with sub(a, b)", workspace_path=str(workspace)))

    with sqlite3.connect(trace_db_path(cfg)) as db:
        (events_json,) = db.execute("SELECT run_events FROM runs").fetchone()
    events = json.loads(events_json)
    metrics = [e for e in events if e["kind"] == "model.role_metrics"]
    assert len(metrics) == 1
    rows = {row["role"]: row for row in metrics[0]["details"]["rows"]}
    developer = rows["developer"]
    assert developer["model"] == "dev-model" and developer["runtime_exact"] is True
    failures = [e["details"].get("failure_type") for e in events if e["kind"] == "attempt.failed"]
    assert developer["calls"] >= 1 and developer["attempts"] == 1, failures
    assert developer["first_pass_success"] is True and developer["schema_failures"] == 0
    assert rows["planner"]["model"] == "helper-model"
    assert rows["planner"]["runtime_digest"] != developer["runtime_digest"]
    assert UNATTRIBUTED not in rows or rows[UNATTRIBUTED]["calls"] >= 0

    independence = next(e for e in events if e["kind"] == "model.role_independence")["details"]
    assert independence["roles"]["developer"]["model"] == "dev-model"
    shared = [group["roles"] for group in independence["groups"] if len(group["roles"]) > 1]
    assert shared == [sorted(ROLES)]
    assert independence["second_opinion_is_verification"] is False

    # Between-run aggregation reads the same rows back.
    monkeypatch.chdir(workspace)
    result = CliRunner().invoke(main, ["model", "metrics", "--json"], obj={"config": cfg})
    assert result.exit_code == 0, result.output
    table = json.loads(result.output)
    assert any(row["role"] == "developer" and row["attempts_passed"] == 1 for row in table["rows"])


def _policy_cfg(helper):
    cfg = _cfg(helper)
    cfg.model_policy.independent_roles = ["run_verifier", "spec_compliance"]
    return cfg


def test_the_independence_policy_refuses_a_workflow_before_any_model_call(monkeypatch):
    from kriya.cli import _workflow_config

    _exact_ollama(monkeypatch)
    shared = _policy_cfg("dev-model")  # the verifier roles run the Developer's own runtime
    with pytest.raises(SystemExit):
        _workflow_config(shared)
    independent = _policy_cfg("helper-model")
    assert _workflow_config(independent) is independent


def test_the_policy_cannot_be_shown_on_an_unidentified_runtime():
    from kriya.core.role_metrics import independence_violations, role_runtimes

    cfg = _policy_cfg("helper-model")  # probing is off: no exact identity
    violations = independence_violations(cfg, role_runtimes(cfg))
    assert violations and "not exactly identified" in violations[0]


def test_doctor_row_is_informational_by_default_and_blocking_under_the_policy(tmp_path, monkeypatch):
    from kriya.production_doctor import CheckStatus, _check_role_independence, _Context, _role_independence_required

    _exact_ollama(monkeypatch)
    default = _cfg("dev-model")
    check = _check_role_independence(_Context(cfg=default, workspace=str(tmp_path)))
    assert (check.status, check.required) == (CheckStatus.WARN, False)
    assert not _role_independence_required(default)

    violated = _policy_cfg("dev-model")
    check = _check_role_independence(_Context(cfg=violated, workspace=str(tmp_path)))
    assert (check.status, check.required) == (CheckStatus.FAIL, True)
    assert check.evidence["reason_code"] == "ROLE_INDEPENDENCE_REQUIRED"
    assert _role_independence_required(violated)

    met = _policy_cfg("helper-model")
    check = _check_role_independence(_Context(cfg=met, workspace=str(tmp_path)))
    assert (check.status, check.required) == (CheckStatus.PASS, True)


def test_the_policy_is_security_authority():
    from kriya.config.authority import FieldClassification, classify_field

    assert classify_field("model_policy", "independent_roles") is FieldClassification.SECURITY_AUTHORITY


def test_an_unknown_policy_role_is_rejected():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        AppConfig(model_policy={"independent_roles": ["developer"]})


def test_calls_before_a_unit_run_are_reported_once_by_the_next_row():
    """A controller's planning calls happen before a unit run starts; they
    are carried by the next trace row, and no row repeats another's."""
    metrics = RoleMetrics()
    with model_role("planner"):
        _call(metrics)  # structured planning, before any unit run
    with model_role("developer"):
        _call(metrics)
    first = metrics.take_unreported()
    assert sorted(row["role"] for row in first) == ["developer", "planner"]
    with model_role("developer"):
        _call(metrics)
    second = metrics.take_unreported()
    assert [(row["role"], row["calls"]) for row in second] == [("developer", 1)]
    assert metrics.take_unreported() == []
