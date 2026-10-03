"""CAGC-0 registry contract (KRIYA_CAGC v0.7 §4, §6, §12, §14)."""
import ast
import importlib
import os
import re

import pytest

from kriya.capabilities.guidance import REGISTRY, Role, registry_without
from kriya.capabilities.guidance.selection import CAPABILITY_SELECTORS

RULE_ID = re.compile(r"^[a-z_]+\.(plan|dev|review)\.[a-z_]+$")
NARRATIVE_MARKERS = re.compile(r"(?i)\b(confirmed|live|incident|found|run \w|observed)\b|\d{4}-\d{2}-\d{2}")
PROJECT_NAMES = ("Ignite", "Qpid", "petclinic", "commons-lang", "httpx", "more-itertools", "graphify")

# Every migrated rule keeps its source prompt's role (§12.2).
MIGRATED_ROLES = {
    "java.dev.import_style": {Role.DEVELOPER},
    "maven.plan.greenfield_minimal_topology": {Role.PLANNER},
    "maven.plan.existing_topology_preserved": {Role.PLANNER},
    "maven.plan.greenfield_manifest_required": {Role.ARCHITECT},
    "gradle.plan.greenfield_minimal_topology": {Role.PLANNER},
    "gradle.plan.existing_topology_preserved": {Role.PLANNER},
    "gradle.plan.greenfield_manifest_required": {Role.ARCHITECT},
    "spring_xml.plan.extend_existing_context": {Role.ARCHITECT},
    "maven.review.manifest_care": {Role.REVIEWER},
    "spring.review.transactional_self_invocation": {Role.REVIEWER},
}

ALL_RULES = [rule for capability in REGISTRY for rule in capability.rules]


def test_registry_is_closed_and_ids_unique():
    capability_ids = [capability.capability_id for capability in REGISTRY]
    assert len(capability_ids) == len(set(capability_ids))
    rule_ids = [rule.rule_id for rule in ALL_RULES]
    assert len(rule_ids) == len(set(rule_ids))
    assert isinstance(REGISTRY, tuple)


def test_selector_map_covers_exactly_the_registry():
    assert set(CAPABILITY_SELECTORS) == {capability.capability_id for capability in REGISTRY}


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.rule_id)
def test_rule_shape(rule):
    assert RULE_ID.match(rule.rule_id)
    capability = rule.rule_id.split(".")[0]
    assert any(c.capability_id == capability and rule in c.rules for c in REGISTRY)
    assert rule.roles, "a rule names its roles explicitly"
    assert len(rule.text) <= 200
    assert rule.text.count(". ") == 0 and rule.text.endswith(".") and "\n" not in rule.text
    assert not NARRATIVE_MARKERS.search(rule.text)
    assert not any(name.lower() in rule.text.lower() for name in PROJECT_NAMES)
    assert 0 <= rule.priority <= 99


@pytest.mark.parametrize("rule", ALL_RULES, ids=lambda rule: rule.rule_id)
def test_evidence_resolves(rule):
    assert rule.evidence.startswith("CAGC-MIGRATED-")
    module, attribute = rule.evidence[len("CAGC-MIGRATED-"):].split(":")
    target = importlib.import_module(module)
    for name in attribute.split("."):
        target = getattr(target, name)


def test_migrated_rules_keep_their_source_role():
    assert {rule.rule_id: set(rule.roles) for rule in ALL_RULES} == MIGRATED_ROLES


def test_registry_without_removes_one_rule_and_refuses_unknown_ids():
    ablated = registry_without("java.dev.import_style")
    assert "java.dev.import_style" not in {r.rule_id for c in ablated for r in c.rules}
    assert len([r for c in ablated for r in c.rules]) == len(ALL_RULES) - 1
    with pytest.raises(KeyError):
        registry_without("java.dev.not_a_rule")


def test_workflow_and_agents_import_only_the_public_api():
    """kriya/workflow/** and kriya/agents/** import kriya.capabilities.guidance
    itself, never its modules."""
    root = os.path.join(os.path.dirname(__file__), "..", "kriya")
    offenders = []
    for package in ("workflow", "agents"):
        for directory, _dirs, files in os.walk(os.path.join(root, package)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(directory, name)
                with open(path, encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
                            "kriya.capabilities.guidance."):
                        offenders.append(f"{path}:{node.lineno}")
    assert offenders == []
