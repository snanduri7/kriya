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
