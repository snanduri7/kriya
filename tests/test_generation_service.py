"""The Developer request assembly and generation (full modularization W4, E6): characterization written and green at
dd8a980, BEFORE the code moves out of ``kriya/workflow/attempt.py``.

What a run hands the Developer is pinned at the two boundaries every attempt mode crosses, for each mode the scripted
model drives (attempt 1 full set, the targeted retries, the fallback-targeted retry, the full-set retries escalated to
the fallback) under both Developer response protocols (``structured``, the production default, and
``legacy_strict``; the scripted answers come from ``tests/_protocol_responses.py`` through ``_scripted_run``):

- ``DeveloperAgent.run_generation``'s keyword arguments as ``_run_developer_generation`` builds them: the routed
  model, the request fit (binding, optional sections and what each rebuilds to), the grounded output expectations,
  the operations, and digests of every text argument;
- the prompt text the model receives (system + user), digested;
- the generation's run events (model.transition, model.fallback_selection, developer.prompt_composition,
  generation.completed/failed) and the LR-R1-M1 fallback decisions;
- the attempt module's own log lines that name the mode (logger ``kriya.workflow.attempt``).

Every value is normalized with the kernel golden harness's ``Normalizer`` (tmp/workspace paths -> tokens, clocks,
durations); the golden ``tests/golden/generation/developer_requests.json`` was WRITTEN from dd8a980
(``KRIYA_WRITE_GENERATION_GOLDEN=1``) and is only ever compared afterwards.
"""
import asyncio
import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from kriya.core.attempt_evidence import scope as attempt_evidence_scope
from kriya.workflow.state import GenerationState

sys.path.insert(0, str(Path(__file__).resolve().parent / "state_machine"))
import _kernel_golden as kg  # noqa: E402 - needs the sys.path entry above  # pylint: disable=wrong-import-position

GOLDEN = Path(__file__).resolve().parent / "golden" / "generation" / "developer_requests.json"
WRITE_ENV = "KRIYA_WRITE_GENERATION_GOLDEN"
GENERATION_EVENTS = frozenset({
    "model.transition", "model.fallback_selection", "model.fallback_incompatible", "developer.prompt_composition",
    "generation.completed", "generation.failed", "model.output_budget_protocol_fallback",
    "model.retry_identity_not_qualified",
})
MODE_LINES = ("Targeted retry", "Fallback-targeted retry", "Escalating compilation attempt", "Missing-file recovery")
REBUILD_BUDGETS = (0, 40, 4000)


@pytest.fixture
def _git_identity(monkeypatch):
    for key, value in kg.GIT_ENV.items():
        monkeypatch.setenv(key, value)


def _digest(text):
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _sizes_free(details):
    """prompt_composition counts tokens of text that quotes tmp paths: keep its shape, not its numbers."""
    if isinstance(details, dict):
        return {key: _sizes_free(value) for key, value in details.items()}
    if isinstance(details, list):
        return [_sizes_free(value) for value in details]
    return "<n>" if isinstance(details, (int, float)) and not isinstance(details, bool) else details


def _request_form(kwargs, norm):
    fit = kwargs.get("request_fit")
    form = {
        "keys": sorted(kwargs),
        "model_override": kwargs.get("model_override"),
        "base_url_override": kwargs.get("base_url_override"),
        "known_target_files": kwargs.get("known_target_files"),
        "operation_by_file": {path: getattr(op, "value", op) for path, op in (kwargs.get("operation_by_file") or {}).items()},
        "expected_output_by_file": {path: [exp.tokens, norm.text(exp.grounding)]
                                    for path, exp in (kwargs.get("expected_output_by_file") or {}).items()},
        "text": {key: _digest(norm.text(value)) for key, value in sorted(kwargs.items()) if isinstance(value, str)},
        "request_fit": None if fit is None else {
            "binding": getattr(fit.binding, "model", None),
            "sections": [[section.kind, _digest(norm.text(section.text)),
                          [_digest(norm.text(section.rebuild(budget))) for budget in REBUILD_BUDGETS]]
                         for section in fit.sections],
        },
    }
    return form


