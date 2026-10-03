"""CAGC-0 selection (KRIYA_CAGC v0.7 §6, §14 test_cagc_selection)."""
import pytest

from kriya.capabilities.guidance import (
    REGISTRY,
    CapabilityGuidance,
    Domain,
    GuidanceRule,
    Operation,
    RepoContext,
    Role,
    SelectionFacts,
    compose_guidance,
)
from kriya.capabilities.guidance.facts import BuildRootFact, RepositoryFacts, selection_facts
from kriya.capabilities.guidance.selection import CAPABILITY_SELECTORS, select

SPRING_XML = (b'<beans xmlns="http://www.springframework.org/schema/beans">'
              b'<bean id="petTypes" class="a.B"/></beans>')
POM = b'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion></project>'
FILES = {
    "src/main/java/a/Svc.java": b"package a;\nimport org.springframework.stereotype.Service;\n@Service\nclass Svc {}",
    "src/main/java/a/Plain.java": b"package a;\nimport java.util.List;\nclass Plain {}",
    "src/main/java/a/Imports.java": b"package a;\nimport org.springframework.util.Assert;\nclass Imports {}",
    "src/main/java/a/Tx.java": b"package a;\nclass Tx { @Transactional void f() {} }",
    "src/main/resources/ctx.xml": SPRING_XML,
    "src/main/resources/application.properties": b"server.port=8080\n",
    "src/main/resources/application.yml": b"server:\n  port: 8080\n",
    "pom.xml": POM,
    "build.gradle": b"apply plugin: 'java'\n",
    "build.gradle.kts": b"plugins { java }\n",
    "tool/app.py": b"import os\n",
    "requirements.txt": b"httpx\n",
    "Pipfile": b"[packages]\n",
    "setup.cfg": b"[metadata]\n",
}
MAVEN_ROOT = (BuildRootFact("", frozenset({"maven"})),)
GRADLE_ROOT = (BuildRootFact("", frozenset({"gradle"})),)


def _facts(role, targets, *, spring=False, greenfield=False, roots=(), goal=(), operation=None):
    operation = operation or {Role.PLANNER: Operation.PLAN, Role.ARCHITECT: Operation.PLAN,
                              Role.REVIEWER: Operation.REVIEW, Role.DEVELOPER: Operation.EDIT}[role]
    repo = RepositoryFacts(spring, greenfield, tuple(roots), frozenset(goal))
    return selection_facts(role, operation, repo, targets, FILES.get)


def _selected(facts):
    return {capability.capability_id for capability, _rules in select(facts, REGISTRY)}


def _rules(facts):
    return set(compose_guidance(facts).rule_ids)


