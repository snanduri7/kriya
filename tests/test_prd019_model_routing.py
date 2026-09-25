"""PRD-019: evidence-based model routing - deterministic route decisions.

Pure decision tests first (capability mismatch, metric tie, explicit
override, frozen mode, never an unqualified runtime), then the table and
the wiring into a real run.
"""
import json

import pytest

from kriya.core import model_qualification as mq
from kriya.core import model_routing as mr
from kriya.core import role_metrics as rm
from kriya.core.model_routing import CandidateEvidence


def _candidate(model, order, *, digest=None, qualification=mq.QUALIFIED, json_mode=True, window=32768,
               explicit=False, metrics=None, exact=True):
    return CandidateEvidence(
        model=model, order=order, runtime_digest=digest or f"sha-{model}", runtime_exact=exact,
        qualification=qualification, qualification_reasons=(), json_mode=json_mode, native_tool_calls=True,
        context_window=window, explicit=explicit, metrics=metrics,
    )


def _row(calls, *, protocol=0, schema=0, latency=1.0, attempts=0, passed=0):
    return {"calls": calls, "protocol_failures": protocol, "schema_failures": schema,
            "latency_seconds": latency * calls, "attempts": attempts, "attempts_passed": passed}


def _route(role, candidates, **kw):
    return mr.choose_route(role, candidates, min_calls=kw.pop("min_calls", 5),
                           min_context_window=kw.pop("min_context_window", None), table_digest="t",
                           default_model=kw.pop("default_model", "default-model"), **kw)


def test_capability_mismatch_is_rejected_with_its_reason():
    decision = _route("spec_compliance", [
        _candidate("no-json", 0, json_mode=False, metrics=_row(50)),
        _candidate("json", 1, metrics=_row(50, schema=10)),
    ])
    assert decision.model == "json"
    rejected = {row["model"]: row["reasons"] for row in decision.rejected}
    assert any("needs json_mode" in reason for reason in rejected["no-json"])


def test_an_unqualified_runtime_is_never_chosen_even_with_a_better_score():
    decision = _route("reviewer", [
        _candidate("unqualified-star", 0, qualification=mq.MISSING, metrics=_row(100)),
        _candidate("qualified", 1, metrics=_row(100, schema=40)),
    ])
    assert decision.model == "qualified"
    decision = _route("reviewer", [_candidate("only", 0, qualification=mq.NOT_QUALIFIED, metrics=_row(100))])
    assert decision.source == "configured_default" and decision.model == "default-model"
    assert decision.rejected[0]["reasons"]


def test_a_non_exact_runtime_is_not_eligible():
    decision = _route("planner", [_candidate("fuzzy", 0, exact=False)])
    assert decision.source == "configured_default"
    assert "not exact" in decision.rejected[0]["reasons"][0]


def test_the_safe_context_need_rejects_a_smaller_window():
    decision = _route("planner", [_candidate("small", 0, window=8192), _candidate("large", 1, window=32768)],
                      min_context_window=32768)
    assert decision.model == "large"


def test_metric_tie_breaks_by_operator_order_then_alias():
    same = _row(20, schema=2, latency=3.0)
    decision = _route("reviewer", [_candidate("zeta", 0, metrics=same), _candidate("alpha", 1, metrics=same)])
    assert decision.model == "zeta"  # operator order, not the alphabet
    assert _route("reviewer", [_candidate("zeta", 0, metrics=same),
                               _candidate("alpha", 0, metrics=same)]).model == "alpha"


def test_a_measured_better_candidate_wins_over_operator_order():
    decision = _route("reviewer", [_candidate("first", 0, metrics=_row(20, schema=8)),
                                   _candidate("second", 1, metrics=_row(20, schema=1))])
    assert decision.model == "second" and decision.source == "evidence"
    assert "clean-call rate" in decision.reason


def test_developer_is_scored_by_first_pass_gate_outcomes_not_call_success():
    decision = _route("developer", [
        _candidate("chatty", 0, metrics=_row(40, attempts=10, passed=3)),
        _candidate("solid", 1, metrics=_row(40, schema=5, attempts=10, passed=8)),
    ])
    assert decision.model == "solid"


def test_too_few_calls_is_unmeasured_and_ranked_after_measured():
    decision = _route("reviewer", [_candidate("new", 0, metrics=_row(2)),
                                   _candidate("known", 1, metrics=_row(30, schema=6))])
    assert decision.model == "known"
    only_new = _route("reviewer", [_candidate("new", 0, metrics=_row(2)), _candidate("newer", 1)])
    assert only_new.model == "new" and "operator order" in only_new.reason