def _capture(tmp_path, protocol):
    """One scripted run whose Developer calls cross every generation mode: the primary keeps failing (attempt 1 full
    set, the targeted retries), then the fallback-targeted retry and the full-set retries escalated to the fallback;
    the eighth call succeeds."""
    from _scripted_run import FALLBACK, engine, git_workspace, script

    calls, decisions, events, modes = [], [], [], []
    with kg.isolated_working_directory(tmp_path) as cwd:
        workspace = git_workspace(tmp_path, {"calc.py": kg.BASE, "test_calc.py": kg.TESTS})
        norm = kg.Normalizer({str(Path(workspace).resolve()): "ws", workspace: "ws", str(tmp_path): "tmp",
                              str(Path(str(tmp_path)).resolve()): "tmp", str(cwd): "cwd"})

        def configure(cfg):
            cfg.autonomy.developer_response_protocol = protocol
        eng, llm, _cfg = engine(chain=True, configure=configure)
        prompts = []

        def developer(model, n, text):
            prompts.append([model, _digest(norm.text(text))])
            return kg.GOOD if (model == FALLBACK and n > 7) else kg.BROKEN
        script(llm, plan="Step 1: add mul", target="calc.py", developer=developer)
        real_generation = eng.developer.run_generation
        attempt_logger = logging.getLogger("kriya.workflow.attempt")
        handler = _ModeLines()
        attempt_logger.addHandler(handler)

        async def spy_generation(**kwargs):
            form = _request_form(kwargs, norm)
            form["mode_line"] = norm.text(handler.last) if handler.last else None
            try:
                return await real_generation(**kwargs)
            finally:
                fit = kwargs.get("request_fit")
                form["fitted_prompts"] = [_digest(norm.text(text)) for text in (fit.fitted if fit is not None else [])]
                calls.append(form)
        eng.developer.run_generation = spy_generation
        real_record_event = GenerationState.record_event
        real_decision = attempt_evidence_scope.record_fallback_decision

        def record_event(state, event):
            if event.kind in GENERATION_EVENTS:
                details = dict(event.details or {})
                details.pop("duration_seconds", None)
                if event.kind == "developer.prompt_composition":
                    details = _sizes_free(details)
                events.append(norm.value({"kind": event.kind, "attempt": event.attempt, "source": event.source,
                                          "authority": event.authority, "message": event.message,
                                          "details": details}))
            return real_record_event(state, event)

        def record_decision(decision):
            decisions.append(norm.value(decision))
            return real_decision(decision)
        try:
            with patch.object(GenerationState, "record_event", new=record_event), \
                    patch.object(attempt_evidence_scope, "record_fallback_decision", new=record_decision):
                result = asyncio.run(eng.run_generation_workflow(
                    goal=kg.GOAL, workspace_path=workspace, approval_callback=MagicMock(return_value=True)))
        finally:
            attempt_logger.removeHandler(handler)
        modes = handler.lines
    return {"quality_gates_passed": result.get("quality_gates_passed"), "developer_calls": calls,
            "model_prompts": prompts, "run_events": events, "fallback_decisions": decisions,
            "mode_lines": [norm.text(line) for line in modes]}


class _ModeLines(logging.Handler):
    """The attempt module's mode lines, in order (the logger name is part of what is pinned)."""

    def __init__(self):
        super().__init__(logging.INFO)
        self.lines, self.last = [], None

    def emit(self, record):
        message = record.getMessage()
        if record.name == "kriya.workflow.attempt" and message.startswith(MODE_LINES):
            self.lines.append(message)
            self.last = message


def _render(form):
    return json.dumps(form, indent=1, sort_keys=True, ensure_ascii=False) + "\n"


@pytest.mark.parametrize("protocol", ["structured", "legacy_strict"])
def test_the_developer_request_of_every_generation_mode_is_byte_identical(protocol, tmp_path, _git_identity, caplog):
    caplog.set_level(logging.INFO, logger="kriya.workflow.attempt")
    form = _capture(tmp_path, protocol)
    rendered = _render(form)
    golden = json.loads(GOLDEN.read_text(encoding="utf-8")) if GOLDEN.exists() else {}
    if os.environ.get(WRITE_ENV):
        golden[protocol] = json.loads(rendered)
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(_render(golden), encoding="utf-8")
        pytest.skip(f"golden written for {protocol}")
    # the scripted trajectory itself (a precondition of the pin, not the pin)
    assert form["quality_gates_passed"] is True
    assert [line.split(":")[0].split(" ")[0] for line in form["mode_lines"]][:1] == ["Targeted"]
    assert any(line.startswith("Fallback-targeted retry") for line in form["mode_lines"])
    assert any(line.startswith("Escalating compilation attempt") for line in form["mode_lines"])
    assert rendered == _render(golden[protocol])


