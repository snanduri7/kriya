"""CAGC-0 evidence (KRIYA_CAGC v0.7 §9, §10.1, §14 test_cagc_evidence): one
``capability.guidance`` event per wired role request, recorded AFTER the
request's final fit and describing the exact block sent (digest and token
count of the text the model received); none for the Milestone Planner."""
import ast
import hashlib
import json
import pathlib
import re

import pytest
import test_prompt_budget_fit_001ab as harness
from test_prompt_budget_fit_001ab import REPORT, _enforce, _run

from kriya.capabilities.guidance.render import FOOTER, HEADER
from kriya.workflow.context_budget import estimate_tokens

_BLOCK = re.compile(r"\n\n" + re.escape(HEADER) + r"\n.*?" + re.escape(FOOTER) + r"\n", re.DOTALL)
_KRIYA = pathlib.Path(__file__).resolve().parents[1] / "kriya"


class _ArchitectListsTheReport(harness.Transport):
    """The harness transport, with an Architect that names its file (so the
    first Developer attempt has an authorized target)."""

    async def __call__(self, client, model, system_prompt, user_prompt, *args, **kwargs):
        if (system_prompt or "").startswith("You are the Kriya Architect Agent"):
            self.requests.append(("You are the Kriya Architect Agent.", system_prompt, user_prompt or ""))
            content = f"Design: create {REPORT}.\n```json\n" + json.dumps({"files": [REPORT]}) + "\n```"
            return {"content": content, "reasoning_chars": 0, "prompt_tokens": harness.plausible_prompt_tokens(system_prompt, user_prompt), "completion_tokens": 5,
                    "finish_reason": "stop", "provider_metadata": {}}
        return await super().__call__(client, model, system_prompt, user_prompt, *args, **kwargs)


@pytest.fixture(autouse=True)
def _architect_file_list(monkeypatch):
    monkeypatch.setattr(harness, "Transport", _ArchitectListsTheReport)


def _guidance_events(run, request):
    return [event.details for event in run.events
            if event.kind == "capability.guidance" and event.details.get("request") == request]


def _sent_blocks(run, *markers):
    """The guidance block text of every request that reached the transport."""
    blocks = []
    for _system, user in run.transport.by(*markers):
        found = _BLOCK.findall(user)
        assert len(found) <= 1
        blocks.append(found[0] if found else "")
    return blocks


def _matches(event, text):
    return event["digest"] == hashlib.sha256(text.encode()).hexdigest() and \
        event["estimated_tokens"] == estimate_tokens(text)


def test_every_wired_request_reports_the_block_it_sent(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 32768)
    assert run.result.get("quality_gates_passed") is True

    # Planner: an existing Maven repository's matched Java files.
    [planner] = _guidance_events(run, "planner")
    [planner_text] = _sent_blocks(run, "Planner Agent")
    assert planner["role"] == "planner" and planner["operation"] == "plan" and planner["context"] == "existing"
    assert "maven.plan.existing_topology_preserved" in planner["rule_ids"] and _matches(planner, planner_text)
    assert "maven.plan.greenfield_minimal_topology" not in planner["rule_ids"]

    # Architect: same selection inputs, its own rules (none apply here).
    [architect] = _guidance_events(run, "architect")
    [architect_text] = _sent_blocks(run, "Architect Agent")
    assert architect["role"] == "architect" and architect["rule_ids"] == [] and architect_text == ""
    assert "maven@1" in architect["selected_capability_ids"] and _matches(architect, "")

    # Developer: a new Java file -> CREATE, the Java import-style rule.
    developer = _guidance_events(run, "developer")
    sent = _sent_blocks(run, "Developer Agent")
    assert developer and len(developer) == len(sent)
    for event, text in zip(developer, sent, strict=True):
        assert event["operation"] == "create" and event["rule_ids"] == ["java.dev.import_style"]
        assert _matches(event, text)

    # Reviewer: a Java file with no Spring or build-file target -> selected, nothing rendered.
    reviews = [event for event in run.events if event.kind == "capability.guidance"
               and str(event.details.get("request", "")).startswith("reviewer.")]
    assert reviews and all(e.details["role"] == "reviewer" and e.details["operation"] == "review" for e in reviews)
    assert all(e.details["selected_capability_ids"] == ["java@1"] and e.details["estimated_tokens"] == 0
               for e in reviews)