def test_a_qualified_explicit_override_always_wins():
    decision = _route("planner", [
        _candidate("override", 0, explicit=True, metrics=_row(30, schema=20)),
        _candidate("better", 1, metrics=_row(30)),
    ])
    assert decision.model == "override" and decision.source == "explicit_override"


def test_an_unqualified_explicit_override_falls_back_to_the_candidates():
    decision = _route("planner", [
        _candidate("override", 0, explicit=True, qualification=mq.STALE),
        _candidate("qualified", 1),
    ])
    assert decision.model == "qualified"
    assert decision.rejected[0]["model"] == "override"


def test_routes_are_reproducible():
    candidates = [_candidate("a", 0, metrics=_row(10, schema=1)), _candidate("b", 1, metrics=_row(10, schema=1))]
    assert _route("reviewer", candidates).to_dict() == _route("reviewer", list(candidates)).to_dict()


# --- frozen mode ------------------------------------------------------------------------------

def test_frozen_mode_replays_exactly_the_recorded_runtime():
    frozen = {"routes": {"reviewer": {"model": "b", "runtime_digest": "sha-b"}}}
    candidates = [_candidate("a", 0), _candidate("b", 1)]
    decision = mr.frozen_route("reviewer", frozen, candidates, table_digest="f")
    assert (decision.model, decision.source) == ("b", "frozen_table")


@pytest.mark.parametrize("frozen,candidates,fragment", [
    ({"routes": {}}, [_candidate("a", 0)], "no route"),
    ({"routes": {"reviewer": {"model": "gone", "runtime_digest": "x"}}}, [_candidate("a", 0)], "no longer"),
    ({"routes": {"reviewer": {"model": "a", "runtime_digest": "old"}}}, [_candidate("a", 0)], "frozen as old"),
    ({"routes": {"reviewer": {"model": "a", "runtime_digest": "sha-a"}}},
     [_candidate("a", 0, qualification=mq.STALE)], "STALE"),
])
def test_frozen_mode_refuses_any_drift(frozen, candidates, fragment):
    with pytest.raises(mr.RoutingError) as refused:
        mr.frozen_route("reviewer", frozen, candidates, table_digest="f")
    assert refused.value.reason_code == mr.ROUTE_FROZEN_MISMATCH
    assert fragment in str(refused.value)


# --- the between-run table --------------------------------------------------------------------

def _metrics_row(role, model, digest, **counters):
    return {"role": role, "model": model, "runtime_digest": digest, "runtime_exact": True, "latency_seconds": 1.5,
            **counters}


def test_aggregation_is_deterministic_and_order_independent():
    runs = [
        ("run-2", [_metrics_row("reviewer", "m", "d1", calls=3, schema_failures=1)]),
        ("run-1", [_metrics_row("reviewer", "m", "d1", calls=2),
                   _metrics_row("developer", "m", "d1", calls=4, attempts=2, attempts_passed=1,
                                first_pass_success=False)]),
    ]
    table = rm.aggregate_role_metrics(runs)
    assert table == rm.aggregate_role_metrics(list(reversed(runs)))
    assert table["runs"] == ["run-1", "run-2"]
    reviewer = mr.metrics_row(table, "reviewer", "d1")
    assert (reviewer["calls"], reviewer["schema_failures"], reviewer["latency_seconds"]) == (5, 1, 3.0)
    developer = mr.metrics_row(table, "developer", "d1")
    assert (developer["first_pass_runs"], developer["first_pass_successes"]) == (1, 0)
    assert table["digest"] == mr.table_digest(table)


def test_a_tampered_table_is_refused(tmp_path):
    path = tmp_path / "table.json"
    table = rm.aggregate_role_metrics([("r", [_metrics_row("reviewer", "m", "d", calls=1)])])
    mr.write_table(str(path), table)
    assert mr.load_table(str(path))["digest"] == table["digest"]
    data = json.loads(path.read_text())
    data["rows"][0]["calls"] = 999
    path.write_text(json.dumps(data))
    with pytest.raises(mr.RoutingError) as refused:
        mr.load_table(str(path))
    assert refused.value.reason_code == mr.ROUTING_TABLE_INVALID


def test_a_missing_table_is_empty(tmp_path):
    assert mr.load_table(str(tmp_path / "none.json"))["rows"] == []


# --- planning and applying routes on a real configuration ----------------------------------------

from kriya.config import AppConfig  # noqa: E402
from kriya.config.config import FallbackModelConfig, ModelCapabilities  # noqa: E402
from kriya.core import model_runtime  # noqa: E402
from kriya.core.model_runtime import ModelRuntimeFingerprint  # noqa: E402


