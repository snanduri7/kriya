"""Code Intelligence R1 slice 2, item 6: the Developer receives exact T0.

End to end through the real direct run (the model is scripted at the
transport seam, so every prompt is the one Kriya builds): fused localization
finds the member the goal names, the known-target seam renders its exact
CURRENT body bound to its lines and raw revision, and the Code Intelligence
package beside it (enclosing declarations, sibling signatures, linked
configuration). The index is deliberately stale - it holds an older body -
and must never be what the Developer is shown as the target.
"""
import asyncio
import json
import os
import subprocess
import textwrap
from unittest.mock import AsyncMock, patch

from _fake_embedding import StaticEmbedder
from _protocol_responses import as_requested

from kriya.analyzer.analyzer import RepositoryAnalyzer
from kriya.config import AppConfig
from kriya.core import model_runtime
from kriya.core.kernel import Kernel
from kriya.core.llm import LLMClient
from kriya.workflow.context_budget import build_known_target_context
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

TARGET = "src/main/java/shop/OrderService.java"
INDEXED_BODY = "        return subtotal - discount;"
CURRENT_BODY = "        return subtotal - discount - loyaltyCredit(subtotal);"


def _source(body: str) -> str:
    return textwrap.dedent("""\
        package shop;

        import java.math.BigDecimal;
        import java.util.List;

        public class OrderService {
            @Value("${orders.discount}")
            private int discount;

            public OrderService() {
            }

            public int total(int subtotal) {
        BODY
            }

            int loyaltyCredit(int subtotal) {
                return subtotal / 100;
            }
        }
        """).replace("BODY", body)


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _config(tmp_path):
    cfg = AppConfig()
    cfg.llm.model = "dev-model"
    cfg.llm.context_window = 32768
    cfg.llm.extra_body = {}
    cfg.llm_chain = []
    cfg.autonomy.mode = "guardrails"
    cfg.autonomy.run_verification_enabled = False
    cfg.autonomy.spec_compliance_enabled = False
    cfg.paths.skills = str(tmp_path / "skills")
    cfg.paths.memory = str(tmp_path / "memory")
    cfg.logging.file_enabled = False
    cfg.logging.run_file_enabled = False
    return cfg


def _workspace(tmp_path, cfg):
    workspace = tmp_path / "ws"
    (workspace / "src/main/java/shop").mkdir(parents=True)
    (workspace / "src/main/resources").mkdir(parents=True)
    (workspace / TARGET).write_text(_source(INDEXED_BODY))
    (workspace / "src/main/resources/application.properties").write_text("orders.discount=5\n")
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    embedder = StaticEmbedder([0.3, 0.4, 0.5])
    asyncio.run(RepositoryAnalyzer(str(workspace)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=embedder))
    # The member changes after indexing: the index now holds an older body.
    (workspace / TARGET).write_text(_source(CURRENT_BODY))
    _git(workspace, "commit", "-qam", "change total")
    return workspace, embedder


def _run(tmp_path, goal):
    model_runtime.clear_model_runtime_cache()
    cfg = _config(tmp_path)
    workspace, embedder = _workspace(tmp_path, cfg)
    developer = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TARGET]})
        elif "Developer Agent" in first:
            developer.append(user_prompt)
            content = as_requested("FIX ANALYSIS: none.\nSEARCH:\n" + CURRENT_BODY + "\nREPLACE:\n"
                                   + CURRENT_BODY + "\n", system_prompt, TARGET)
        else:
            content = "Review: Approved"
        tokens = len(((system_prompt or "") + (user_prompt or "")).encode()) * 2 // 7
        return {"content": content, "reasoning_chars": 0, "prompt_tokens": tokens, "completion_tokens": 5,
                "finish_reason": "stop", "provider_metadata": {}}

    events = []
    real_record = GenerationState.record_event

    def record(state, event):
        events.append(event)
        return real_record(state, event)

    engine = WorkflowEngine(Kernel(config=cfg), LLMClient(cfg))
    with patch.object(LLMClient, "_request_once", new=transport), \
         patch.object(GenerationState, "record_event", new=record), \
         patch("kriya.memory.embedding.configured_client", return_value=embedder), \
         patch("kriya.tools.validate.PolymorphicValidator.run_compile_check",
               new=lambda *a, **k: {"success": True, "output": "ok"}), \
         patch("kriya.tools.validate.PolymorphicValidator.run_tests",
               new=lambda *a, **k: {"success": True, "output": "ok"}):
        asyncio.run(engine.run_generation_workflow(
            goal=goal, workspace_path=str(workspace), predetermined_plan=f"Repair {TARGET}",
            predetermined_design="", predetermined_architect_files=[TARGET],
            approval_callback=AsyncMock(return_value=True)))
    return developer, events