# ----------------------------------------------------------------------------- the qualified retry identity

def test_a_refused_retry_identity_is_recorded_as_its_typed_event_and_logged(tmp_path, monkeypatch, caplog):
    """MODEL-EVIDENCE-HARDENING-001 (written at dd8a980; pre-move mutant R04 survived the existing suite): the
    production refusal of an unqualified retry identity records ONE model.retry_identity_not_qualified run event
    carrying exactly the failure's diagnostics and message, and logs the message through the attempt logger, before
    any request is sent."""
    from unittest.mock import AsyncMock

    from openai.resources.chat.completions import AsyncCompletions
    from test_prd017_fallback_transition import PRIMARY, _ctx, _exact_ollama, _response
    from test_retry_inference_identity import _cfg as _retry_cfg
    from test_retry_inference_identity import _qualify, _retry_kwargs

    from kriya.agents.agent import DeveloperAgent
    from kriya.core.inference_settings import role_inference_settings
    from kriya.core.llm import LLMClient
    from kriya.workflow import attempt
    from kriya.workflow.failure import QualityGateFailure

    _exact_ollama(monkeypatch)
    cfg = _retry_cfg()
    _qualify(cfg, role_inference_settings(cfg, "developer", PRIMARY))   # the normal identity only
    state = GenerationState()
    state.attempt_number = 2
    create = AsyncMock(return_value=_response("class A {}\n"))
    caplog.set_level(logging.ERROR, logger="kriya.workflow.attempt")
    with patch.object(AsyncCompletions, "create", new=create), pytest.raises(QualityGateFailure) as refused:
        asyncio.run(attempt._run_developer_generation(
            state, _ctx(str(tmp_path), cfg, DeveloperAgent("developer", LLMClient(cfg))), **_retry_kwargs(cfg)))
    failure = refused.value.failure
    (event,) = [e for e in state.run_events if e.kind == "model.retry_identity_not_qualified"]
    assert (event.attempt, event.source, event.authority.value) == (2, "attempt._run_developer_generation", "advisory")
    assert event.message == failure.message and event.details == failure.diagnostics
    assert (failure.source, failure.attempt, failure.raw_output) == ("orchestrator", 2, failure.message)
    assert [r.getMessage() for r in caplog.records if r.name == "kriya.workflow.attempt"] == [failure.message]
    create.assert_not_awaited()


# ----------------------------------------------------------------------------- the generation call's own record

def _primary_generation(tmp_path, monkeypatch, outcome, **kwargs):
    """One primary-model generation through the attempt's entry point, the Developer answering ``outcome`` (a value,
    or an exception to raise)."""
    from unittest.mock import AsyncMock

    from test_prd017_fallback_transition import _cfg, _ctx, _exact_ollama

    from kriya.agents.agent import DeveloperAgent
    from kriya.core.llm import LLMClient
    from kriya.workflow import attempt

    _exact_ollama(monkeypatch)
    cfg = _cfg()
    developer = DeveloperAgent("developer", LLMClient(cfg))
    developer.run_generation = AsyncMock(side_effect=outcome) if isinstance(outcome, BaseException) \
        else AsyncMock(return_value=outcome)
    tmp_path.mkdir(parents=True, exist_ok=True)
    source = "class A {\n    void a() {}\n}\n"
    (tmp_path / "A.java").write_text(source)
    state = GenerationState()
    state.attempt_number = 1
    ctx = _ctx(str(tmp_path), cfg, developer)
    try:
        asyncio.run(attempt._run_developer_generation(state, ctx, task_description="t", design_context="d",
                                                      existing_code_context=f"=== File: A.java ===\n{source}",
                                                      known_target_files=["A.java"],
                                                      **kwargs))
    except RuntimeError:
        pass
    return state, developer.run_generation.await_args.kwargs


