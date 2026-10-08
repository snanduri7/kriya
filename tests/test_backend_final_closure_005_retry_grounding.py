"""RETRY-CONTEXT-GOAL-MEMBER-LOSS-001 (BACKEND-FINAL-CLOSURE-005, measured on P4-T2-r3 jsoup): attempt 1 showed the
goal's own named member of an unfit target (Element.absUrl, the capacity fallback of KNOWN-TARGET-GOAL-NAMED-MEMBER-
HINT-001); every retry's member set came only from the failure's loci and the model's rejected SEARCH text, so
absUrl vanished from attempts 2-8 - the model's own reasoning: "the full file content isn't provided ... only specific
members like cssSelector, wholeTextOf, shallowClone are shown, and the absUrl method is not among them" - and it could
only fabricate anchors (ANCHOR_NOT_IN_FILE x5). Invariant: a goal-named member a planned target defines stays in every
retry's member set, whether the target is member-scoped by a failure locus or omitted whole for capacity.

Repository-independent: a synthetic large Java owner, the real retry-context preparation, no model.
"""
from test_dev_inv_001_investigation import _minimal_attempt_ctx

from kriya.workflow.attempt import _prepare_retry_context
from kriya.workflow.failure import Failure, FileLocation
from kriya.workflow.state import GenerationState

FILLER = "".join(f"    public int helper{i}(int a) {{\n        return a + {i};\n    }}\n\n" for i in range(400))
ELEMENT = ("package org.jsoup.nodes;\n\npublic class Element extends Node {\n"
           "    public String cssSelector() {\n        return tagName();\n    }\n\n"
           "    public String attr(String attributeKey) {\n        return super.attr(attributeKey);\n    }\n\n"
           + FILLER +
           "    public String absUrl(String attributeKey) {\n        return StringUtil.resolve(baseUri(), attr(attributeKey));\n    }\n}\n")
GOAL = ("Element.absUrl(...) / attr(\"abs:href\") resolves relative links incorrectly in two situations.\n\n"
        "Fix the resolution in Element.java; existing tests must keep passing.\n")
CSS_SELECTOR_LINE = ELEMENT.splitlines().index("    public String cssSelector() {") + 1


def _ctx(tmp_path, goal=GOAL):
    (tmp_path / "Element.java").write_text(ELEMENT)
    return _minimal_attempt_ctx(tmp_path, goal=goal, retrieval_member_hints={}, architect_files=["Element.java"],
                                expected_files_upfront=["Element.java"],
                                architect_basename_to_path={"Element.java": "Element.java"})


def _state(failure):
    state = GenerationState()
    state.attempt_number = 2
    state.last_attempt_mode = "targeted"
    state.last_failure = failure
    state.budgets.last_failure_signature = (failure.type, failure.message[:40])
    return state


def _names(members):
    return sorted(m.split(".")[-1].split("(")[0] for m in members)


def test_a_failure_locus_on_another_member_does_not_evict_the_goal_named_member(tmp_path):
    """The measured shape: the retry is member-scoped by a failure locus (cssSelector); absUrl stays."""
    ctx = _ctx(tmp_path)
    failure = Failure(type="anchored_edit", message="ANCHOR_NOT_IN_FILE: the search block does not occur in the current file",
                      raw_output="ANCHOR_NOT_IN_FILE", likely_files=["Element.java"],
                      file_locations=[FileLocation(filepath="Element.java", line=CSS_SELECTOR_LINE)], attempt=1)
    events = []
    state = _state(failure)
    state.record_event = events.append  # type: ignore[method-assign]
    prep = _prepare_retry_context(state, ctx, target_files=["Element.java"], prompt_window=8000, model_identity="m")
    names = _names(prep.member_hints["Element.java"])
    assert "cssSelector" in names and "absUrl" in names and "attr" in names, names
    assert "StringUtil.resolve(baseUri()" in prep.member_hint_rendered  # the causal member is shown exactly
    [grounding] = [e for e in events if e.kind == "context.retry_goal_member_grounding"]
    assert "absUrl" in "".join(grounding.details["member_hints"]["Element.java"])


def test_a_target_the_retry_package_omits_whole_is_grounded_by_its_goal_named_members(tmp_path):
    """No locus, no SEARCH text: the unfit target would be omitted entirely; the goal's members ground it instead."""
    ctx = _ctx(tmp_path)
    failure = Failure(type="operation_contract", message="OPERATION CONTRACT FAILURE: malformed repair response",
                      raw_output="INVALID_EDIT_PROTOCOL", likely_files=["Element.java"], attempt=1)
    events = []
    state = _state(failure)
    state.record_event = events.append  # type: ignore[method-assign]
    prep = _prepare_retry_context(state, ctx, target_files=["Element.java"], prompt_window=8000, model_identity="m")
    assert _names(prep.member_hints.get("Element.java", [])) == ["absUrl", "attr"]
    assert "StringUtil.resolve(baseUri()" in prep.member_hint_rendered
    assert prep.retry_package is None or "Element.java" not in prep.retry_package.omitted_files
    assert any(e.kind == "context.retry_goal_member_grounding" and e.details["unfit_targets"] == ["Element.java"] for e in events)


def test_a_goal_naming_no_member_of_the_target_adds_nothing(tmp_path):
    """Negative control: nothing is invented for a goal that names no member the file defines."""
    ctx = _ctx(tmp_path, goal="Links resolve incorrectly; see Jsoup.parse(...). Fix Element.java.\n")
    failure = Failure(type="anchored_edit", message="ANCHOR_NOT_IN_FILE", raw_output="x", likely_files=["Element.java"],
                      file_locations=[FileLocation(filepath="Element.java", line=CSS_SELECTOR_LINE)], attempt=1)
    events = []
    state = _state(failure)
    state.record_event = events.append  # type: ignore[method-assign]
    prep = _prepare_retry_context(state, ctx, target_files=["Element.java"], prompt_window=8000, model_identity="m")
    assert _names(prep.member_hints["Element.java"]) == ["cssSelector"]
    assert not any(e.kind == "context.retry_goal_member_grounding" for e in events)


def test_a_small_target_shown_whole_gets_no_member_scoping(tmp_path):
    """A target that fits whole keeps its full source: no member hints, no grounding event."""
    (tmp_path / "Node.java").write_text("package org.jsoup.nodes;\n\npublic class Node {\n    public String attr(String k) {\n        return \"\";\n    }\n}\n")
    ctx = _minimal_attempt_ctx(tmp_path, goal="Node.attr(...) must trim its key.\n", retrieval_member_hints={},
                               architect_files=["Node.java"], expected_files_upfront=["Node.java"],
                               architect_basename_to_path={"Node.java": "Node.java"})
    failure = Failure(type="compile", message="cannot find symbol", raw_output="cannot find symbol",
                      likely_files=["Node.java"], attempt=1)
    events = []
    state = _state(failure)
    state.record_event = events.append  # type: ignore[method-assign]
    prep = _prepare_retry_context(state, ctx, target_files=["Node.java"], prompt_window=16384, model_identity="m")
    assert prep.member_hints == {} and prep.member_hint_rendered == ""
    assert not any(e.kind == "context.retry_goal_member_grounding" for e in events)