def test_developer_receives_exact_current_t0_with_its_package_never_the_indexed_body(tmp_path):
    developer, events = _run(tmp_path, "OrderService.total must also subtract the loyalty credit exactly once")
    assert developer, "no Developer request was made"
    prompt = developer[0]
    owner = prompt[prompt.index("=== EXISTING OWNER (member OrderService.total"):]
    header, body = owner.split("===\n", 1)[0], owner.split("===\n", 1)[1]
    # T0: the CURRENT bytes, bound to their exact lines and raw revision.
    assert "lines 13-15" in header and "revision " in header and "tier=member_exact" in header
    assert CURRENT_BODY in body.split("=== EXISTING OWNER")[0]
    assert INDEXED_BODY not in prompt
    # The package beside it, from the same current bytes.
    assert "#### Enclosing declarations (read-only)" in prompt
    assert "package shop;" in prompt and "public class OrderService" in prompt
    assert '@Value("${orders.discount}") private int discount' in prompt
    assert "import java.util.List;" not in prompt  # imports the member does not use stay out
    assert "int loyaltyCredit(int subtotal)" in prompt  # sibling signatures
    assert "src/main/resources/application.properties:1  orders.discount=5" in prompt  # T3
    [composition, *_] = [e.details for e in events if e.kind == "developer.prompt_composition"]
    assert composition["t0_member_tokens"] > 0 and composition["t0_header_tokens"] > 0
    assert composition["t3_tokens"] > 0 and composition["sibling_signatures_tokens"] > 0
    assert composition["prompt_tokens_reported"] > composition["t0_member_tokens"]
    # The goal names the member exactly: deterministic localization is
    # clear, so no model is consulted (CI-6 is ambiguity-only).
    [decision] = [e.details for e in events if e.kind == "localization.decision"]
    assert (decision["reason_code"], decision["called"]) == ("LOCALIZATION_CLEAR", False)
    retrieval = [e for e in events if e.kind == "retrieval.code_intelligence"]
    assert retrieval and retrieval[0].details["source"] == "code_intelligence"
    assert retrieval[0].details["candidates"][0]["lookup_key"] == "shop.OrderService.total"


def test_skills_and_optional_context_cannot_evict_t0(tmp_path):
    """T0 is admitted against its own room: with the shared budget already
    exhausted (skills, references, graph context took it), the exact member
    is still rendered; without that room the old path dropped it."""
    workspace = tmp_path / "ws"
    (workspace / "src/main/java/shop").mkdir(parents=True)
    (workspace / TARGET).write_text(_source(CURRENT_BODY))
    hints = {TARGET: ["OrderService.total"]}
    rendered, package = build_known_target_context([TARGET], str(workspace), None, 0, member_hints=hints,
                                                   exact_member_budget=2000)
    exact = [item for item in package.relevant_files if item.tier == "member_exact"]
    assert [item.member_id for item in exact] == ["OrderService.total"]
    assert CURRENT_BODY in rendered
    _, starved = build_known_target_context([TARGET], str(workspace), None, 0, member_hints=hints)
    assert not [item for item in starved.relevant_files if item.tier == "member_exact"]


def test_the_package_is_built_from_the_resolved_current_bytes_not_the_index(tmp_path):
    """A worktree holding newer bytes than the workspace and the index:
    both the exact member and its package come from the worktree."""
    workspace, worktree = tmp_path / "ws", tmp_path / "wt"
    for root, body in ((workspace, INDEXED_BODY), (worktree, CURRENT_BODY)):
        (root / "src/main/java/shop").mkdir(parents=True)
        (root / TARGET).write_text(_source(body).replace("loyaltyCredit", "credit" if root is worktree else
                                                         "loyaltyCredit"))
    rendered, package = build_known_target_context(
        [TARGET], str(workspace), str(worktree), 4000, member_hints={TARGET: ["OrderService.total"]})
    assert "int credit(int subtotal)" in rendered and "loyaltyCredit" not in rendered.split("Enclosing")[1]
    kinds = {item.reason for item in package.relevant_files}
    assert {"known_target_member_exact", "known_target_member_package"} <= kinds
    assert not any(item.is_exact for item in package.relevant_files if item.reason == "known_target_member_package")


def test_a_failing_package_build_keeps_the_exact_member_and_plain_signatures(tmp_path, monkeypatch):
    from kriya.code_intel import service as service_module

    workspace = tmp_path / "ws"
    (workspace / "src/main/java/shop").mkdir(parents=True)
    (workspace / TARGET).write_text(_source(CURRENT_BODY))
    monkeypatch.setattr(service_module.CodeIntelligenceService, "build_context",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rendered, package = build_known_target_context(
        [TARGET], str(workspace), None, 4000, member_hints={TARGET: ["OrderService.total"]})
    reasons = [item.reason for item in package.relevant_files]
    assert reasons == ["known_target_member_exact", "known_target_sibling_signatures"]
    assert CURRENT_BODY in rendered
    assert os.path.exists(workspace / TARGET)


def test_prompt_composition_counts_rendered_sections_and_provider_prefill():
    from kriya.workflow.prompt_composition import prompt_composition

    context = ("=== EXISTING OWNER (member A.m, lines 1-3, revision abc, tier=member_exact): A.java ===\n"
               + "x" * 400 + "\n\n=== EXISTING OWNER (full source, tier=signatures): A.java ===\n"
               "### Context for a.A.m (A.java)\n\n#### Enclosing declarations (read-only)\n" + "h" * 80
               + "\n\n#### Linked tests (read-only)\n" + "t" * 40 + "\n\n#### Linked configuration (read-only)\n"
               + "c" * 20)
    details = prompt_composition(context, "s" * 100, prompt_tokens_reported=900,
                                 provider_metadata={"prompt_eval_ms": 1500, "load_ms": 20})
    assert (details["t0_member_tokens"], details["t0_header_tokens"], details["t2_tokens"],
            details["t3_tokens"], details["t1_tokens"]) == (100, 20, 10, 5, 0)
    assert (details["skills_tokens"], details["prompt_tokens_reported"], details["prefill_seconds"],
            details["load_seconds"]) == (25, 900, 1.5, 0.02)