def test_a_callers_expected_outputs_are_never_replaced_by_the_grounded_ones(tmp_path, monkeypatch):
    """(written at dd8a980; pre-move mutant G08 survived the existing suite) the grounded full-file expectations are
    computed only when the caller passed none: an explicit mapping, even an empty one, is what the Developer gets."""
    marker = {"A.java": "the caller's own expectation"}
    _state, sent = _primary_generation(tmp_path, monkeypatch, [], expected_output_by_file=marker)
    assert sent["expected_output_by_file"] is marker
    _state, sent = _primary_generation(tmp_path / "empty", monkeypatch, [], expected_output_by_file={})
    assert sent["expected_output_by_file"] == {}
    _state, sent = _primary_generation(tmp_path / "grounded", monkeypatch, [])
    (path, expectation), = sent["expected_output_by_file"].items()
    assert path == "A.java" and expectation.grounding.startswith("full-file rewrite of A.java")


@pytest.mark.parametrize("outcome, succeeded, kind", [([], True, "generation.completed"),
                                                      (RuntimeError("model unavailable"), False, "generation.failed")])
def test_the_generation_records_whether_the_call_succeeded(outcome, succeeded, kind, tmp_path, monkeypatch):
    """(written at dd8a980; pre-move mutant G13 survived the existing suite) the timing row and the generation event
    of a call that raised say so: succeeded False and generation.failed."""
    state, _sent = _primary_generation(tmp_path, monkeypatch, outcome)
    (timing,) = state.generation_timings
    assert (timing["succeeded"], timing["file_count"], timing["model"]) == (succeeded, 1, "primary-coder:30b")
    (event,) = [e for e in state.run_events if e.kind.startswith("generation.")]
    assert (event.kind, event.details["file_count"], event.details["model"]) == (kind, 1, "primary-coder:30b")


# ----------------------------------------------------------------------------- pins for the pre-move survivors

def test_an_exhausted_investigation_budget_skips_the_hook_entirely(tmp_path, monkeypatch):
    """DEV-INV-001 (written at dd8a980; pre-move mutant I03 survived): with every investigation turn of this attempt
    already used, the hook does nothing at all - the loop is not entered (a zero-turn loop would leave no visible
    trace either, so the pin is at the loop boundary), no model call, no run event, the turn count unchanged."""
    from unittest.mock import AsyncMock

    from test_dev_inv_001_investigation import _minimal_attempt_ctx, _propose_result

    from kriya.config.config import ModelCapabilities
    from kriya.workflow import attempt, investigation

    entered = []
    real_loop = investigation.run_investigation_loop

    async def loop(**kwargs):
        entered.append(kwargs["max_turns"])
        return await real_loop(**kwargs)
    monkeypatch.setattr(investigation, "run_investigation_loop", loop)
    ctx = _minimal_attempt_ctx(tmp_path)
    autonomy = ctx.kernel.config.autonomy
    autonomy.developer_investigation_enabled, autonomy.developer_investigation_max_turns = True, 2
    ctx.kernel.config.paths.memory = str(tmp_path)
    ctx.kernel.config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    (tmp_path / "dependency_graph.db").write_bytes(b"")
    ctx.developer.llm = MagicMock()
    ctx.developer.llm.complete_with_tools = AsyncMock(return_value=_propose_result())
    state = GenerationState()
    state.investigation_turns_used_by_attempt[state.attempt_number] = 2
    kwargs = {"existing_code_context": "original", "task_description": "t", "design_context": "d"}
    asyncio.run(attempt._maybe_run_developer_investigation(state, ctx, kwargs, ctx.kernel.config.llm.model))
    ctx.developer.llm.complete_with_tools.assert_not_awaited()
    assert entered == [] and state.run_events == []
    assert state.investigation_turns_used_by_attempt == {state.attempt_number: 2}
    assert kwargs == {"existing_code_context": "original", "task_description": "t", "design_context": "d"}


