"""MA8: Bounded Architecture-Choice Invalidation (kriya/workflow/
architectural_choice.py) - spec §35-38/§55. See that module's own
docstring for why it deliberately does not invent new duplication
detection and instead adds a bounded trigger on top of the existing,
already-tested find_brownfield_test_redirections detector."""

from kriya.workflow.architectural_choice import (
    CandidateArchitecturalChange,
    architecture_choice_invalidated_message,
    candidate_architectural_change_id,
    classify_ownership_violations,
    should_invalidate_architectural_choice,
)

_VIOLATION = {
    "existing_owner": "src/main/java/com/example/JsonService.java",
    "new_candidate": "src/main/java/com/example/JsonUtil.java",
    "redirected_test": "src/test/java/com/example/JsonServiceTest.java",
}
_GOAL = "Replace Gson with Jackson for JSON serialization."


def test_should_invalidate_architectural_choice_threshold():
    assert should_invalidate_architectural_choice([]) is False
    one = [CandidateArchitecturalChange(
        change_id="k:a", kind="k", files=("a",), introduced_attempt=1,
    )]
    assert should_invalidate_architectural_choice(one) is False
    two = one + [CandidateArchitecturalChange(
        change_id="k:a", kind="k", files=("a",), introduced_attempt=2,
    )]
    assert should_invalidate_architectural_choice(two) is True


def test_first_occurrence_is_recorded_but_does_not_invalidate():
    new_changes, diagnostics = classify_ownership_violations([_VIOLATION], _GOAL, 3, [])
    assert len(new_changes) == 1
    assert new_changes[0].files == (_VIOLATION["new_candidate"],)
    assert diagnostics is None


def test_second_occurrence_of_same_candidate_triggers_invalidation():
    """Doc §55's core required test: baseline owner exists, candidate
    introduces a competing new owner, goal doesn't request it, repeated
    failures trace to the candidate -> bounded invalidation occurs."""
    first_changes, _ = classify_ownership_violations([_VIOLATION], _GOAL, 3, [])
    new_changes, diagnostics = classify_ownership_violations([_VIOLATION], _GOAL, 5, first_changes)
    assert len(new_changes) == 1
    assert diagnostics is not None
    assert diagnostics["reason_code"] == "ARCHITECTURE_CHOICE_INVALIDATED"
    assert diagnostics["invalidated_candidate"] == _VIOLATION["new_candidate"]
    assert diagnostics["grounded_owner"] == _VIOLATION["existing_owner"]
    assert diagnostics["occurrences"] == 2


def test_invalidation_message_names_candidate_to_abandon_and_owner_to_use():
    first_changes, _ = classify_ownership_violations([_VIOLATION], _GOAL, 3, [])
    _, diagnostics = classify_ownership_violations([_VIOLATION], _GOAL, 5, first_changes)
    message = architecture_choice_invalidated_message(diagnostics)
    assert "Abandon" in message
    assert _VIOLATION["new_candidate"] in message
    assert _VIOLATION["existing_owner"] in message


def test_goal_explicitly_authorizing_new_artifact_is_never_invalidated():
    """Doc §55's explicit non-trigger case: a goal that explicitly asks for
    the new abstraction must not automatically reject it, even on repeat."""
    authorizing_goal = "Create a new JsonUtil helper class and also update JsonService accordingly."
    first_changes, diag1 = classify_ownership_violations([_VIOLATION], authorizing_goal, 3, [])
    assert first_changes == []
    assert diag1 is None
    second_changes, diag2 = classify_ownership_violations([_VIOLATION], authorizing_goal, 5, first_changes)
    assert second_changes == []
    assert diag2 is None


def test_different_candidate_file_does_not_inherit_another_ones_occurrence_count():
    other_violation = {
        "existing_owner": "src/main/java/com/example/JsonService.java",
        "new_candidate": "src/main/java/com/example/AnotherHelper.java",
        "redirected_test": "src/test/java/com/example/JsonServiceTest.java",
    }
    first_changes, _ = classify_ownership_violations([_VIOLATION], _GOAL, 3, [])
    _, diagnostics = classify_ownership_violations([other_violation], _GOAL, 5, first_changes)
    assert diagnostics is None


def test_change_id_is_stable_for_the_same_kind_and_files():
    assert (
        candidate_architectural_change_id("brownfield_ownership_redirect", ("a.py",))
        == candidate_architectural_change_id("brownfield_ownership_redirect", ("a.py",))
    )
    assert (
        candidate_architectural_change_id("brownfield_ownership_redirect", ("a.py",))
        != candidate_architectural_change_id("brownfield_ownership_redirect", ("b.py",))
    )
