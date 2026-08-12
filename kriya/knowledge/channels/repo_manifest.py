"""Mechanical, zero-LLM, zero-network knowledge channel: reads the exact dependency
coordinates and framework markers RepositoryAnalyzer already found in the target
repo's own manifest files (pom.xml, package.json, requirements.txt, build.gradle),
scoped to only what's actually relevant to the skill being scored.

Deliberately NOT a blanket scan of every dependency in the repo into candidate facts
- that would flood staged_knowledge.json with facts about libraries nobody's
generating code against. Relevance reuses the exact same fact_match check
kriya/workflow/workflow.py already uses to decide skill activation (see
kriya/skills/skill.py::fact_match), so this channel can never surface a dependency
Kriya itself wouldn't already consider that skill relevant for.
"""
from typing import List, NamedTuple

from kriya.analyzer.analyzer import RepositoryModel
from kriya.knowledge.channels.base import KnowledgeChannel
from kriya.knowledge.schema import KnowledgeFact
from kriya.skills.skill import Skill, fact_match


class RepoManifestContext(NamedTuple):
    skill: Skill
    repo_model: RepositoryModel


class RepoManifestChannel(KnowledgeChannel):

    @property
    def name(self) -> str:
        return "repo_manifest"

    async def extract(self, context: RepoManifestContext) -> List[KnowledgeFact]:
        skill, repo_model = context.skill, context.repo_model
        if not fact_match(skill, repo_model):
            return []

        facts: List[KnowledgeFact] = []
        provenance = f"manifest in {repo_model.root_path}"
        matched_deps = set()

        for tag in skill.tags:
            tag_lower = tag.lower()
            for dep in repo_model.dependencies:
                if tag_lower in dep.lower():
                    matched_deps.add(dep)

        for dep in sorted(matched_deps):
            version = repo_model.dependency_versions.get(dep)
            if version:
                facts.append(KnowledgeFact(
                    category="Dependencies",
                    key=dep,
                    value=f"{dep} {version}",
                    source_channel=self.name,
                    extraction_confidence="mechanical",
                    provenance=provenance,
                ))
                facts.append(KnowledgeFact(
                    category="Compatibility",
                    key=dep,
                    value=f"Target project declares '{dep}' at version '{version}'.",
                    source_channel=self.name,
                    extraction_confidence="mechanical",
                    provenance=provenance,
                ))
            else:
                facts.append(KnowledgeFact(
                    category="Dependencies",
                    key=dep,
                    value=dep,
                    source_channel=self.name,
                    extraction_confidence="mechanical",
                    provenance=f"{provenance} (present, no version captured)",
                ))

        matched_frameworks = set()
        for tag in skill.tags:
            tag_lower = tag.lower()
            for framework in repo_model.frameworks:
                if tag_lower in framework.lower():
                    matched_frameworks.add(framework)

        for framework in sorted(matched_frameworks):
            facts.append(KnowledgeFact(
                category="Metadata",
                key=framework,
                value=f"Target project uses '{framework}'.",
                source_channel=self.name,
                extraction_confidence="mechanical",
                provenance=provenance,
            ))

        return facts