def test_a_target_the_caller_already_hinted_is_never_refit_with_goal_members(tmp_path):
    """KNOWN-TARGET-GOAL-NAMED-MEMBER-HINT-001 (written at dd8a980; pre-move mutant T04 survived): an unfit target
    that already carries a member hint keeps it; the goal's named members are added only for unhinted unfit targets."""
    from test_dev_inv_001_investigation import _minimal_attempt_ctx

    from kriya.workflow import attempt

    filler = "".join(f"    public int helper{i}(int a) {{\n        return a + {i};\n    }}\n\n" for i in range(400))
    (tmp_path / "Element.java").write_text(
        "package org.jsoup.nodes;\n\npublic class Element {\n" + filler
        + "    public String absUrl(String key) {\n        return key;\n    }\n}\n")
    ctx = _minimal_attempt_ctx(tmp_path, goal="Element.absUrl(...) resolves relative links incorrectly.",
                               retrieval_member_hints={})
    _rendered, _package, unhinted = attempt._target_package_with_goal_member_fallback(
        ctx, ["Element.java"], 900, 8000, {})
    assert unhinted is not None and unhinted["unfit_targets"] == ["Element.java"]   # the control: it is unfit
    _rendered, _package, hinted = attempt._target_package_with_goal_member_fallback(
        ctx, ["Element.java"], 900, 8000, {"Element.java": ["Element.notAMember()"]})
    assert hinted is None


def test_the_graph_section_rebuilds_without_the_excluded_paths(tmp_path):
    """DEVELOPER-PROMPT-FIT-001 (written at dd8a980; pre-move mutant S04 survived): the graph-context section, rebuilt
    smaller, is rebuilt from the same candidates minus the paths a more authoritative producer already shows."""
    from types import SimpleNamespace

    from kriya.workflow import attempt
    from kriya.workflow.context_source import SourceDerivationCache

    (tmp_path / "shown.py").write_text("def shown():\n    return 'SHOWN-ELSEWHERE'\n")
    (tmp_path / "other.py").write_text("def other():\n    return 'ONLY-IN-GRAPH'\n")
    ctx = SimpleNamespace(planned_source_files=(), worktree_path=str(tmp_path), source_cache=SourceDerivationCache(),
                          matched_files=["shown.py", "other.py"], related_files=["shown.py"])
    (section,) = attempt._developer_optional_sections(ctx, "graph context text", {"shown.py"}, "")
    rebuilt = section.rebuild(4000)
    assert section.kind == "graph_context" and "ONLY-IN-GRAPH" in rebuilt and "SHOWN-ELSEWHERE" not in rebuilt


def test_the_graph_section_rebuilds_from_the_matched_and_the_related_candidates_each_in_its_own_place(tmp_path):
    """DEVELOPER-PROMPT-FIT-001 (orchestrator, after E6; orchestrator mutant g3 survived): the rebuilt graph section
    passes the matched candidates and the related candidates to the code-context builder in that order, so a file only
    the related list names appears in the rebuilt text exactly as it does when the section is first built."""
    from types import SimpleNamespace

    from kriya.workflow import attempt
    from kriya.workflow.context_source import SourceDerivationCache

    (tmp_path / "m.py").write_text("def matched_fn():\n    return 'MATCHED-MARK'\n")
    (tmp_path / "r.py").write_text("def related_fn():\n    return 'RELATED-MARK'\n")
    ctx = SimpleNamespace(planned_source_files=(), worktree_path=str(tmp_path), source_cache=SourceDerivationCache(),
                          matched_files=["m.py"], related_files=["r.py"])
    (section,) = attempt._developer_optional_sections(ctx, "graph context text", set(), "")
    rebuilt = section.rebuild(4000)
    assert "MATCHED-MARK" in rebuilt and "RELATED-MARK" in rebuilt


