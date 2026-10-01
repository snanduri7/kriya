"""GRAPHIFY-OVERSIZE-REQUEST-001: a bounded enforce subtask's planned file
reached the Developer as verbatim, mandatory text.

Measured (Graphify control run 20260929T181026-68927268, re-rendered offline
at c6ec798): WorkflowController._build_context read the planned file's full
on-disk content (engine.py, 331 KB), _render_context_package rendered it into
supplementary_context, which became skills_prompt and so the Developer's
mandatory existing_code_context - 156,805 of the request's 168,814 dispatch
tokens, beside the budgeted 1K known-target skeleton of the same file. No
request fit could shrink it, so every attempt was refused before inference
(CONTEXT_BUDGET_UNSATISFIABLE). Removing only that item: 12,009 tokens.

Now the planned file's path goes to the attempt, which renders its current
source through build_code_context's tiers as the first optional Developer
section: whole whenever the request fits, degraded rather than unsendable,
and never beside a known-target rendering of the same path."""
import ast
import pathlib
from types import SimpleNamespace

from _strict_doubles import strict_config

from kriya.workflow import attempt
from kriya.workflow.context_budget import (
    DEVELOPER_SECTION_ORDER,
    SourceDerivationCache,
    fit_developer_request,
    request_capacity,
)
from kriya.workflow.context_package import build_context_package, make_context_item
from kriya.workflow.subtask_executor import _render_context_package
from kriya.workflow.workflow_controller import PLANNED_FILE_SOURCE_REASON, split_planned_source

SYSTEM = "You are a senior software engineer. Return the requested file edit."
MANDATORY = ("=== Authoritative Goal ===\nFix the generic call edge.\n\n=== Task ===\nModify big.py only.\n\n"
             "=== Existing Code Base Context ===\n")


def _big_source(functions=3000):
    return "".join(f"def handler_{i}(node, source):\n    value = node.child({i})\n    return value\n\n"
                   for i in range(functions))


def _workspace(tmp_path, source):
    (tmp_path / "big.py").write_text(source, encoding="utf-8")
    return str(tmp_path)


def _ctx(worktree, planned=("big.py",)):
    return SimpleNamespace(planned_source_files=tuple(planned), worktree_path=worktree,
                           source_cache=SourceDerivationCache(), matched_files=[], related_files=[])


def _package(worktree, source):
    item = make_context_item(path="big.py", content=source, reason=PLANNED_FILE_SOURCE_REASON,
                             source_type="named_in_request", trust_level="repository")
    other = make_context_item(path="notes.md", content="design note", reason="named dependency",
                              source_type="named_in_request", trust_level="repository")
    return build_context_package(relevant_files=(item, other))


def _capacity():
    return request_capacity(strict_config(), None)


def test_the_split_keeps_planned_source_out_of_the_rendered_context(tmp_path):
    source = _big_source()
    paths, rest = split_planned_source(_package(_workspace(tmp_path, source), source))
    rendered = _render_context_package(rest)
    assert paths == ["big.py"]
    assert source not in rendered and "def handler_17" not in rendered
    assert "design note" in rendered  # every other item is still rendered


def test_a_large_planned_file_no_longer_makes_the_request_unsendable(tmp_path):
    source = _big_source()
    worktree = _workspace(tmp_path, source)
    capacity = _capacity()
    # Pre-fix composition (the measured mechanism): the whole file as mandatory text.
    before = MANDATORY + _render_context_package(_package(worktree, source))
    assert capacity.count(SYSTEM) + capacity.count(before) > capacity.tokens
    # Post-fix composition: mandatory text without it + the optional planned-source section.
    ctx = _ctx(worktree)
    planned = attempt._planned_source_context(ctx, set())
    assert source in planned  # rendered whole before any fitting
    prompt = MANDATORY + _render_context_package(split_planned_source(_package(worktree, source))[1]) + planned
    sections = attempt._developer_optional_sections(ctx, "", set(), "", planned)
    fitted, details = fit_developer_request(capacity, SYSTEM, prompt, sections)
    assert capacity.count(SYSTEM) + capacity.count(fitted) <= capacity.tokens
    assert details["sections"]["planned_source"]["reduced"] is True
    assert attempt.PLANNED_SOURCE_HEADER in fitted and "big.py" in fitted


def test_a_planned_file_that_fits_is_sent_whole_and_unchanged(tmp_path):
    source = _big_source(functions=20)
    worktree = _workspace(tmp_path, source)
    ctx = _ctx(worktree)
    planned = attempt._planned_source_context(ctx, set())
    prompt = MANDATORY + planned
    fitted, details = fit_developer_request(_capacity(), SYSTEM, prompt,
                                            attempt._developer_optional_sections(ctx, "", set(), "", planned))
    assert fitted == prompt and details == {} and source in fitted


def test_a_path_already_represented_by_a_known_target_is_never_rendered_twice(tmp_path):
    worktree = _workspace(tmp_path, _big_source(functions=20))
    ctx = _ctx(worktree)
    assert attempt._planned_source_context(ctx, {"big.py"}) == ""
    assert attempt._developer_optional_sections(ctx, "", {"big.py"}, "", "") == ()


def test_no_planned_source_outside_a_bounded_subtask(tmp_path):
    assert attempt._planned_source_context(_ctx(str(tmp_path), planned=()), set()) == ""


def test_the_planned_source_gets_room_first():
    assert DEVELOPER_SECTION_ORDER[0] == "planned_source"


_KRIYA = pathlib.Path(__file__).resolve().parents[1] / "kriya"


def test_the_bounded_subtask_hands_planned_paths_to_the_attempt():
    """The enforce subtask call passes planned_source_files and renders its
    context only after split_planned_source."""
    tree = ast.parse((_KRIYA / "workflow" / "workflow_controller.py").read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "attr", None) == "run_generation_workflow"
             and any(kw.arg == "supplementary_context" for kw in node.keywords)]
    assert calls and all(any(kw.arg == "planned_source_files" for kw in call.keywords) for call in calls)
    renders = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
               and getattr(node.func, "attr", None) == "_render_context_package"]
    assert renders and all(isinstance(call.args[0], ast.Name) and call.args[0].id == "rendered_context"
                           for call in renders)


def test_every_developer_context_branch_includes_and_registers_the_planned_source():
    source = (_KRIYA / "workflow" / "attempt.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and getattr(node.func, "id", None) == "_developer_optional_sections"]
    assert len(calls) >= 5
    assert all(len(call.args) == 5 and getattr(call.args[4], "id", None) == "planned_source" for call in calls)
    assert source.count("_planned_source_context(ctx, _graph_exclude)") == 4