CASES = [
    # (id, role, targets, kwargs, expected selected capabilities)
    ("python-in-spring-repo", Role.DEVELOPER, ["tool/app.py"], {"spring": True}, {"python"}),
    ("pure-spring-xml", Role.DEVELOPER, ["src/main/resources/ctx.xml"], {"spring": True}, {"spring_xml"}),
    ("spring-xml-not-spring-by-name-alone", Role.ARCHITECT, ["src/main/resources/ctx.xml"], {"spring": True},
     {"spring_xml"}),
    ("stereotype-selects-spring", Role.REVIEWER, ["src/main/java/a/Svc.java"], {}, {"java", "spring"}),
    ("transactional-selects-spring", Role.REVIEWER, ["src/main/java/a/Tx.java"], {}, {"java", "spring"}),
    ("spring-import-in-spring-repo", Role.REVIEWER, ["src/main/java/a/Imports.java"], {"spring": True},
     {"java", "spring"}),
    ("spring-import-in-non-spring-repo", Role.REVIEWER, ["src/main/java/a/Imports.java"], {}, {"java"}),
    ("non-spring-java", Role.REVIEWER, ["src/main/java/a/Plain.java"], {"spring": True}, {"java"}),
    ("config-key-in-non-spring-repo", Role.DEVELOPER, ["src/main/resources/application.properties"], {}, set()),
    ("properties-in-xml-only-spring-repo", Role.DEVELOPER, ["src/main/resources/application.properties"],
     {"spring": True}, {"spring_config"}),
    ("yaml-in-spring-repo", Role.DEVELOPER, ["src/main/resources/application.yml"], {"spring": True},
     {"spring_config"}),
    ("pom-target-selects-maven-for-developer", Role.DEVELOPER, ["pom.xml"], {"spring": True}, {"maven"}),
    ("developer-maven-root-not-maven", Role.DEVELOPER, ["src/main/java/a/Plain.java"], {"roots": MAVEN_ROOT},
     {"java"}),
    ("reviewer-maven-root-not-maven", Role.REVIEWER, ["src/main/java/a/Plain.java"], {"roots": MAVEN_ROOT},
     {"java"}),
    ("planner-maven-root-selects-maven", Role.PLANNER, ["src/main/java/a/Plain.java"], {"roots": MAVEN_ROOT},
     {"java", "maven"}),
    ("architect-maven-root-selects-maven", Role.ARCHITECT, ["src/main/java/a/Plain.java"], {"roots": MAVEN_ROOT},
     {"java", "maven"}),
    ("build-gradle-selects-gradle", Role.DEVELOPER, ["build.gradle"], {}, {"gradle"}),
    ("kotlin-dsl-is-not-gradle", Role.DEVELOPER, ["build.gradle.kts"], {}, set()),
    ("planner-gradle-root", Role.PLANNER, ["src/main/java/a/Plain.java"], {"roots": GRADLE_ROOT},
     {"java", "gradle"}),
    ("pipfile-selects-pip", Role.DEVELOPER, ["Pipfile"], {}, {"pip"}),
    ("requirements-selects-pip", Role.DEVELOPER, ["requirements.txt"], {}, {"pip"}),
    ("setup-cfg-selects-pip", Role.DEVELOPER, ["setup.cfg"], {}, {"pip"}),
    ("empty-facts", Role.PLANNER, [], {}, set()),
    ("greenfield-goal-maven", Role.PLANNER, [], {"greenfield": True, "goal": {"maven"}}, {"maven"}),
    ("greenfield-goal-maven-developer", Role.DEVELOPER, [], {"greenfield": True, "goal": {"maven"}}, set()),
    ("new-java-file-is-java", Role.DEVELOPER, ["src/main/java/a/New.java"], {}, {"java"}),
    ("mixed-batch", Role.DEVELOPER, ["tool/app.py", "src/main/java/a/Svc.java"], {}, {"python", "java", "spring"}),
]


@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_selection_cases(case):
    _name, role, targets, kwargs, expected = case
    assert _selected(_facts(role, targets, **kwargs)) == expected


def test_architect_maven_greenfield_gets_the_manifest_rule():
    facts = _facts(Role.ARCHITECT, [], greenfield=True, goal={"maven"})
    assert _rules(facts) == {"maven.plan.greenfield_manifest_required"}


def test_architect_spring_xml_candidate_gets_extend_existing_context_when_existing():
    facts = _facts(Role.ARCHITECT, ["src/main/resources/ctx.xml"], spring=True, roots=MAVEN_ROOT)
    assert _rules(facts) == {"spring_xml.plan.extend_existing_context"}


def test_planner_gets_neither_architect_rule():
    existing = _facts(Role.PLANNER, ["src/main/resources/ctx.xml"], spring=True, roots=MAVEN_ROOT)
    greenfield = _facts(Role.PLANNER, [], greenfield=True, goal={"maven"})
    assert _rules(existing) == {"maven.plan.existing_topology_preserved"}
    assert _rules(greenfield) == {"maven.plan.greenfield_minimal_topology"}


def test_context_filter_keeps_greenfield_rules_out_of_existing_requests():
    existing = _facts(Role.ARCHITECT, ["src/main/java/a/Plain.java"], roots=MAVEN_ROOT)
    assert "maven.plan.greenfield_manifest_required" not in _rules(existing)
    assert "maven" in _selected(existing)