def _exact_ollama(monkeypatch):
    def probe(**kw):
        return ModelRuntimeFingerprint(
            alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
            artifact_digest=f"sha256:{kw['model']}", tokenizer_digest="sha256:tok", model_context_length=262144,
            configured_context_window=kw["configured_context"], effective_context_window=kw["configured_context"],
            kriya_protocol=kw["kriya_protocol"],
        )

    monkeypatch.setattr(model_runtime, "probe_model_runtime", probe)


def _routing_cfg(tmp_path, *, mode="evidence", roles=None, json_b=True):
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    routing = cfg.model_policy.routing
    routing.mode = mode
    routing.table_path = str(tmp_path / "table.json")
    routing.candidates = [
        FallbackModelConfig(model="cand-a", extra_body={"options": {"num_ctx": 32768}},
                            capabilities=ModelCapabilities(json_mode=True)),
        FallbackModelConfig(model="cand-b", extra_body={"options": {"num_ctx": 32768}},
                            capabilities=ModelCapabilities(json_mode=json_b)),
    ]
    routing.roles = roles or {"reviewer": ["cand-a", "cand-b"]}
    return cfg


def _qualify_placed(cfg, role, alias):
    """Qualify ``alias`` as it runs when routed to ``role``."""
    placed = mr.place_candidate(cfg, role, next(c for c in cfg.model_policy.routing.candidates if c.model == alias))
    runtime = model_runtime.resolve_configured_model_runtime(placed, alias)
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES]))
    return runtime.digest


def test_routing_off_changes_nothing(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path, mode="off")
    plan = mr.plan_routes(cfg)
    assert plan.decisions == {}
    assert mr.apply_routes(cfg, plan) is cfg