def test_the_investigation_gets_the_failure_grounded_member_hints_and_its_events_are_recorded(tmp_path, monkeypatch):
    """DEV-INV-001 (written at dd8a980; pre-move mutants I05, I09 and I11 survived): the loop is told which member the
    last failure grounds in each declared target (SOURCE 2, _resolve_retry_member_hints) and only the turns this
    attempt has left; every event it returns is recorded on the run, in order, and its turns are added to the
    attempt's count."""
    from types import SimpleNamespace

    from test_dev_inv_001_investigation import _minimal_attempt_ctx

    from kriya.config.config import ModelCapabilities
    from kriya.workflow import attempt, investigation
    from kriya.workflow.context_source import resolve_member_hints_from_failure_location
    from kriya.workflow.failure import Failure, FileLocation
    from kriya.workflow.run_events import EventAuthority, RunEvent

    source = "class Foo:\n    def bar(self):\n        return 1 / 0\n\n    def baz(self):\n        return 2\n"
    (tmp_path / "target.py").write_text(source)
    ctx = _minimal_attempt_ctx(tmp_path)
    ctx.kernel.config.autonomy.developer_investigation_enabled = True
    ctx.kernel.config.paths.memory = str(tmp_path)
    ctx.kernel.config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    (tmp_path / "dependency_graph.db").write_bytes(b"")
    state = GenerationState()
    state.attempt_number = 2
    state.investigation_turns_used_by_attempt[2] = 1   # one turn of this attempt already spent
    state.last_failure = Failure(type="test", message="ZeroDivisionError", raw_output="target.py:3",
                                 file_locations=[FileLocation("target.py", 3)], attempt=1)
    events = [RunEvent(kind="investigation.turn", attempt=2, source="investigation", authority=EventAuthority.ADVISORY,
                       message="one", details={}),
              RunEvent(kind="investigation.completed", attempt=2, source="investigation",
                       authority=EventAuthority.ADVISORY, message="two", details={})]
    seen = {}

    async def loop(**kwargs):
        seen.update(kwargs)
        return SimpleNamespace(turns_used=1, events=list(events), evidence=[])
    monkeypatch.setattr(investigation, "run_investigation_loop", loop)
    kwargs = {"existing_code_context": "", "task_description": "t", "design_context": "d",
              "known_target_files": ["target.py"]}
    asyncio.run(attempt._maybe_run_developer_investigation(state, ctx, kwargs, ctx.kernel.config.llm.model))
    expected = [c.member_id for c in resolve_member_hints_from_failure_location("target.py", source, 3)]
    assert expected and seen["known_target_member_hints"] == {"target.py": expected}
    assert ctx.kernel.config.autonomy.developer_investigation_max_turns > 1
    assert seen["max_turns"] == ctx.kernel.config.autonomy.developer_investigation_max_turns - 1
    assert state.run_events == events and state.investigation_turns_used_by_attempt == {2: 2}


def test_planned_files_with_nothing_to_show_add_no_header(tmp_path):
    """GRAPHIFY-OVERSIZE-REQUEST-001 (written at dd8a980; pre-move mutant S06 survived): when the planned files render
    to nothing (here: none of them exists yet), the planned-source context is empty - never a bare header - and no
    optional section is registered for it."""
    from types import SimpleNamespace

    from kriya.workflow import attempt
    from kriya.workflow.context_source import SourceDerivationCache

    ctx = SimpleNamespace(planned_source_files=("not_yet_created.py",), worktree_path=str(tmp_path),
                          source_cache=SourceDerivationCache(), matched_files=[], related_files=[])
    assert attempt._planned_source_context(ctx, set()) == ""
    assert attempt._developer_optional_sections(ctx, "", set(), "", attempt._planned_source_context(ctx, set())) == ()


def _investigating_ctx(tmp_path, *, indexed=True):
    from test_dev_inv_001_investigation import _minimal_attempt_ctx

    from kriya.config.config import ModelCapabilities

    ctx = _minimal_attempt_ctx(tmp_path)
    ctx.kernel.config.autonomy.developer_investigation_enabled = True
    ctx.kernel.config.paths.memory = str(tmp_path)
    ctx.kernel.config.llm.capabilities = ModelCapabilities(native_tool_calls=True)
    if indexed:
        (tmp_path / "dependency_graph.db").write_bytes(b"")
    return ctx


