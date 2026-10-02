"""Code Intelligence live closure (live matrix, commons-lang Fraction): the
grounded T0 survives retries.

Live run da5af568 (goal "fix silent int overflow in
Fraction.getFraction(double)"): localization put the four getFraction
overloads first and CI-6 chose getFraction(double); attempt 1 rendered every
overload's exact body. The attempt failed with a response-protocol error -
no failure line, no grounded SEARCH text - and on attempts 2-4 the
Developer saw no exact getFraction body at all: retry member hints came only
from failure evidence, and the path-keyed known-target record held only the
LAST member packed for the file (mulPosAndCheck), which the retry treated as
the file's T0. Three fabricated anchors later the run ended.

End to end through the real direct run (model scripted at the transport):
attempt 1 answers with two outcome blocks (the live
CONFLICTING_DEVELOPER_RESPONSE: no failure line, no SEARCH text to ground); attempt 2 must still be grounded in the exact current
bodies of the members localization found, and an edit of one of them must be
authorized and applied.
"""
import asyncio
import json
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
from kriya.workflow.state import GenerationState
from kriya.workflow.workflow import WorkflowEngine

TARGET = "src/main/java/shop/Ledger.java"
ONE_ARG = "        return amount * rate;"
TWO_ARG = "        return amount * rate + fee;"
OTHER = "        return x * y;"
SOURCE = textwrap.dedent("""\
    package shop;

    public class Ledger {
        private int rate = 3;

        public static int unrelatedHelperOne() {
            return 1;
        }

        public int convert(int amount) {
    ONE_ARG
        }

        public int convert(int amount, int fee) {
    TWO_ARG
        }

        public static int unrelatedHelperTwo() {
            return 2;
        }

        private static int mulCheck(int x, int y) {
    OTHER
        }
    FILLER
    }
    """).replace("ONE_ARG", ONE_ARG).replace("TWO_ARG", TWO_ARG).replace("OTHER", OTHER).replace(
    "FILLER\n", "".join(  # far larger than any prompt budget: the file is never shown whole
        f"\n    /** Ledger rule {i}: rounds a posting the way clause {i} of the policy requires. */\n"
        f"    public static long rule{i}(long posting, long rounding) {{\n"
        f"        long adjusted = posting + rounding * {i};\n"
        f"        return adjusted - adjusted % {i + 2};\n    }}\n" for i in range(400)))
GOAL = "fix silent int overflow in Ledger.convert(int) with mulCheck"
FIXED = "        return mulCheck(amount, rate);"


def _git(repo, *args):
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, check=True,
                   capture_output=True)


def _run(tmp_path):
    model_runtime.clear_model_runtime_cache()
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
    workspace = tmp_path / "ws"
    (workspace / "src/main/java/shop").mkdir(parents=True)
    (workspace / TARGET).write_text(SOURCE)
    _git(workspace, "init", "-q")
    _git(workspace, "add", "-A")
    _git(workspace, "commit", "-qm", "base")
    embedder = StaticEmbedder([0.3, 0.4, 0.5])
    asyncio.run(RepositoryAnalyzer(str(workspace)).index_repository(
        cfg, generate_conventions_skill=False, embedding_client=embedder))
    developer = []

    async def transport(llm, client, model, system_prompt, user_prompt, *args, **kwargs):
        del llm, client, model, args, kwargs
        first = (system_prompt or "").splitlines()[0] if system_prompt else ""
        if "File List Planner" in first:
            content = json.dumps({"files": [TARGET]})
        elif "Developer Agent" in first:
            developer.append(user_prompt)
            content = as_requested(f"FIX ANALYSIS: overflow.\nSEARCH:\n{ONE_ARG}\nREPLACE:\n{FIXED}\n",
                                   system_prompt, TARGET)
            if len(developer) == 1:  # the live attempt 1: two outcome blocks, no failure locator at all
                content += content
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
        result = asyncio.run(engine.run_generation_workflow(
            goal=GOAL, workspace_path=str(workspace), predetermined_plan=f"Repair {TARGET}",
            predetermined_design="", predetermined_architect_files=[TARGET],
            approval_callback=AsyncMock(return_value=True)))
    return workspace, developer, events, result


