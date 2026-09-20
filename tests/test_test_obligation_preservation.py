"""Test-obligation preservation (2026-09-20, run 7ec06f51 forensic follow-up).

Live incident: the Planner's plan named a NEW regression-test artifact
(tests/test_csharp_unqualified_generic_calls.py). kriya/workflow/
file_resolution.py::prefer_existing_artifact_owners() redirected it onto an
already-existing file (tests/test_csharp_call_site_generic_args.py) via
ordinary token-overlap resolution - correct behavior for a Developer-
INVENTED duplicate path, but here it silently relocated a real acceptance
obligation. The Developer then reported NO CHANGE NEEDED for the redirected
owner, and nothing checked whether the goal's own test-coverage intent was
actually satisfied anywhere. Kriya accepted this without ever adding
equivalent coverage.

Two pure, generic (no G1/C#/language-specific) functions close this:
  - identify_redirected_test_obligations(): detects the redirect.
  - find_unpreserved_test_obligation(): refuses to let a bare NO CHANGE
    NEEDED response for the redirected owner discharge it.

No live model/Ollama calls anywhere in this file.
"""
import os

import pytest

from kriya.workflow.file_resolution import (
    find_unpreserved_test_obligation,
    identify_redirected_test_obligations,
)


# ---------------------------------------------------------------------------
# identify_redirected_test_obligations
# ---------------------------------------------------------------------------

def test_positive_detects_redirected_new_test_artifact(tmp_path):
    """The exact live shape: a planned new test file that does not exist
    gets redirected onto an existing file - flagged as an obligation."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_existing_owner.py").write_text("def test_a(): pass\n")

    planned = ["graphify/extractors/engine.py", "tests/test_new_unqualified.py"]
    resolved = ["graphify/extractors/engine.py", "tests/test_existing_owner.py"]
    obligations = identify_redirected_test_obligations(planned, resolved, str(tmp_path))
    assert obligations == {"tests/test_existing_owner.py": "tests/test_new_unqualified.py"}


def test_negative_no_redirect_is_not_an_obligation(tmp_path):
    planned = ["graphify/extractors/engine.py", "tests/test_new_unqualified.py"]
    resolved = ["graphify/extractors/engine.py", "tests/test_new_unqualified.py"]
    assert identify_redirected_test_obligations(planned, resolved, str(tmp_path)) == {}


def test_negative_non_test_file_redirect_is_not_an_obligation(tmp_path):
    """Only TEST-file redirects create an obligation - an ordinary source
    file redirected to a different existing owner (the pre-existing
    Developer-invented-duplicate-path use case) is unaffected."""
    planned = ["service/CustomerService.java"]
    resolved = ["CustomerService.java"]
    assert identify_redirected_test_obligations(planned, resolved, str(tmp_path)) == {}


def test_negative_planned_path_already_exists_is_not_an_obligation(tmp_path):
    """If the ORIGINAL planned path already existed, this isn't a "new
    artifact silently relocated" case at all - prefer_existing_artifact_
    owners() itself would not even redirect it (see its own early return),
    but this function is defensive independent of that caller's behavior."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new_unqualified.py").write_text("def test_a(): pass\n")
    planned = ["tests/test_new_unqualified.py"]
    resolved = ["tests/test_other_owner.py"]
    assert identify_redirected_test_obligations(planned, resolved, str(tmp_path)) == {}


def test_adversarial_mismatched_lengths_raises():
    with pytest.raises(ValueError):
        identify_redirected_test_obligations(["a.py", "b.py"], ["a.py"], "/tmp")


def test_adversarial_multiple_redirects_all_tracked(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "owner_one.py").write_text("")
    (tmp_path / "tests" / "owner_two.py").write_text("")
    planned = ["tests/test_new_a.py", "tests/test_new_b.py"]
    resolved = ["tests/owner_one.py", "tests/owner_two.py"]
    obligations = identify_redirected_test_obligations(planned, resolved, str(tmp_path))
    assert obligations == {
        "tests/owner_one.py": "tests/test_new_a.py",
        "tests/owner_two.py": "tests/test_new_b.py",
    }


