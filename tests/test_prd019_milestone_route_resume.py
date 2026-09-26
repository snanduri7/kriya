"""PRD-019: route stickiness on the milestone path (`kriya generate --from-milestones --resume`).

The same invariant as a direct `generate --resume`, through the real CLI,
milestone driver, WorkflowEngine and LLMClient (only the transport and the
runtime probe are stand-ins): a resumed milestone sequence reuses the routes
its checkpoint recorded, even when the metrics table would now route
elsewhere, and a saved route that no longer holds refuses the resume before
any model request.
"""
import json
import sqlite3
import subprocess
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from kriya.cli import main
from kriya.config import AppConfig
from kriya.config.config import FallbackModelConfig, ModelCapabilities
from kriya.core import model_qualification as mq
from kriya.core import model_routing as mr
from kriya.core import model_runtime
from kriya.core import role_metrics as rm
from kriya.core.inference_settings import role_inference_settings
from kriya.core.llm import LLMClient
from kriya.core.model_runtime import ModelRuntimeFingerprint
from kriya.core.state_paths import trace_db_path
from kriya.workflow.checkpoint import list_checkpoints, save_checkpoint

GROUP = "route-resume"


def _probe(version="v1"):
    def probe(**kw):
        suffix = f"-{version}" if kw["model"] == "cand-a" and version != "v1" else ""
        return ModelRuntimeFingerprint(
            alias=kw["model"], endpoint="http://localhost:11434/v1", provider="ollama", provider_version="0.34.2",
            artifact_digest=f"sha256:{kw['model']}{suffix}", tokenizer_digest="sha256:tok",
            model_context_length=262144, configured_context_window=kw["configured_context"],
            effective_context_window=kw["configured_context"], kriya_protocol=kw["kriya_protocol"],
        )
    return probe


def _config(tmp_path):
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    routing = cfg.model_policy.routing
    routing.mode = "evidence"
    routing.table_path = str(tmp_path / "table.json")
    routing.candidates = [
        FallbackModelConfig(model=name, extra_body={"options": {"num_ctx": 32768}},
                            capabilities=ModelCapabilities(json_mode=True))
        for name in ("cand-a", "cand-b")
    ]
    routing.roles = {"reviewer": ["cand-a", "cand-b"]}
    return cfg


def _routed_settings(cfg, alias):
    placed = mr.place_candidate(cfg, "reviewer", next(c for c in cfg.model_policy.routing.candidates
                                                       if c.model == alias))
    return role_inference_settings(placed, "reviewer", alias).digest


def _qualify(cfg, alias):
    placed = mr.place_candidate(cfg, "reviewer", next(c for c in cfg.model_policy.routing.candidates
                                                       if c.model == alias))
    runtime = model_runtime.resolve_configured_model_runtime(placed, alias)
    mq.save_record(mq.build_record(runtime, [mq.CaseResult(c, mq.PASS) for c in mq.CAPABILITIES],
                                   settings=role_inference_settings(placed, "reviewer", alias)))
    return runtime.digest


class Transport:
    """LLMClient._request_once stand-in: records every model request."""

    def __init__(self):
        self.calls = []
        self.broken_m2 = True

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        self.calls.append(model)
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        prompt = user_prompt or ""
        target = "m2.py" if "build M2" in prompt or "m2.py" in prompt else "m1.py"
        if "File List Planner" in first:
            content = json.dumps({"files": [target]})
        elif "Planner Agent" in first:
            content = f"Step 1: create {target}"
        elif model == "dev-model":
            broken = target == "m2.py" and self.broken_m2
            content = "def value(:\n" if broken else f"VALUE = '{target}'\n"
        else:
            content = "Review: Approved"
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": 10, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}


