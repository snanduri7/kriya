"""MA5.8: compatibility projections (kriya/workflow/context_projection.py) -
project_established_file_context/project_established_dependencies bridge
the new control-plane types (ContextPackage, ArtifactRegistry) into the
EXACT string/list shapes run_generation_workflow() already consumes via
MilestoneRunState, so it can adopt the new control plane without being
rewritten."""

from kriya.control.artifacts import ArtifactRecord, ArtifactRegistry
from kriya.workflow.context_orchestrator import SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT
from kriya.workflow.context_package import build_context_package, make_context_item
from kriya.workflow.context_projection import (
    project_established_dependencies,
    project_established_file_context,
    render_established_file_context,
)


# --- project_established_file_context ---

def test_project_established_file_context_matches_render_established_file_context_exactly():
    """Same real content through both paths must produce byte-identical
    output - that's the whole compatibility promise."""
    established = {"Protocol.java": "public class Protocol {}"}
    direct = render_established_file_context(established)

    item = make_context_item(
        path="Protocol.java", content="public class Protocol {}",
        reason="established output", source_type=SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT,
        trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    via_package = project_established_file_context(package)

    assert via_package == direct


def test_project_established_file_context_excludes_non_established_items():
    """A semantic_hit/contract_provider item must never be misrepresented
    as 'already built by an earlier milestone.'"""
    established_item = make_context_item(
        path="Protocol.java", content="class Protocol {}", reason="x",
        source_type=SOURCE_TYPE_ESTABLISHED_MILESTONE_OUTPUT, trust_level="repository",
    )
    other_item = make_context_item(
        path="unrelated.py", content="print(1)", reason="x",
        source_type="semantic_hit", trust_level="repository",
    )
    package = build_context_package(relevant_files=(established_item, other_item))
    result = project_established_file_context(package)
    assert "Protocol.java" in result
    assert "unrelated.py" not in result


def test_project_established_file_context_empty_package_is_blank():
    package = build_context_package()
    assert project_established_file_context(package) == ""


# --- project_established_dependencies ---

def test_project_established_dependencies_matches_get_pom_dependencies_shape():
    """Real Kriya code (kriya/tools/validate.py's get_pom_dependencies)
    produces plain 'groupId:artifactId' strings - this projection must
    match that shape exactly, not invent a new one."""
    registry = ArtifactRegistry()
    registry.record(ArtifactRecord(
        milestone_id="M1", ecosystem="maven", kind="library",
        coordinates={"groupId": "com.example", "artifactId": "protocol-lib", "version": "1.0"},
    ))
    assert project_established_dependencies(registry) == ["com.example:protocol-lib"]


def test_project_established_dependencies_falls_back_to_bare_artifact_id_without_group():
    registry = ArtifactRegistry()
    registry.record(ArtifactRecord(
        milestone_id="M1", ecosystem="maven", kind="library",
        coordinates={"artifactId": "standalone-lib"},
    ))
    assert project_established_dependencies(registry) == ["standalone-lib"]


def test_project_established_dependencies_uses_name_for_non_maven_ecosystems():
    registry = ArtifactRegistry()
    registry.record(ArtifactRecord(
        milestone_id="M1", ecosystem="npm", kind="library", coordinates={"name": "my-pkg", "version": "1.0.0"},
    ))
    assert project_established_dependencies(registry) == ["my-pkg"]


def test_project_established_dependencies_is_sorted_and_deduplicated():
    registry = ArtifactRegistry()
    registry.record(ArtifactRecord(
        milestone_id="M1", ecosystem="maven", kind="library",
        coordinates={"groupId": "com.b", "artifactId": "z"},
    ))
    registry.record(ArtifactRecord(
        milestone_id="M2", ecosystem="maven", kind="library",
        coordinates={"groupId": "com.a", "artifactId": "y"},
    ))
    registry.record(ArtifactRecord(
        milestone_id="M3", ecosystem="maven", kind="library",
        coordinates={"groupId": "com.b", "artifactId": "z"},
    ))
    assert project_established_dependencies(registry) == ["com.a:y", "com.b:z"]


def test_project_established_dependencies_empty_registry_is_empty_list():
    assert project_established_dependencies(ArtifactRegistry()) == []
