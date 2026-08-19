from kriya.workflow.context_projection import ProjectionLevel
from kriya.workflow.edit_safety import content_revision
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.retry_package import build_retry_package
from kriya.workflow.retry_prompts import _build_targeted_retry_prompt


def test_retry_package_is_bounded_revision_labelled_and_target_first(tmp_path):
    target = "src/App.java"
    reference = "src/Helper.java"
    (tmp_path / "src").mkdir()
    target_content = "class App {\n" + ("  void work() {}\n" * 1000) + "}\n"
    reference_content = "class Helper {\n" + ("  int value;\n" * 1000) + "}\n"
    (tmp_path / target).write_text(target_content)
    (tmp_path / reference).write_text(reference_content)
    failure = Failure(
        type="compile",
        message="compile failed",
        raw_output="compiler error\n" * 1000,
        file_locations=[FileLocation(target, 20, 5)],
        likely_files=[target],
    )

    package = build_retry_package(
        failure=failure,
        worktree_path=str(tmp_path),
        all_files=[reference, target],
        target_files=[target],
        source_context={target: "source line\n" * 1000},
        max_chars=5000,
        max_error_chars=1000,
    )

    assert len(package.authoritative_error) <= 1000
    assert len(package.render_context()) < 8000
    assert package.target_projections[0].path == target
    assert package.target_projections[0].revision == content_revision(target_content)
    assert package.target_projections[0].level is ProjectionLevel.IMPLEMENTATION_EXCERPT
    assert len(package.source_context[target]) <= 2000
    assert "canonical source remains local" in package.render_context()


def test_targeted_retry_prompt_uses_package_instead_of_unbounded_file_dump(tmp_path):
    target = "App.java"
    huge_middle = "UNBOUNDED_MIDDLE\n" * 2000
    content = "class App {\n" + huge_middle + "}\n"
    (tmp_path / target).write_text(content)
    failure = Failure(
        type="compile", message="failed", raw_output="cannot find symbol",
        likely_files=[target],
    )
    package = build_retry_package(
        failure=failure,
        worktree_path=str(tmp_path),
        all_files=[target],
        target_files=[target],
        source_context={},
        max_chars=1200,
    )

    task, context = _build_targeted_retry_prompt(
        "Fix App", "Plan", "legacy unbounded error", [target], [target],
        str(tmp_path), "base context", retry_package=package,
    )

    assert "Failure type: compile" in task
    assert "cannot find symbol" in task
    assert "legacy unbounded error" not in task
    assert len(context) < len(content)
    assert "revision=" in context