@pytest.fixture
def milestone_run(tmp_path, monkeypatch):
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe())
    model_runtime.clear_model_runtime_cache()
    workspace = tmp_path / "ws"
    workspace.mkdir()
    for args in (["init", "-q"], ["config", "user.email", "t@e"], ["config", "user.name", "T"]):
        subprocess.run(["git", *args], cwd=workspace, check=True)
    (workspace / "README.md").write_text("seed\n")
    subprocess.run(["git", "add", "."], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "seed"], cwd=workspace, check=True)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"group_id": GROUP, "original_goal": "build m1 and m2", "milestones": [
        {"id": "M1", "goal": "build M1: create m1.py", "success_criterion": "m1.py exists", "depends_on": []},
        {"id": "M2", "goal": "build M2: create m2.py", "success_criterion": "m2.py exists", "depends_on": ["M1"]},
    ]}))
    monkeypatch.chdir(workspace)
    cfg = _config(tmp_path)
    digests = {alias: _qualify(cfg, alias) for alias in ("cand-a", "cand-b")}
    transport = Transport()

    def invoke(*extra):
        with patch("kriya.cli.load_config", return_value=cfg), \
             patch.object(LLMClient, "_request_once", new=transport):
            return CliRunner().invoke(main, ["generate", "--from-milestones", str(plan), "-y", *extra])

    first = invoke()
    # M1 passed; M2 failed its gates and left a resumable checkpoint that records the run's routes.
    m2_checkpoints = [c for c in list_checkpoints(str(workspace)) if c.get("milestone_group_id") == GROUP
                      and c.get("model_routes")]
    assert m2_checkpoints, first.output
    assert {c["model_routes"]["routes"]["reviewer"]["model"] for c in m2_checkpoints} == {"cand-a"}
    return {"cfg": cfg, "workspace": workspace, "digests": digests, "transport": transport, "invoke": invoke}


def _route_events_since(cfg, runs_before):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        rows = db.execute("SELECT run_events FROM runs ORDER BY rowid").fetchall()
    events = [event for (payload,) in rows[runs_before:] for event in json.loads(payload or "[]")]
    return [event["details"] for event in events if event["kind"] == "model.route"]


def _run_count(cfg):
    with sqlite3.connect(trace_db_path(cfg)) as db:
        return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def test_a_resumed_milestone_sequence_reuses_its_checkpointed_routes(milestone_run):
    cfg, transport, digests = milestone_run["cfg"], milestone_run["transport"], milestone_run["digests"]
    # Between the run and the resume the evidence changes: a fresh run would now route the reviewer to cand-b.
    table = rm.aggregate_role_metrics([("r1", [
        {"role": "reviewer", "model": "cand-a", "runtime_digest": digests["cand-a"], "runtime_exact": True,
         "inference_settings_digest": _routed_settings(cfg, "cand-a"), "calls": 20, "schema_failures": 8},
        {"role": "reviewer", "model": "cand-b", "runtime_digest": digests["cand-b"], "runtime_exact": True,
         "inference_settings_digest": _routed_settings(cfg, "cand-b"), "calls": 20, "schema_failures": 1},
    ])])
    mr.write_table(cfg.model_policy.routing.table_path, table)
    assert mr.plan_routes(cfg).decisions["reviewer"].model == "cand-b"
    # A newer checkpoint of an unrelated direct run in the same workspace must not lend its routes.
    save_checkpoint(str(milestone_run["workspace"]), "direct-run", {
        "stage": "plan", "model_routes": {"version": 1, "table_digest": None, "routes": {
            "reviewer": {"model": "cand-b", "runtime_digest": digests["cand-b"], "source": "evidence"}}},
    })

    runs_before = _run_count(cfg)
    transport.calls.clear()
    transport.broken_m2 = False
    resumed = milestone_run["invoke"]("--resume")

    assert "cand-a" in transport.calls, resumed.output
    assert "cand-b" not in transport.calls  # no rerouting to the table's new favourite
    routes = _route_events_since(cfg, runs_before)
    assert routes and all(r["mode"] == "resume" for r in routes)
    assert {(r["role"], r["model"], r["runtime_digest"]) for r in routes} == {
        ("reviewer", "cand-a", digests["cand-a"])}


def test_a_milestone_resume_whose_saved_route_no_longer_holds_is_refused_before_any_request(
        milestone_run, monkeypatch):
    transport = milestone_run["transport"]
    # cand-a was re-pulled since the run: its exact runtime is no longer the one the checkpoint recorded.
    monkeypatch.setattr(model_runtime, "probe_model_runtime", _probe("v2"))
    model_runtime.clear_model_runtime_cache()
    transport.calls.clear()

    refused = milestone_run["invoke"]("--resume")

    assert refused.exit_code == 1
    assert mr.ROUTE_RESUME_MISMATCH in refused.output
    assert transport.calls == []  # refused before any model request