def test_evidence_routing_binds_the_chosen_qualified_candidate(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    digest_b = _qualify_placed(cfg, "reviewer", "cand-b")
    plan = mr.plan_routes(cfg)
    decision = plan.decisions["reviewer"]
    assert (decision.model, decision.source, decision.runtime_digest) == ("cand-b", "evidence", digest_b)
    assert [row["model"] for row in decision.rejected] == ["cand-a"]  # not qualified in this placement
    routed = mr.apply_routes(cfg, plan)
    assert routed.agent_llms.reviewer.llm.model == "cand-b"
    assert cfg.agent_llms.reviewer.llm is None  # the loaded configuration is never changed


def test_without_an_eligible_candidate_the_role_keeps_its_binding(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    plan = mr.plan_routes(cfg)
    assert plan.decisions["reviewer"].source == "configured_default"
    assert mr.apply_routes(cfg, plan).agent_llms.reviewer.llm is None


def test_the_developer_can_be_routed(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path, roles={"developer": ["cand-a"]})
    _qualify_placed(cfg, "developer", "cand-a")
    routed = mr.apply_routes(cfg, mr.plan_routes(cfg))
    assert routed.llm.model == "cand-a"
    assert routed.llm.max_tokens == cfg.llm.max_tokens  # unset candidate fields keep the primary's


def test_measured_outcomes_from_the_table_decide_between_qualified_candidates(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    digest_a = _qualify_placed(cfg, "reviewer", "cand-a")
    digest_b = _qualify_placed(cfg, "reviewer", "cand-b")
    assert mr.plan_routes(cfg).decisions["reviewer"].model == "cand-a"  # unmeasured: operator order
    table = rm.aggregate_role_metrics([("r1", [
        _metrics_row("reviewer", "cand-a", digest_a, calls=20, schema_failures=8),
        _metrics_row("reviewer", "cand-b", digest_b, calls=20, schema_failures=1),
    ])])
    mr.write_table(cfg.model_policy.routing.table_path, table)
    decision = mr.plan_routes(cfg).decisions["reviewer"]
    assert decision.model == "cand-b" and decision.table_digest == table["digest"]


def test_frozen_routing_replays_and_refuses_drift(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    _qualify_placed(cfg, "reviewer", "cand-b")
    frozen = mr.frozen_routes_from(mr.plan_routes(cfg))
    path = tmp_path / "frozen.json"
    mr.write_table(str(path), frozen)
    cfg.model_policy.routing.mode = "frozen"
    cfg.model_policy.routing.frozen_routes_path = str(path)
    assert mr.plan_routes(cfg).decisions["reviewer"].model == "cand-b"

    # The candidate's served window changes: a different exact runtime. It is
    # qualified too, so only the frozen runtime identity can refuse it.
    cfg.model_policy.routing.candidates[1] = cfg.model_policy.routing.candidates[1].model_copy(
        update={"extra_body": {"options": {"num_ctx": 16384}}})
    _qualify_placed(cfg, "reviewer", "cand-b")
    with pytest.raises(mr.RoutingError) as refused:
        mr.plan_routes(cfg)
    assert refused.value.reason_code == mr.ROUTE_FROZEN_MISMATCH
    assert "frozen as" in str(refused.value)


def test_a_role_on_its_configured_binding_cannot_be_frozen(tmp_path, monkeypatch):
    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    with pytest.raises(mr.RoutingError):
        mr.frozen_routes_from(mr.plan_routes(cfg))


def test_routing_config_is_validated_and_security_authority():
    from pydantic import ValidationError

    from kriya.config.authority import FieldClassification, classify_field

    assert classify_field("model_policy", "routing") is FieldClassification.SECURITY_AUTHORITY
    with pytest.raises(ValidationError):
        AppConfig(model_policy={"routing": {"mode": "evidence", "roles": {"reviewer": ["missing"]}}})
    with pytest.raises(ValidationError):
        AppConfig(model_policy={"routing": {"mode": "frozen"}})
    with pytest.raises(ValidationError):
        AppConfig(model_policy={"routing": {"mode": "guess"}})
    with pytest.raises(ValidationError):
        AppConfig(model_policy={"routing": {"table_path": "relative/table.json"}})


def test_the_workflow_command_applies_routes_and_the_run_records_them(tmp_path, monkeypatch):
    """Through the CLI boundary and a real run: the reviewer calls go to the
    routed candidate, model.route events land in traces.db, and the run never
    writes the routing table."""
    import asyncio
    import sqlite3
    import subprocess
    from unittest.mock import AsyncMock

    from kriya.cli import _workflow_config
    from kriya.core.kernel import Kernel
    from kriya.core.llm import LLMClient
    from kriya.core.state_paths import trace_db_path
    from kriya.workflow.workflow import WorkflowEngine

    _exact_ollama(monkeypatch)
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "s"], cwd=workspace, check=True)

    cfg = _routing_cfg(tmp_path)
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    _qualify_placed(cfg, "reviewer", "cand-b")
    routed = _workflow_config(cfg)
    assert routed.agent_llms.reviewer.llm.model == "cand-b"

    models_called = []
    llm = LLMClient(routed)

    async def request_once(client, model, system_prompt, user_prompt, *args, **kwargs):
        models_called.append(model)
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        content = ('{"files": ["mathx.py"]}' if "File List Planner" in first
                   else "Step 1: create mathx.py" if "Planner Agent" in first else "Review: Approved")
        if model == "dev-model" and "File List Planner" not in first:
            content = "def sub(a, b):\n    return a - b\n"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    llm._request_once = request_once
    engine = WorkflowEngine(Kernel(config=routed), llm)
    engine.run_verifier.judge = AsyncMock(return_value={"should_run": False, "run_commands": None})
    asyncio.run(engine.run_generation_workflow(goal="create mathx.py with sub(a, b)",
                                               workspace_path=str(workspace)))

    assert "cand-b" in models_called
    with sqlite3.connect(trace_db_path(routed)) as db:
        (events_json,) = db.execute("SELECT run_events FROM runs").fetchone()
    routes = [e["details"] for e in json.loads(events_json) if e["kind"] == "model.route"]
    assert [(r["role"], r["model"], r["source"]) for r in routes] == [("reviewer", "cand-b", "evidence")]
    assert routes[0]["rejected"][0]["model"] == "cand-a"
    assert not (tmp_path / "table.json").exists()


def test_a_candidate_aliasing_another_binding_with_other_settings_is_refused(tmp_path, monkeypatch):
    """Bindings resolve by alias: a candidate that shares its alias with an
    llm_chain entry (no JSON mode, whole files) would silently take that
    entry's capabilities and runtime identity when routed."""
    from pydantic import ValidationError

    _exact_ollama(monkeypatch)
    cfg = _routing_cfg(tmp_path)
    cfg.llm_chain = [FallbackModelConfig(model="cand-b", capabilities=ModelCapabilities(
        json_mode=False, preferred_edit_protocol="full_file"))]
    with pytest.raises(mr.RoutingError) as refused:
        mr.plan_routes(cfg)
    assert refused.value.reason_code == mr.ROUTING_CANDIDATE_ALIAS_CONFLICT
    assert "llm_chain[0]" in str(refused.value)

    with pytest.raises(ValidationError) as invalid:
        AppConfig(llm_chain=[{"model": "cand-b"}],
                  model_policy={"routing": {"candidates": [{"model": "cand-b", "context_window": 8192}]}})
    assert "distinct alias" in str(invalid.value)


def test_a_candidate_identical_to_the_binding_it_aliases_is_accepted():
    AppConfig(llm_chain=[{"model": "same", "context_window": 8192}],
              model_policy={"routing": {"candidates": [{"model": "same", "context_window": 8192}]}})
