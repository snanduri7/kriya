"""OWNER-RECOVERY-STATIC-CAUSE-001 (Stage C run 2, 20261119T125126-20dc890c).

MEASURED in the sealed run: the verification-only unit failed because the program printed ``[VERIFICATION] PASS`` and no file held the
``[VERIFICATION] FAIL`` branch (the gate's own distrust rule). The reopened owner answered with a change to one comment. The enforce
loop accepted it - its bytes had changed and its own gates passed - and ran the verification again to the identical failure.

The correction, at the loop's one acceptance decision: when the failure being repaired is that statically decidable one, the same
predicate (``verification_contract.classify_contract_verdict``) is read again on the bytes the owner left. Still true = not accepted, no
second run; the existing rejection ends the episode (VERIFICATION_OWNER_EPISODE_OWNER_STOPPED) and the failure stands.

The sources and outputs are the sealed run's own bytes (tests/fixtures/stage_c_run2, digest-checked). The harness is the existing
run-6 replay of test_verification_owner_reopen_review_and_owner_search_001.
"""
import hashlib
import os

import pytest
from test_enforce_recovery_convergence_001 import K1, _assert_converged, _ran, _results
from test_verification_owner_reopen_review_and_owner_search_001 import (
    REOPEN,
    RERUN,
    RUN6,
    _episode,
    _planned,
    _reopened_owners,
    _run,
    _run6_plan,
)

from kriya.workflow.enforce_loop import (
    VERIFICATION_OWNER_EPISODE_ACCEPTED,
    VERIFICATION_OWNER_EPISODE_OWNER_STOPPED,
    distrusted_marker_cause_stands,
)

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "stage_c_run2")
SEALED = {
    "ProtocolApplication.call44.java": "ad70a1ae20cb1ef5db674b4122bcbdba97ed80cafb995e9e84088bbeebf1a877",
    "ProtocolApplication.call51.java": "9d0bcabf52ed61b3f742b42df3da019b41c79984748419f3affa3045b1d0ce98",
    "s8_marker_failure.raw_output.txt": "5d1c3ab56761e1f8c9ad58d89f1337f1e1f82ab9b28722d44753e6eb245df27a",
    "s8_npe_failure.raw_output.txt": "f2827610a0a11402bd188dba0d0facd7e4cdb7d8562cf4a5648204bd5e41f7ee",
}
UNCONDITIONAL = '        System.out.println("[VERIFICATION] PASS");\n'
GROUNDED = ('        if (!equals) {\n            System.out.println("[VERIFICATION] FAIL: decoded protocol differs from the original");\n'
            '            return;\n        }\n        System.out.println("[VERIFICATION] PASS");\n')
APP = "App.java"   # the file of stage s5 in the run-6 plan graph

pytestmark = pytest.mark.usefixtures("bound_workspace")


def _sealed(name):
    with open(os.path.join(FIXTURES, name), "rb") as handle:
        data = handle.read()
    assert hashlib.sha256(data).hexdigest() == SEALED[name], f"{name} is not the sealed run-2 content"
    return data.decode("utf-8")


def _failed(output_name):
    """The verification unit's typed one-attempt stop, carrying the sealed gate output as ``last_failure`` does."""
    return {"_skip_write": True, "quality_gates_passed": False, "status": "failed", "no_progress_reason": K1,
            "last_failure": {"type": "run_verification", "message": "runtime verification failed", "raw_output": _sealed(output_name)}}


def _script(reopen_content, *, failure="s8_marker_failure.raw_output.txt"):
    return _planned({("s5", "planned"): [{"_content": _sealed("ProtocolApplication.call44.java")}],
                     ("s7", "planned"): [_failed(failure)],
                     ("s5", REOPEN): [{"_content": reopen_content}]})


def _grounded_source():
    source = _sealed("ProtocolApplication.call44.java")
    assert source.count(UNCONDITIONAL) == 1
    return source.replace(UNCONDITIONAL, GROUNDED)


# ---------------------------------------------------------------------------------------------- the sealed facts the proof rests on

def test_the_sealed_answer_differs_from_the_accepted_file_in_one_comment_and_neither_holds_a_fail_branch():
    accepted, answer = _sealed("ProtocolApplication.call44.java"), _sealed("ProtocolApplication.call51.java")
    assert accepted != answer
    assert answer == accepted.replace("connection.start();\n", "connection.start(); // Ensure connection is started\n")
    assert "[VERIFICATION] FAIL" not in accepted + answer and UNCONDITIONAL in accepted and UNCONDITIONAL in answer
    assert "\n[VERIFICATION] PASS\n" in _sealed("s8_marker_failure.raw_output.txt")
    assert "[VERIFICATION]" not in _sealed("s8_npe_failure.raw_output.txt")


# ------------------------------------------------------------------------------------------------------------------ the predicate

def _unit_result(output, failure_type="run_verification"):
    return {"last_failure": {"type": failure_type, "raw_output": output}}


