"""The harness stand-ins of ``tests/conftest.py`` for test files written before S6/S7 (``PRE_S6_S7_SCRIPTED_RUN_FILES``) stay narrow:
no suite whose subject is the requirement truth or the approval is listed, every listed file exists, and the approval stand-in answers the
one question the commit service really asks."""
import inspect
import os

import conftest

from kriya.workflow import commit_service

TESTS = os.path.dirname(os.path.abspath(__file__))


def test_every_listed_file_exists_and_no_s6_or_s7_suite_is_listed():
    for name in sorted(conftest.PRE_S6_S7_SCRIPTED_RUN_FILES):
        assert os.path.isfile(os.path.join(TESTS, name)), name
        assert not name.startswith(("test_s6", "test_s7")), f"{name}: an S6/S7 suite asserts the open or unapproved run itself"
    assert "test_backend_final_closure_005_partial_no_change.py" not in conftest.PRE_S6_S7_SCRIPTED_RUN_FILES, (
        "its closer tests flip under a closed outcome (measured 2026-11-17): it opts in per test")


def test_the_approval_stand_in_names_the_question_the_commit_service_asks():
    assert conftest.COMPLETE_CHANGE_QUESTION in inspect.getsource(commit_service)


def test_a_file_that_is_not_listed_gets_no_stand_in(request):
    assert os.path.basename(__file__) not in conftest.PRE_S6_S7_SCRIPTED_RUN_FILES
    assert "requirements_closed" not in request.fixturenames and "complete_change_approved" not in request.fixturenames