def test_reviewer_rules():
    assert _rules(_facts(Role.REVIEWER, ["src/main/java/a/Tx.java"])) == \
        {"spring.review.transactional_self_invocation"}
    assert _rules(_facts(Role.REVIEWER, ["pom.xml"])) == {"maven.review.manifest_care"}


def test_developer_java_rule_only_for_java():
    assert _rules(_facts(Role.DEVELOPER, ["src/main/java/a/Plain.java"])) == {"java.dev.import_style"}
    assert _rules(_facts(Role.DEVELOPER, ["tool/app.py"])) == set()


def test_operation_filter_is_applied():
    rule = GuidanceRule("java.dev.only_repair", "Repair it.", frozenset({Role.DEVELOPER}),
                        frozenset({Operation.REPAIR}), frozenset(), 1, "test")
    registry = (CapabilityGuidance("java", 1, Domain.LANGUAGE, (rule,)),)
    edit = _facts(Role.DEVELOPER, ["src/main/java/a/Plain.java"], operation=Operation.EDIT)
    repair = _facts(Role.DEVELOPER, ["src/main/java/a/Plain.java"], operation=Operation.REPAIR)
    assert compose_guidance(edit, registry=registry).rule_ids == ()
    assert compose_guidance(repair, registry=registry).rule_ids == ("java.dev.only_repair",)


def test_no_dependency_expansion_spring_xml_never_pulls_java():
    facts = _facts(Role.DEVELOPER, ["src/main/resources/ctx.xml"], spring=True, roots=MAVEN_ROOT)
    assert "java" not in _selected(facts)
    assert compose_guidance(facts).rule_ids == ()


def test_a_capability_without_a_selector_is_an_error():
    registry = (CapabilityGuidance("ruby", 1, Domain.LANGUAGE, ()),)
    with pytest.raises(KeyError):
        select(_facts(Role.DEVELOPER, ["tool/app.py"]), registry)


def test_each_predicate_is_exercised_true_and_false():
    seen = {name: set() for name in CAPABILITY_SELECTORS}
    for _name, role, targets, kwargs, _expected in CASES:
        facts = _facts(role, targets, **kwargs)
        for name, predicate in CAPABILITY_SELECTORS.items():
            seen[name].add(predicate(facts))
    assert all(values == {True, False} for values in seen.values()), seen


def test_selection_facts_role_operation_and_context_flow_through():
    facts = _facts(Role.REVIEWER, ["pom.xml"], greenfield=True)
    assert isinstance(facts, SelectionFacts)
    assert (facts.role, facts.operation, facts.context) == (Role.REVIEWER, Operation.REVIEW, RepoContext.GREENFIELD)


def _synthetic(**overrides):
    values = dict(role=Role.DEVELOPER, operation=Operation.EDIT, context=RepoContext.EXISTING, target_paths=(),
                  target_languages=frozenset(), target_symbol_kinds=frozenset(), target_annotations=frozenset(),
                  target_imports_spring=False, build_systems=frozenset(), spring_repository=False)
    values.update(overrides)
    return SelectionFacts(**values)


def test_spring_requires_java_even_with_a_stereotype_name():
    """A decorator or annotation named like a Spring stereotype outside Java
    (a Python class decorated @Service) is not Spring."""
    spring = CAPABILITY_SELECTORS["spring"]
    assert not spring(_synthetic(target_languages=frozenset({"python"}), target_annotations=frozenset({"Service"})))
    assert spring(_synthetic(target_languages=frozenset({"java"}), target_annotations=frozenset({"Service"})))


def test_spring_xml_selected_by_either_language_or_kinds():
    spring_xml = CAPABILITY_SELECTORS["spring_xml"]
    assert spring_xml(_synthetic(target_languages=frozenset({"spring-xml"})))
    assert spring_xml(_synthetic(target_symbol_kinds=frozenset({"bean"})))
    assert not spring_xml(_synthetic(target_symbol_kinds=frozenset({"config_key", "class"})))
