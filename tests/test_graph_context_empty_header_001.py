"""GRAPH-CONTEXT-EMPTY-HEADER-001: an optional Developer section with nothing
to show is no section at all.

Measured in the canonical Graphify run (20260930T073012-85574393): all six
over-capacity retries logged `optional section(s) ['graph_context'] not
found exactly once; kept as mandatory text`, reproduced offline on every
retry. With every graph candidate excluded, build_code_context returned its
bare 46-char header, which every caller's `if context:` guard treats as
content, and the retry's planned-source section (rendered by the same
function) opens with the same header - so the graph text occurred twice,
could not be located, and became ~22 tokens of mandatory text per request."""
from kriya.workflow import context_budget as budget
from kriya.workflow.context_budget import build_code_context


def test_an_empty_graph_context_renders_nothing(tmp_path):
    assert build_code_context([], [], str(tmp_path), 1000) == ""
    (tmp_path / "a.py").write_text("x = 1\n")
    assert build_code_context(["a.py"], [], str(tmp_path), 1000).startswith(
        "\n\n=== Codebase Semantic Reference Context ===\n")


def test_a_left_out_file_still_reports_its_omission(tmp_path):
    (tmp_path / "a.py").write_text("def f():\n" + "    x = 1\n" * 4000)
    rendered = build_code_context(["a.py"], [], str(tmp_path), 1)
    assert "Left out for the context budget" in rendered and "a.py" in rendered


def test_an_empty_graph_section_is_never_registered_so_nothing_is_unlocated(tmp_path):
    """Measured in the canonical run: every over-capacity retry logged
    `optional section(s) ['graph_context'] not found exactly once; kept as
    mandatory text`. With every graph candidate excluded, build_code_context
    returned only its 46-char header, which the retry's planned-source
    section (rendered by the same function) also opens with, so the graph
    text occurred twice. Now an empty graph context is no section at all."""
    from kriya.workflow.attempt import PLANNED_SOURCE_HEADER

    header = "\n\n=== Codebase Semantic Reference Context ===\n"
    (tmp_path / "a.py").write_text("x = 1\n")
    planned = f"\n\n{PLANNED_SOURCE_HEADER}\n" + build_code_context(["a.py"], [], str(tmp_path), 1000)
    graph = build_code_context([], [], str(tmp_path), 1000)
    assert header in planned and graph == ""
    sections = [budget.OptionalSection("planned_source", planned, lambda _b: "")]
    if graph:  # _developer_optional_sections registers a section only when it has text
        sections.append(budget.OptionalSection("graph_context", graph, lambda _b: ""))
    prompt = "TASK" + planned + graph + "END" + "y" * 400
    _fitted, details = budget.fit_developer_request(budget.RequestCapacity(tokens=10), "sys", prompt, sections)
    assert details["unlocated_sections"] == []