def test_the_grounded_t0_reaches_every_retry_and_authorizes_its_edit(tmp_path):
    workspace, developer, events, _ = _run(tmp_path)
    assert len(developer) == 2, "attempt 1 fails on its protocol error, attempt 2 repairs"
    first, retry = developer
    for prompt in (first, retry):
        # The overloads localization found, exact and current, on EVERY attempt.
        assert ONE_ARG in prompt and TWO_ARG in prompt
    # The retry's edit of a shown overload body is authorized and applied.
    assert FIXED in (workspace / TARGET).read_text()
    [first_capability, retry_capability] = [e.details for e in events if e.kind == "context.edit_capability"]
    for capability in (first_capability, retry_capability):
        spans = [(s["start_line"], s["end_line"]) for s in capability["targets"][0]["spans"]
                 if s["unit"] == "member_exact"]
        lines = SOURCE.splitlines()
        covered = {lines[i - 1] for start, end in spans for i in range(start, end + 1)}
        assert ONE_ARG in covered and TWO_ARG in covered, spans  # every shown member, not the last one


def test_retries_never_record_another_member_as_the_files_t0(tmp_path):
    _, _, events, _ = _run(tmp_path)
    [retry_source] = [e.details for e in events if e.kind == "context.retry_target_source"
                      and e.details.get("mode") == "targeted"]
    [target] = retry_source["targets"]
    assert target["tier"] == "member_exact" and target["member_id"] == "Ledger.convert"


def test_a_member_unit_authorizes_only_when_its_bytes_are_in_this_request(tmp_path):
    """Every member unit of the latest package counts as shown - but only
    while its exact bytes are in this request's mandatory text (an optional
    section can be trimmed, an earlier package may not be in this prompt)."""
    from test_prd016_adaptive_budget import _attempt_ctx, _cfg

    from kriya.workflow import context_budget as budget
    from kriya.workflow.attempt import _decide_edit_capabilities, _record_known_target_package
    from kriya.workflow.context_package import TrustLevel, make_context_item
    from kriya.workflow.file_integrity import read_shown_text

    source = "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n" + "".join(
        f"\n\ndef rule{i}(x):\n    return x * {i}\n" for i in range(50))
    (tmp_path / "calc.py").write_text(source)
    revision = read_shown_text(str(tmp_path / "calc.py"))[1]
    add_body, sub_body = "def add(a, b):\n    return a + b\n", "def sub(a, b):\n    return a - b\n"

    def item(member_id, body, start):
        return make_context_item(path="calc.py", content=body, reason="known_target_member_exact",
                                 source_type="named_in_request", trust_level=TrustLevel.REPOSITORY, score=1.0,
                                 member_id=member_id, start_line=start, end_line=start + 1, tier="member_exact",
                                 is_exact=True, revision=revision, omitted_regions=False)

    cfg = _cfg()
    cfg.llm.inference_runtime = None
    ctx = _attempt_ctx(tmp_path, cfg, developer=None)

    def spans(context, optional=()):
        state = GenerationState()
        state.attempt_number = 1
        _record_known_target_package(state, [item("add", add_body, 1), item("sub", sub_body, 5)])
        assert state.known_target_context_items["calc.py"].member_id == "add"  # the highest-ranked member
        kwargs = {"known_target_files": ["calc.py"], "existing_code_context": context,
                  "optional_sections": optional}
        return {(s.start_line, s.end_line) for s in _decide_edit_capabilities(state, ctx, kwargs)["calc.py"].spans
                if s.unit == "member_exact"}

    assert spans(f"HEAD\n{add_body}\n{sub_body}") == {(1, 2), (5, 6)}
    assert spans(f"HEAD\n{add_body}") == {(1, 2)}  # sub is not in this request: it authorizes nothing
    optional = (budget.OptionalSection("planned_source", sub_body, lambda _b: ""),)
    assert spans(f"HEAD\n{add_body}\n{sub_body}", optional) == {(1, 2)}
