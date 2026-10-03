"""CAGC-0 tripwire (KRIYA_CAGC v0.7 §12.3): Kriya's language-neutral role
prompts carry no stack-specific text and no incident narrative. Stack
semantics reach a model only as capability guidance selected for the evidence
the request actually carries."""
import ast
import pathlib
import re

import pytest

from kriya.agents.agent import ArchitectAgent, DeveloperAgent, MilestonePlannerAgent, PlannerAgent, ReviewerAgent
from kriya.workflow.workflow_controller import AUTHORITATIVE_PLANNER_SYSTEM_PROMPT, build_authoritative_planner_request

_KRIYA = pathlib.Path(__file__).resolve().parents[1] / "kriya"

FORBIDDEN = re.compile(r"(?i)\b(?:maven|gradle|spring|ignite|jms|pytest)\b|pom\.xml|build\.gradle|@Transactional"
                       r"|confirmed live")
NARRATIVE = re.compile(r"(?i)confirmed live|real (?:live )?incident|real, repeated failure")

# Every allowlisted string constant, by the function (or module constant) that
# owns it, with the reason it may name a stack. Starts from what CAGC-0 kept.
ALLOWLIST = {
    "RunVerifierAgent.system_prompt": "Run Verification Judge (not a CAGC R1 role): choosing the runtime "
                                      "command from the shown build file is its contract (D3/D4)",
    "RunVerifierAgent.judge": "fact builder: renders the actual build file or states its absence",
    "ECOSYSTEM_INVARIANT_HEADER": "language-neutral ecosystem-preservation invariant (KRIYA_CAGC §12.2: keep); "
                                  "its stack names are contrasting examples, not guidance for one stack",
}


def _base_prompts():
    reviewer = ReviewerAgent("reviewer", None)
    return {
        "planner": PlannerAgent("planner", None).system_prompt,
        "architect": ArchitectAgent("architect", None).system_prompt,
        "developer": DeveloperAgent("developer", None).system_prompt,
        "reviewer": reviewer.system_prompt,
        "reviewer.rejected_candidate": reviewer.rejected_candidate_system_prompt("gates failed"),
        "structured_planner.system": AUTHORITATIVE_PLANNER_SYSTEM_PROMPT,
        "structured_planner.request": build_authoritative_planner_request(
            "Add a feature.", route_kind=_route_kind(), extension_candidates=["a/b.txt"],
            repository_candidates=["a/b.txt"], structural_evidence=""),
        "milestone_planner": MilestonePlannerAgent("milestone_planner", None).system_prompt,
    }


def _route_kind():
    from kriya.workflow.triage import ChangeKind

    return next(iter(ChangeKind))


@pytest.mark.parametrize("name", sorted(_base_prompts()))
def test_rendered_base_prompts_are_stack_neutral(name):
    text = _base_prompts()[name]
    assert not FORBIDDEN.findall(text), (name, FORBIDDEN.findall(text))


def _owner(node, parents):
    names = []
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            names.append(node.targets[0].id)
    return ".".join(reversed(names))


def _string_constants(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                  if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                  and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings \
                and len(node.value) > 40:
            yield _owner(node, parents), node.lineno, node.value


SCANNED = [_KRIYA / "agents" / "agent.py", _KRIYA / "workflow" / "retry_prompts.py",
           _KRIYA / "workflow" / "workflow_controller.py"]


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_string_constants_are_stack_neutral_outside_the_allowlist(path):
    offenders = [(owner, line, sorted(set(FORBIDDEN.findall(text))))
                 for owner, line, text in _string_constants(path)
                 if FORBIDDEN.search(text) and not any(owner == key or owner.endswith("." + key) or
                                                       owner.startswith(key + ".") for key in ALLOWLIST)]
    assert offenders == []


@pytest.mark.parametrize("path", SCANNED, ids=lambda p: p.name)
def test_no_incident_narrative_in_any_string_constant(path):
    """No allowlist: an incident narrative never belongs in a model prompt."""
    assert [(owner, line) for owner, line, text in _string_constants(path) if NARRATIVE.search(text)] == []


def test_every_allowlist_entry_is_still_needed():
    owners = set()
    for path in SCANNED:
        owners |= {owner for owner, _line, text in _string_constants(path) if FORBIDDEN.search(text)}
    for key in ALLOWLIST:
        assert any(o == key or o.endswith("." + key) or o.startswith(key + ".") for o in owners), key