def test_the_predicate_is_true_only_for_a_pass_marker_with_no_fail_branch_in_any_given_file(tmp_path):
    marker = _sealed("s8_marker_failure.raw_output.txt")
    (tmp_path / APP).write_text(_sealed("ProtocolApplication.call51.java"), encoding="utf-8")
    (tmp_path / "Other.java").write_text("class Other { }\n", encoding="utf-8")
    paths = [APP, "Other.java", "missing/Never.java"]                                   # an unreadable path contributes nothing
    assert distrusted_marker_cause_stands(_unit_result(marker), str(tmp_path), paths) is True
    # every case it must NOT decide: the existing acceptance then applies unchanged
    assert distrusted_marker_cause_stands(_unit_result(marker, "compile"), str(tmp_path), paths) is False          # another gate
    assert distrusted_marker_cause_stands(_unit_result(_sealed("s8_npe_failure.raw_output.txt")), str(tmp_path), paths) is False
    assert distrusted_marker_cause_stands(_unit_result("[VERIFICATION] FAIL: not equal\n"), str(tmp_path), paths) is False
    assert distrusted_marker_cause_stands({}, str(tmp_path), paths) is False and \
        distrusted_marker_cause_stands({"last_failure": None}, str(tmp_path), paths) is False
    # the gate's own grounding rule, file-set wide: the FAIL branch anywhere in the files read grounds the marker
    (tmp_path / "Other.java").write_text('class Other { String f = "[VERIFICATION] FAIL: x"; }\n', encoding="utf-8")
    assert distrusted_marker_cause_stands(_unit_result(marker), str(tmp_path), paths) is False
    assert distrusted_marker_cause_stands(_unit_result(marker), str(tmp_path), [APP]) is True                      # only what it is given
    (tmp_path / APP).write_text(_grounded_source(), encoding="utf-8")
    assert distrusted_marker_cause_stands(_unit_result(marker), str(tmp_path), [APP]) is False


# ------------------------------------------------------------------------------------------------------- the acceptance decision

@pytest.mark.asyncio
async def test_c2_replay_a_comment_only_answer_does_not_clear_the_marker_failure_and_obtains_no_second_run(tmp_path):
    """The sealed sequence. Before the correction the byte change was accepted and s7 ran again (the unscripted rerun of this harness
    passes, so the run would end ``success``): every assertion below fails on that behaviour."""
    result, calls = await _run(tmp_path, _run6_plan(), _script(_sealed("ProtocolApplication.call51.java")))
    assert _reopened_owners(calls) == ["s5"]
    assert _ran(calls, "s7") == ["planned"]                                              # the unchanged verification is not run again
    assert result.legacy_result["status"] == "failed" and ("s7", "failed") in _results(result)
    episode = _episode(result)
    assert episode["reason_codes"] == [VERIFICATION_OWNER_EPISODE_OWNER_STOPPED]         # the existing typed end of a refused change
    assert episode["accepted_owner"] is None and episode["owner_recovery_passed"] is False
    assert episode["owners_invoked"] == ["s5"] and episode["unchanged_owners"] == []     # no other owner is asked after changed bytes


# The harness's two documented opt-ins for a scripted run that is asserted to END SUCCESS (tests/conftest.py, S6/S7): the scripted
# goal's general requirement is closed at the outcome owner, and a person approves the complete change through the product's channel.
# MEASURED (owner run 2026-11-19, check.v2/4_diag.txt): without them this run ends OPEN_REQUIREMENTS on REQ-1 "Create the application"
# after the acceptance, the rerun and the applied bytes all held - the product's fail-closed rule for a goal nothing in a script can
# verify, which the run-6 twin gets from PRE_S6_S7_SCRIPTED_RUN_FILES. Only this test asserts success, so only it opts in.
@pytest.mark.usefixtures("requirements_closed", "complete_change_approved")
@pytest.mark.asyncio
async def test_a_real_pass_fail_branch_is_accepted_and_goes_through_the_existing_rerun_and_gates(tmp_path):
    """The other side: the same failure, answered by gating the marker on the comparison. Nothing new stands in its way."""
    result, calls = await _run(tmp_path, _run6_plan(), _script(_grounded_source()))
    assert _reopened_owners(calls) == ["s5"] and _ran(calls, "s7") == ["planned", RERUN]
    assert _episode(result)["reason_codes"] == [VERIFICATION_OWNER_EPISODE_ACCEPTED] and _episode(result)["accepted_owner"] == "s5"
    assert (tmp_path / APP).read_text(encoding="utf-8") == _grounded_source()
    _assert_converged(tmp_path, result, (*RUN6, "s7"))


@pytest.mark.asyncio
async def test_a_runtime_dependent_failure_keeps_the_existing_acceptance_even_for_the_same_comment_only_bytes(tmp_path):
    """The sealed first s8 failure (a NullPointerException: no marker, nothing decidable from bytes). The very same comment-only
    answer is accepted and the verification runs again - only running it can judge such a repair."""
    script = _script(_sealed("ProtocolApplication.call51.java"), failure="s8_npe_failure.raw_output.txt")
    result, calls = await _run(tmp_path, _run6_plan(), script)
    assert _reopened_owners(calls) == ["s5"] and _ran(calls, "s7") == ["planned", RERUN]
    assert _episode(result)["reason_codes"] == [VERIFICATION_OWNER_EPISODE_ACCEPTED]


@pytest.mark.asyncio
async def test_a_marker_grounded_before_the_owner_wrote_is_not_this_failure_and_changes_nothing(tmp_path):
    """The predicate must hold BEFORE the owner writes: when another established file already carries the FAIL branch, the gate would
    not have distrusted the marker, so this is some other failure and the existing byte-change acceptance applies."""
    script = _script(_sealed("ProtocolApplication.call51.java"))
    script[("s2", "planned")] = [{"_content": 'public class Protocol { String f = "[VERIFICATION] FAIL: x"; }\n'}]
    result, calls = await _run(tmp_path, _run6_plan(), script)
    assert _ran(calls, "s7") == ["planned", RERUN] and _episode(result)["accepted_owner"] == "s5"