def test_an_unindexed_workspace_never_runs_the_investigation(tmp_path, monkeypatch):
    """DEV-INV-001 (written at dd8a980; pre-move mutant I02 survived): enabled but with no dependency-graph index, the
    loop is never entered."""
    from kriya.workflow import attempt, investigation

    calls = []

    async def loop(**kwargs):
        calls.append(kwargs)
    monkeypatch.setattr(investigation, "run_investigation_loop", loop)
    state = GenerationState()
    kwargs = {"existing_code_context": "original", "task_description": "t", "design_context": "d"}
    asyncio.run(attempt._maybe_run_developer_investigation(state, _investigating_ctx(tmp_path, indexed=False), kwargs,
                                                           "some-model"))
    assert calls == [] and state.run_events == [] and kwargs["existing_code_context"] == "original"


def test_investigation_evidence_is_appended_and_registered_as_an_optional_section(tmp_path, monkeypatch):
    """DEV-INV-001 / DEVELOPER-PROMPT-FIT-001 (written at dd8a980; pre-move mutant I08 survived): rendered evidence is
    appended to the code context AND registered, after the caller's sections, as the "investigation" optional section
    that re-renders itself within a smaller budget."""
    from types import SimpleNamespace

    from kriya.workflow import attempt, investigation
    from kriya.workflow.context_budget import OptionalSection
    from kriya.workflow.context_package import make_context_item

    item = make_context_item(path="notes.py", content="def helper():\n    return 'EVIDENCE'\n", reason="investigation",
                             source_type="developer_investigation", trust_level="repository")

    async def loop(**_kwargs):
        return SimpleNamespace(turns_used=1, events=[], evidence=[item])
    monkeypatch.setattr(investigation, "run_investigation_loop", loop)
    caller = OptionalSection("graph_context", "graph", lambda budget: "")
    kwargs = {"existing_code_context": "original", "task_description": "t", "design_context": "d",
              "optional_sections": (caller,)}
    asyncio.run(attempt._maybe_run_developer_investigation(GenerationState(), _investigating_ctx(tmp_path), kwargs,
                                                           "some-model"))
    assert kwargs["existing_code_context"].startswith("original") and "EVIDENCE" in kwargs["existing_code_context"]
    first, added = kwargs["optional_sections"]
    assert first is caller and added.kind == "investigation"
    assert added.text == kwargs["existing_code_context"][len("original"):]
    assert added.rebuild(10) == investigation.render_investigation_evidence([item], char_budget=40)


def test_the_investigations_code_search_records_an_unavailable_semantic_leg(tmp_path, monkeypatch):
    """DEV-INV-001 / EMBEDDING-CONTRACT-001 (written at dd8a980; pre-move mutant I12 survived): the search_code the
    hook hands the loop answers lexically when the embedding is unavailable and records that as a typed advisory
    retrieval.semantic_unavailable event of this attempt."""
    from types import SimpleNamespace

    from kriya.memory.vector import LocalVectorStore
    from kriya.workflow import attempt, graph_retrieval, investigation

    async def unavailable(_client, _store, _query, availability, _deadline=None):
        availability.semantic_unavailable = "EMBEDDING_UNAVAILABLE"
        return None, None
    monkeypatch.setattr(graph_retrieval, "semantic_query_embedding", unavailable)
    LocalVectorStore(str(tmp_path / "vector_index.db")).close()
    answers = []

    async def loop(**kwargs):
        answers.append(await kwargs["deps"].search_code("helper"))
        return SimpleNamespace(turns_used=1, events=[], evidence=[])
    monkeypatch.setattr(investigation, "run_investigation_loop", loop)
    state = GenerationState()
    state.attempt_number = 3
    asyncio.run(attempt._maybe_run_developer_investigation(
        state, _investigating_ctx(tmp_path), {"existing_code_context": "", "task_description": "t"}, "some-model"))
    assert answers == [[]]
    (event,) = state.run_events
    assert (event.kind, event.attempt, event.source, event.authority.value) == (
        "retrieval.semantic_unavailable", 3, "investigation.search_code", "advisory")
    assert event.details == {"reason_code": "EMBEDDING_UNAVAILABLE"}
    assert event.message == "semantic search unavailable (EMBEDDING_UNAVAILABLE); lexical only"



