"""CAGC selection (§6.1): one predicate per capability, bound by one closed
map. There is no fallback selector, no string-prefix inference, no dynamic
discovery and no dependency expansion: a capability is selected only by its
own predicate."""
from __future__ import annotations

import os
from types import MappingProxyType
from typing import List, Sequence, Tuple

from kriya.capabilities import GRADLE, MAVEN, PIP
from kriya.capabilities.gradle import BUILD_SCRIPT as GRADLE_BUILD_SCRIPT
from kriya.capabilities.guidance.facts import SelectionFacts
from kriya.capabilities.guidance.model import PLANNING_ROLES, CapabilityGuidance, GuidanceRule
from kriya.capabilities.maven import POM as MAVEN_POM
from kriya.capabilities.pip import MARKERS as PIP_MARKERS
from kriya.code_intel.config_parsing import PROPERTIES, SPRING_XML, YAML
from kriya.code_intel.model import CONFIG_KEY, SPRING_XML_KINDS
from kriya.code_intel.parsing import JAVA as JAVA_LANG
from kriya.code_intel.parsing import PYTHON as PYTHON_LANG

SPRING_STEREOTYPES = frozenset({
    "Component", "Service", "Repository", "Controller", "RestController", "Configuration", "Bean", "Cacheable",
    "CacheEvict", "CachePut", "Transactional", "Value", "ConfigurationProperties", "Autowired", "RequestMapping",
    "GetMapping", "PostMapping", "PutMapping", "DeleteMapping", "SpringBootApplication",
})


def _planning(f: SelectionFacts) -> bool:
    return f.role in PLANNING_ROLES


def _names_target(f: SelectionFacts, names: Sequence[str]) -> bool:
    return any(os.path.basename(path) in names for path in f.target_paths)


def java(f: SelectionFacts) -> bool:
    return JAVA_LANG in f.target_languages


def spring(f: SelectionFacts) -> bool:
    return java(f) and (bool(f.target_annotations & SPRING_STEREOTYPES)
                        or (f.spring_repository and f.target_imports_spring))


def spring_xml(f: SelectionFacts) -> bool:
    return SPRING_XML in f.target_languages or bool(f.target_symbol_kinds & SPRING_XML_KINDS)


def spring_config(f: SelectionFacts) -> bool:
    return f.spring_repository and (bool({PROPERTIES, YAML} & f.target_languages)
                                    or CONFIG_KEY in f.target_symbol_kinds)


def maven(f: SelectionFacts) -> bool:
    return _names_target(f, (MAVEN_POM,)) or (_planning(f) and MAVEN.build_system in f.build_systems)


def gradle(f: SelectionFacts) -> bool:
    return _names_target(f, (GRADLE_BUILD_SCRIPT,)) or (_planning(f) and GRADLE.build_system in f.build_systems)


def python(f: SelectionFacts) -> bool:
    return PYTHON_LANG in f.target_languages


def pip(f: SelectionFacts) -> bool:
    return _names_target(f, PIP_MARKERS) or (_planning(f) and PIP.build_system in f.build_systems)


CAPABILITY_SELECTORS = MappingProxyType({
    "java": java,
    "spring": spring,
    "spring_xml": spring_xml,
    "spring_config": spring_config,
    "maven": maven,
    "gradle": gradle,
    "python": python,
    "pip": pip,
})


def _applies(rule: GuidanceRule, f: SelectionFacts) -> bool:
    return (f.role in rule.roles
            and (not rule.operations or f.operation in rule.operations)
            and (not rule.contexts or f.context in rule.contexts))


def select(facts: SelectionFacts, registry: Sequence[CapabilityGuidance],
           ) -> List[Tuple[CapabilityGuidance, Tuple[GuidanceRule, ...]]]:
    """Each selected capability with its rules that apply to this request
    (role, operation and context filter). A registry capability without a
    selector is a programming error, never silently skipped."""
    selected = []
    for capability in registry:
        if CAPABILITY_SELECTORS[capability.capability_id](facts):
            selected.append((capability, tuple(rule for rule in capability.rules if _applies(rule, facts))))
    return selected
