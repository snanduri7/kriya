from kriya.workflow.context_projection import ProjectionLevel, project_implementation_source
from kriya.workflow.edit_safety import content_revision


def test_full_projection_is_revision_bound_and_explicit():
    content = "class App { void stop() {} }\n"
    projection = project_implementation_source(
        content, "App.java", 1000, reason="runtime failure triage",
    )

    assert projection.level is ProjectionLevel.FULL
    assert projection.revision == content_revision(content)
    assert not projection.omitted_regions
    assert "projection=full" in projection.render()


def test_bounded_projection_keeps_head_tail_and_marks_omission():
    content = "imports\n" + ("middle\n" * 100) + "Thread.currentThread().join();\n"
    projection = project_implementation_source(
        content, "Application.java", 180, reason="runtime failure triage",
    )

    assert projection.level is ProjectionLevel.IMPLEMENTATION_EXCERPT
    assert projection.omitted_regions
    assert projection.content.startswith("imports")
    assert projection.content.endswith("Thread.currentThread().join();\n")
    assert "canonical source remains local" in projection.content