def test_the_optional_sections_keep_their_registration_order(tmp_path):
    """DEVELOPER-PROMPT-FIT-001 (post-move mutant P04 survived every tier; green at dd8a980 and after the move): the
    sections are registered planned source, graph context, fenced learned reference - in that order, each only when it
    has text (the fit itself orders by kind, but the request fit and its LR-R1-M1 records carry this tuple)."""
    from types import SimpleNamespace

    from kriya.workflow import attempt
    from kriya.workflow.context_source import SourceDerivationCache

    (tmp_path / "planned.py").write_text("def planned():\n    return 1\n")
    ctx = SimpleNamespace(planned_source_files=("planned.py",), worktree_path=str(tmp_path),
                          source_cache=SourceDerivationCache(), matched_files=[], related_files=[])
    planned = attempt._planned_source_context(ctx, set())
    sections = attempt._developer_optional_sections(ctx, "graph text", set(), "a learned lesson", planned)
    assert [section.kind for section in sections] == ["planned_source", "graph_context", "learned_reference"]
    assert sections[0].text == planned and sections[1].text == "graph text"
    assert [s.kind for s in attempt._developer_optional_sections(ctx, "", set(), "a learned lesson", "")] == [
        "learned_reference"]

# ----------------------------------------------------------------------------- the seam (E6 move)

def _injected_names(deps_class):
    import dataclasses

    return [f.name for f in dataclasses.fields(deps_class)]


def test_every_generation_collaborator_is_attempts_own_binding_resolved_at_call_time():
    """The moved code reaches each collaborator through kriya.workflow.attempt: every GenerationDeps / FallbackDeps
    field is the attempt module's object of the same name (a loader field is its ``_load_*`` function), looked up when
    the builder runs (the dynamic proof that a patch entered after import reaches the moved code is the existing patch
    sites: tests/test_developer_prompt_fit_001.py, test_context_edit_protocol_001.py, test_backend_readiness_004_
    relocalization.py, test_known_target_multi_target_starvation_001.py, tests/test_fallback_decision.py, and
    tests/test_kernel_seams.py's call-through of every patched attempt name in the legacy_success run)."""
    from kriya.workflow import attempt, fallback_decision, generation_service

    for build, deps_class in ((attempt._generation_deps, generation_service.GenerationDeps),
                              (attempt._fallback_deps, fallback_decision.FallbackDeps)):
        deps = build()
        for name in _injected_names(deps_class):
            source = "_" + name if name.startswith("load_") else name
            assert getattr(deps, name) is getattr(attempt, source), name
    assert len(_injected_names(generation_service.GenerationDeps)) == 45
    assert len(_injected_names(fallback_decision.FallbackDeps)) == 11


def test_the_forwarders_keep_the_attempt_names_and_kinds():
    """attempt.py keeps every moved name: the patched and called ones as forwarders of the same kind defined in
    attempt.py, the pure ones and the planned-source constants as the owner's objects (re-exports)."""
    import inspect

    from kriya.workflow import attempt, fallback_decision, generation_service

    forwarders = {
        "_maybe_run_developer_investigation": True, "_developer_request_profile": False, "_enter_developer_model": False,
        "_require_qualified_retry_identity": False, "_planned_source_context": False,
        "_developer_optional_sections": False, "_target_package_with_goal_member_fallback": False,
        "_target_package_with_window_reserve": False, "_run_developer_generation": True,
        "_run_developer_generation_as_developer": True, "_record_fallback_selection": False,
        "_raise_fallback_incompatible": False, "_select_developer_fallback": False,
        "_substitute_for_required_patch": False,
    }
    for name, is_async in forwarders.items():
        forwarder = getattr(attempt, name)
        assert forwarder.__module__ == "kriya.workflow.attempt" and inspect.iscoroutinefunction(forwarder) is is_async
        owner = generation_service if hasattr(generation_service, name) else fallback_decision
        assert inspect.iscoroutinefunction(getattr(owner, name)) is is_async
        assert list(inspect.signature(getattr(owner, name)).parameters)[0] == "deps"
    assert attempt._chain_binding is generation_service._chain_binding
    assert attempt._fallback_rejection is fallback_decision._fallback_rejection
    assert attempt.PLANNED_SOURCE_HEADER is generation_service.PLANNED_SOURCE_HEADER
    assert attempt._PLANNED_SOURCE_UNBOUNDED_TOKENS is generation_service._PLANNED_SOURCE_UNBOUNDED_TOKENS