def test_the_event_is_recorded_after_the_request_is_fitted(tmp_path, monkeypatch):
    """At 8K the Planner's optional sections are reduced: its guidance event
    still describes exactly the text that request carried."""
    run = _run(tmp_path, monkeypatch, 8192)
    [planner] = _guidance_events(run, "planner")
    [planner_text] = _sent_blocks(run, "Planner Agent")
    assert _matches(planner, planner_text)
    [fit] = run.fits("planner")
    # At 8K the graph context is left out: nothing it named selects guidance.
    assert fit["graph"]["omitted"] and planner["selected_capability_ids"] == [] and planner_text == ""
    developer = _guidance_events(run, "developer")
    for event, text in zip(developer, _sent_blocks(run, "Developer Agent"), strict=True):
        assert _matches(event, text)


def test_fully_fit_dropped_guidance_records_selection_and_fit_drops(tmp_path, monkeypatch):
    """A Planner request with no room for guidance: the event keeps the
    selected ids and the fit-dropped rule, with zero tokens sent."""
    from kriya.workflow import context_budget

    real = context_budget.fit_guidance_section

    def no_room(capacity, fixed_texts, section):
        return real(context_budget.RequestCapacity(tokens=0), fixed_texts, section)

    monkeypatch.setattr(context_budget, "fit_guidance_section", no_room)
    run = _run(tmp_path, monkeypatch, 32768)
    [planner] = _guidance_events(run, "planner")
    assert planner["estimated_tokens"] == 0 and planner["rule_ids"] == [] and planner["rendered_capability_ids"] == []
    assert "maven@1" in planner["selected_capability_ids"]
    assert planner["dropped_by_fit_rule_ids"] == ["maven.plan.existing_topology_preserved"]
    assert planner["digest"] == hashlib.sha256(b"").hexdigest()
    assert _sent_blocks(run, "Planner Agent") == [""]


def test_the_enforce_planner_records_a_decision_per_request(tmp_path):
    requests, _capacity, _result = _enforce(tmp_path, 32768, "")
    path = tmp_path / "enforce" / ".kriya"
    decisions = [json.loads(line) for file in path.rglob("decisions*.jsonl") for line in file.read_text().splitlines()]
    guidance = [d for d in decisions if d.get("type") == "capability.guidance"]
    assert len(guidance) == len(requests) == 2  # the first request and one repair round
    for decision in guidance:
        assert decision["request"] == "structured_planner" and decision["role"] == "planner"
        assert decision["digest"] == hashlib.sha256(b"").hexdigest()


def test_the_milestone_planner_is_not_wired():
    """KRIYA_CAGC §2/§7: the Milestone Planner gets no guidance in R1
    (MILESTONE-PLANNER-ROLE-BINDING-001 decides its binding first)."""
    tree = ast.parse((_KRIYA / "workflow" / "milestones.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | \
        {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not names & {"compose_section", "compose_guidance", "fit_guidance_section", "run_event_recorder"}
    source = (_KRIYA / "workflow" / "milestones.py").read_text(encoding="utf-8")
    assert "capability_guidance" not in source


def test_guidance_events_are_advisory():
    from kriya.workflow.capability_guidance import run_event_recorder
    from kriya.workflow.run_events import EventAuthority

    recorded = []
    state = type("S", (), {"attempt_number": 3, "record_event": lambda self, e: recorded.append(e)})()
    from kriya.capabilities.guidance import Operation, RepositoryFacts, Role, compose_guidance, selection_facts

    facts = selection_facts(Role.DEVELOPER, Operation.REPAIR, RepositoryFacts(False, False, ()), ["A.java"],
                            lambda _p: None)
    block = compose_guidance(facts)
    run_event_recorder(state, "developer", model="m")(facts, block)
    [event] = recorded
    assert event.authority is EventAuthority.ADVISORY and event.attempt == 3
    assert event.details["model"] == "m" and event.details["rule_ids"] == ["java.dev.import_style"]


def test_the_final_review_of_a_failing_candidate_reports_its_guidance(tmp_path, monkeypatch):
    run = _run(tmp_path, monkeypatch, 32768, compile_error=harness.COMPILE_ERROR)
    finals = _guidance_events(run, "reviewer.final")
    assert finals and all(event["selected_capability_ids"] == ["java@1"] for event in finals)
    assert all(_matches(event, "") for event in finals)
