"""MA6.4: subtask context projection (kriya/workflow/subtask_context_projection.py) -
first real pytest coverage for this module."""

from kriya.workflow.context_package import build_context_package, make_context_item
from kriya.workflow.plan_schema import ExecutionMethod, FileAction, PlannedFile, Subtask
from kriya.workflow.subtask_context_projection import project_for_subtask


def _subtask(*planned_paths):
    return Subtask(
        id="s1", description="do a thing", execution_method=ExecutionMethod.MODEL,
        planned_files=[PlannedFile(path=p, action=FileAction.MODIFY) for p in planned_paths],
    )


def test_direct_hit_file_is_kept():
    item = make_context_item(
        path="a.py", content="print(1)", reason="named in request",
        source_type="named_in_request", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    projected = project_for_subtask(package, _subtask("a.py"))
    assert [i.path for i in projected.relevant_files] == ["a.py"]
    assert projected.omitted == ()


def test_unrelated_file_is_omitted_with_a_real_reason():
    item = make_context_item(
        path="unrelated.py", content="x = 1", reason="semantic hit",
        source_type="semantic_hit", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    projected = project_for_subtask(package, _subtask("a.py"))
    assert projected.relevant_files == ()
    assert len(projected.omitted) == 1
    assert projected.omitted[0]["path"] == "unrelated.py"
    assert "narrowed out of subtask 's1'" in projected.omitted[0]["reason"]


def test_relational_source_type_naming_a_target_in_its_reason_is_kept():
    item = make_context_item(
        path="dep.py", content="def helper(): ...",
        reason="graph dependency of a.py", source_type="graph_dependency", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    projected = project_for_subtask(package, _subtask("a.py"))
    assert [i.path for i in projected.relevant_files] == ["dep.py"]


def test_non_relational_source_type_naming_a_target_is_still_dropped():
    """semantic_hit is not in _RELATIONAL_SOURCE_TYPES - even if its reason
    text happens to mention a target file, it's dropped: only a genuine
    dependency-relationship source_type earns the indirect keep."""
    item = make_context_item(
        path="coincidence.py", content="x = 1",
        reason="mentions a.py in passing", source_type="semantic_hit", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    projected = project_for_subtask(package, _subtask("a.py"))
    assert projected.relevant_files == ()


def test_relational_source_type_naming_a_target_by_basename_is_kept():
    item = make_context_item(
        path="src/dep.py", content="...",
        reason="lsp reference from a.py", source_type="lsp_reference", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    projected = project_for_subtask(package, _subtask("some/nested/a.py"))
    assert [i.path for i in projected.relevant_files] == ["src/dep.py"]


def test_artifact_entry_with_no_path_fields_is_kept_fail_open():
    package = build_context_package(artifact_entries=({"kind": "package_manager"},))
    projected = project_for_subtask(package, _subtask("a.py"))
    assert projected.artifact_entries == ({"kind": "package_manager"},)


def test_artifact_entry_with_overlapping_module_path_is_kept():
    package = build_context_package(artifact_entries=({"module_path": "src"},))
    projected = project_for_subtask(package, _subtask("src/a.py"))
    assert projected.artifact_entries == ({"module_path": "src"},)


def test_artifact_entry_with_non_overlapping_module_path_is_dropped():
    package = build_context_package(artifact_entries=({"module_path": "other"},))
    projected = project_for_subtask(package, _subtask("src/a.py"))
    assert projected.artifact_entries == ()


def test_project_for_subtask_never_mutates_the_original_package():
    item = make_context_item(
        path="a.py", content="x", reason="r", source_type="named_in_request", trust_level="repository",
    )
    package = build_context_package(relevant_files=(item,))
    original_hash = package.package_hash
    project_for_subtask(package, _subtask("a.py"))
    assert package.package_hash == original_hash
    assert package.relevant_files == (item,)


def test_conventions_and_spec_slice_are_always_preserved():
    package = build_context_package(conventions={"style": "PEP8"}, spec_slice={"section": "auth"})
    projected = project_for_subtask(package, _subtask("a.py"))
    assert projected.conventions == {"style": "PEP8"}
    assert projected.spec_slice == {"section": "auth"}