def test_generic_across_non_python_test_naming(tmp_path):
    """No G1/C#/Python hardcoding - a Java test file redirect is detected
    identically via is_runnable_test_file()'s own existing recognition."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "ExistingServiceTest.java").write_text("")
    planned = ["src/NewFeatureTest.java"]
    resolved = ["src/ExistingServiceTest.java"]
    obligations = identify_redirected_test_obligations(planned, resolved, str(tmp_path))
    assert obligations == {"src/ExistingServiceTest.java": "src/NewFeatureTest.java"}


# ---------------------------------------------------------------------------
# find_unpreserved_test_obligation
# ---------------------------------------------------------------------------

def test_positive_bare_no_change_needed_is_rejected():
    """The exact live failure shape: content=None, no edits, for a
    redirected owner never written this run."""
    files = [
        {"filepath": "graphify/extractors/engine.py", "content": "...", "edits": []},
        {"filepath": "tests/test_csharp_call_site_generic_args.py", "content": None,
         "analysis": "Existing test file already covers references[generic_arg] edges."},
    ]
    obligations = {"tests/test_csharp_call_site_generic_args.py": "tests/test_csharp_unqualified_generic_calls.py"}
    failure = find_unpreserved_test_obligation(files, obligations, all_files_written=set(), attempt_number=1)
    assert failure is not None
    assert failure.type == "test_obligation_not_preserved"
    assert "test_csharp_unqualified_generic_calls.py" in failure.message
    assert failure.likely_files == ["tests/test_csharp_call_site_generic_args.py"]


def test_negative_real_edit_discharges_the_obligation():
    files = [
        {"filepath": "tests/test_csharp_call_site_generic_args.py", "content": None,
         "edits": [{"search": "x", "replace": "y"}]},
    ]
    obligations = {"tests/test_csharp_call_site_generic_args.py": "tests/test_csharp_unqualified_generic_calls.py"}
    assert find_unpreserved_test_obligation(files, obligations, set(), 1) is None


def test_negative_real_full_content_discharges_the_obligation():
    files = [{"filepath": "tests/owner.py", "content": "def test_new_case(): pass\n"}]
    obligations = {"tests/owner.py": "tests/test_new_unqualified.py"}
    assert find_unpreserved_test_obligation(files, obligations, set(), 1) is None


def test_negative_already_written_in_an_earlier_attempt_is_not_re_rejected():
    """A file genuinely fixed on an EARLIER attempt must not be re-rejected
    forever just because a LATER retry's own full-set regeneration
    correctly reports no further change is needed."""
    files = [{"filepath": "tests/owner.py", "content": None}]
    obligations = {"tests/owner.py": "tests/test_new_unqualified.py"}
    assert find_unpreserved_test_obligation(files, obligations, {"tests/owner.py"}, 2) is None


def test_negative_file_not_in_obligations_is_unaffected():
    files = [{"filepath": "some/other/file.py", "content": None}]
    obligations = {"tests/owner.py": "tests/test_new_unqualified.py"}
    assert find_unpreserved_test_obligation(files, obligations, set(), 1) is None


def test_negative_no_obligations_at_all_is_a_pure_noop():
    files = [{"filepath": "tests/owner.py", "content": None}]
    assert find_unpreserved_test_obligation(files, {}, set(), 1) is None


def test_adversarial_missing_filepath_key_does_not_crash():
    files = [{"content": None}]
    obligations = {"tests/owner.py": "tests/test_new_unqualified.py"}
    assert find_unpreserved_test_obligation(files, obligations, set(), 1) is None


def test_adversarial_empty_string_content_still_counts_as_no_change():
    """An empty string is not None, so a literal '' content response is a
    (degenerate) real content response, not this check's own concern - it
    would be caught by whatever downstream gate handles empty-file writes,
    not misreported as an unpreserved test obligation."""
    files = [{"filepath": "tests/owner.py", "content": ""}]
    obligations = {"tests/owner.py": "tests/test_new_unqualified.py"}
    assert find_unpreserved_test_obligation(files, obligations, set(), 1) is None


def test_adversarial_multiple_files_returns_first_violation_deterministically():
    files = [
        {"filepath": "tests/owner_a.py", "content": None},
        {"filepath": "tests/owner_b.py", "content": None},
    ]
    obligations = {
        "tests/owner_a.py": "tests/test_new_a.py",
        "tests/owner_b.py": "tests/test_new_b.py",
    }
    failure = find_unpreserved_test_obligation(files, obligations, set(), 1)
    assert failure is not None
    assert failure.likely_files == ["tests/owner_a.py"]
