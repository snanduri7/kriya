"""The Current Project View is rendered for EACH role-chain candidate's own budget (e7f0a5a; owner ruling 2026-11-08).

``fit_planner_request`` renders the view inside the request it builds, from THAT candidate's own RequestCapacity: a smaller fallback must never receive a rendering sized for the larger
first model (an unsatisfiable request), and a larger candidate must never be starved by a rendering cached for a smaller one. The contract is stated by what each part of the request
IS - the view is ONE fenced block in the Workspace Context (head) area, ahead of the reference; the reference/source text is ONE separate fenced block; neither is nested in or repeated
into the other or into another surface - and by the candidate-specific content level (full content, names only, fewer names), never by how many fences the request carries.
"""
from __future__ import annotations

import pytest

from kriya.repository.project_view import (
    AMBIGUOUS_UNTRACKED,
    KRIYA_ACCEPTED,
    TRACKED,
    ProjectFile,
    ProjectView,
    render_project_view,
)
from kriya.workflow import context_budget as budget
from kriya.workflow.context_budget import RequestCapacity, fit_planner_request
from kriya.workflow.untrusted_context import (
    UNTRUSTED_REFERENCE_BEGIN,
    UNTRUSTED_REFERENCE_END,
    fence_untrusted_reference,
)

VIEW_HEADER = "=== Current Project View"
SYSTEM = "You are the planner. " * 5
HEAD = "Goal: change the greeting.\n\nWorkspace Context:\n{}\n"
SUFFIX = "\nReturn the plan.\n"
REFERENCE = "\n[Source: https://docs.example/a (Fetched: 2026-11-08)]\nLedger guidance.\n"      # raw reference text: fit_planner_request fences it (the production caller passes it raw)
FILES = 14


def _view() -> ProjectView:
    entries = []
    for number in range(FILES):
        klass = (TRACKED, KRIYA_ACCEPTED, AMBIGUOUS_UNTRACKED)[number % 3]
        body = f"UNIQUE_BODY_{number:02d} " + "def f():\n    return 1\n" * 60       # ~1.4K chars of content per file: the full rendering is ~20K chars
        entries.append(ProjectFile(path=f"pkg/module_{number:02d}.py", klass=klass, kriya_accepted=klass == KRIYA_ACCEPTED, kind="file", size=len(body),
                                   digest="d" * 64, content=None if klass == AMBIGUOUS_UNTRACKED else body,
                                   withheld="AMBIGUOUS_UNTRACKED" if klass == AMBIGUOUS_UNTRACKED else None))
    return ProjectView(files=tuple(entries), omitted_paths=0, contents_included=True)


def _fit(tokens: int, view: ProjectView, *, reference: str = REFERENCE):
    capacity = RequestCapacity(tokens=tokens)
    prompt, _fit_details = fit_planner_request(
        capacity, system_prompt=SYSTEM, head=HEAD, skills_prompt="", graph_context="", reference=reference, suffix=SUFFIX,
        rebuild_graph=lambda _room: ("", None), project_view=view)
    return capacity, prompt


def _blocks(prompt: str):
    return [chunk.split(UNTRUSTED_REFERENCE_END)[0] for chunk in prompt.split(UNTRUSTED_REFERENCE_BEGIN)[1:]]


# the candidates: a tiny window, a small one (8K class), a medium one (16K class) and a roomy one. Token counts are RequestCapacity units (prompt room after output and margin).
TINY, SMALL, MEDIUM, ROOMY = 600, 3_000, 9_000, 60_000


@pytest.mark.parametrize("tokens", [TINY, SMALL, MEDIUM, ROOMY])
def test_each_candidate_receives_the_view_rendered_for_its_own_room(tokens):
    view = _view()
    capacity, prompt = _fit(tokens, view)
    bodies = _blocks(prompt)
    views = [body for body in bodies if VIEW_HEADER in body]
    references = [body for body in bodies if "[Source: " in body]
    # the request fits THIS candidate (never an unsatisfiable request) ...
    assert capacity.count(SYSTEM + prompt) <= tokens
    # ... the reference/source context: once, its marker semantics intact; the view: at most once, in the Workspace Context area ahead of the reference, never nested or repeated ...
    assert len(references) == 1 and len(views) <= 1 and prompt.count(VIEW_HEADER) == len(views)     # the reference/source context: exactly once at every budget
    assert all("[Source: " not in body for body in views) and all(VIEW_HEADER not in body for body in references)
    assert len(bodies) == len(views) + len(references)
    if views:
        assert prompt.index("Workspace Context:") < prompt.index(views[0])
        assert not references or prompt.index(views[0]) < prompt.index(references[0])
        assert prompt.index(views[0]) + len(views[0]) <= prompt.index(SUFFIX)       # inside the head, not smuggled into the suffix
        # ... and it is EXACTLY the rendering for this candidate's room (what the renderer would give for it, nothing cached from another candidate)
        expected = render_project_view(view, max_chars=max(0, capacity.room(SYSTEM, HEAD, "", SUFFIX)))
        assert views[0] == fence_untrusted_reference(expected).split(UNTRUSTED_REFERENCE_BEGIN)[1].split(UNTRUSTED_REFERENCE_END)[0]
    else:
        assert render_project_view(view, max_chars=max(0, capacity.room(SYSTEM, HEAD, "", SUFFIX))) == ""   # nothing fits = no view, never a truncated one


def test_the_content_level_follows_the_candidate_not_the_first_model():
    view = _view()
    listed = lambda prompt: sum(f"- pkg/module_{number:02d}.py" in prompt for number in range(FILES))        # noqa: E731 - local counter
    bodies = lambda prompt: sum(f"UNIQUE_BODY_{number:02d}" in prompt for number in range(FILES))            # noqa: E731
    _, roomy = _fit(ROOMY, view)
    _, medium = _fit(MEDIUM, view)
    _, small = _fit(SMALL, view)
    _, tiny = _fit(TINY, view)
    assert listed(roomy) == FILES and bodies(roomy) > 0                       # a roomy candidate sees every file with its content
    assert listed(small) == FILES and bodies(small) == 0                      # a smaller one: names only (content degrades first) ...
    assert "this request's context window is small" in small
    assert bodies(medium) < bodies(roomy) or listed(medium) == FILES          # monotone: more room never means less
    assert 0 <= listed(tiny) < FILES                                          # ... and the tiniest: fewer names, still a valid request
    assert bodies(tiny) == 0


def test_no_rendering_is_shared_between_candidates_whatever_the_order():
    """Run the candidates in every interleaving: each result equals a fresh, independent fit for its own budget. A rendering cached for the first (larger or smaller) candidate would make
    one of the repeats differ."""
    view = _view()
    fresh = {tokens: _fit(tokens, view)[1] for tokens in (TINY, SMALL, MEDIUM, ROOMY)}
    for order in ((ROOMY, SMALL), (SMALL, ROOMY), (ROOMY, TINY, MEDIUM, SMALL, ROOMY), (TINY, ROOMY, TINY, SMALL)):
        for tokens in order:
            assert _fit(tokens, view)[1] == fresh[tokens]
    assert len({fresh[SMALL], fresh[ROOMY]}) == 2                             # the budgets really do produce different renderings (the test would otherwise prove nothing)
    assert budget.fit_planner_request is fit_planner_request


def test_no_view_means_a_request_identical_to_the_unviewed_one():
    _, with_none = _fit(MEDIUM, None)  # type: ignore[arg-type]
    assert VIEW_HEADER not in with_none and UNTRUSTED_REFERENCE_BEGIN in with_none and with_none.count("[Source: ") == 1
