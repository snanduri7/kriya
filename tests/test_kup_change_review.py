"""change_approval.review (PRODUCT-UX-COMPLETION): the person is shown the EXACT change they are asked to approve, proved against the review's digests.

Pins: the diff text is stored by the launch process BEFORE the review is pending (approval-scoped, not the evidence store); `change_approval.review` re-proves it
(identity, per-file digests, candidate digest) before serving; any mismatch is a typed refusal and nothing is served; a change that cannot be stored is not offered for
approval at all; general `content_visibility` is untouched; the review is read-only and the decision still refuses a candidate that is no longer the pending one."""
from __future__ import annotations

import asyncio
import json
import os

import pytest

import kriya.control.launch_approval as approval
from kriya.config import AppConfig
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.launch_approval import candidate_digest
from kriya.control.persistence import launch_review_diff_path, load_launch_record, save_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.control.workspace_identity import canonical_workspace, workspace_identity
from kriya.kup import policy
from kriya.kup.cli_ops import run_kup_operation
from kriya.kup.review_ops import MAX_LINES_TOTAL, parse_unified_diff

LID = "20261010T101010-abcdef01"
DIFF_A = ("--- a/src/Service.java\n+++ b/src/Service.java\n@@ -10,4 +10,5 @@\n public void delete(Long id) {\n     Driver d = find(id);\n     d.setDeleted(true);\n"
          "+    repository.save(d);\n }\n")
DIFF_NEW = "--- a/src/Hello.java\n+++ b/src/Hello.java\n@@ -0,0 +1,3 @@\n+class Hello {\n+    // new\n+}\n"
FILES = [{"filepath": "src/Service.java", "content": DIFF_A}, {"filepath": "src/Hello.java", "content": DIFF_NEW}]
REASON = "Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe soft delete now persists."


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)


@pytest.fixture
def ws(tmp_path):
    folder = tmp_path / "proj"
    folder.mkdir()
    return folder


def _session(ws):
    canonical = canonical_workspace(str(ws))
    session = LaunchSession(canonical, workspace_identity(canonical), LID, "goal", {"ready": True}, {})
    session.open()
    return canonical, session


async def _wait_pending(canonical, after_review=None):
    for _ in range(500):
        record = load_launch_record(canonical, LID)
        pending = record.get("pending_approval") if record else None
        if pending and pending.get("review_id") != after_review:
            return pending
        await asyncio.sleep(0.01)
    raise AssertionError("no pending review was recorded")


def _review(ws, review_id, launch_id=LID):
    return run_kup_operation(AppConfig(), {"operation": "change_approval.review", "workspace": str(ws), "launch_id": launch_id, "review_id": review_id})


def _hold(ws, scenario, files=FILES, reason=REASON):
    """A real launch session inside a real run lock; `scenario(canonical, pending)` plays the desktop host while the 'workflow' awaits the decision."""
    canonical, session = _session(ws)
    out = {}

    async def main_():
        awaiting = launch_module.request_change_approval(files, reason)
        out["offered"] = awaiting is not None
        if awaiting is None:
            return
        task = asyncio.ensure_future(awaiting)
        pending = await _wait_pending(canonical)
        out["scenario"] = await asyncio.to_thread(scenario, canonical, pending)
        out["approved"] = await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    return canonical, out


def _reject(canonical, pending):
    return approval.decide(canonical, LID, pending["review_id"], pending["candidate_digest"], "reject")


def test_the_pending_review_serves_the_exact_change_proved_against_its_digests(ws):
    seen = {}

    def scenario(canonical, pending):
        record_before = json.dumps(load_launch_record(canonical, LID), sort_keys=True)
        seen["env"] = _review(ws, pending["review_id"])
        seen["unchanged"] = record_before == json.dumps(load_launch_record(canonical, LID), sort_keys=True)
        seen["pending"] = pending
        return _reject(canonical, pending)

    _hold(ws, scenario)
    env, pending = seen["env"], seen["pending"]
    assert env["error"] is None, env["error"]
    data = env["data"]
    assert data["verified"] is True and data["status"] == "PENDING" and data["truncated"] is False
    assert data["candidate_digest"] == pending["candidate_digest"] == candidate_digest(FILES)
    assert data["reason"] == REASON
    assert data["policy_note"] == "Human-in-the-loop review policy" and data["review_summary"] == "The soft delete now persists."
    first, second = data["files"]
    assert (first["path"], first["change_type"], first["additions"], first["deletions"]) == ("src/Service.java", "modified", 1, 0)
    assert (second["path"], second["change_type"], second["additions"], second["deletions"]) == ("src/Hello.java", "added", 3, 0)
    # the served per-file digests are the digests the review recorded (not recomputed over something else)
    assert [f["diff_sha256"] for f in data["files"]] == [f["diff_sha256"] for f in pending["files"]]
    # the hunks reproduce the gate's diff exactly: every line, in order, with the right kind and numbers
    added = [line for h in first["hunks"] for line in h["lines"] if line["kind"] == "add"]
    assert [line["text"] for line in added] == ["    repository.save(d);"] and added[0]["new_line"] == 13 and added[0]["old_line"] is None
    rebuilt = "".join({"add": "+", "remove": "-", "context": " "}[line["kind"]] + line["text"] + "\n" for h in first["hunks"] for line in h["lines"])
    assert rebuilt == DIFF_A.split("@@\n", 1)[1]
    assert seen["unchanged"] is True, "reading a review must change nothing"


def test_a_decided_review_is_still_inspectable_afterwards(ws):
    seen = {}

    def scenario(canonical, pending):
        seen["review_id"] = pending["review_id"]
        return _reject(canonical, pending)

    _hold(ws, scenario)  # returns once the waiting run has recorded the decision (the record flips after the waiter acts, not inside decide())
    after = _review(ws, seen["review_id"])
    assert after["error"] is None and after["data"]["status"] == "REJECTED" and after["data"]["verified"] is True


def test_general_content_visibility_is_untouched_the_evidence_content_operation_stays_fail_closed(ws):
    seen = {}

    def scenario(canonical, pending):
        seen["review"] = _review(ws, pending["review_id"])
        seen["evidence"] = run_kup_operation(AppConfig(), {"operation": "evidence.change_content", "run_id": LID, "seq": 1, "part": "diff"})
        return _reject(canonical, pending)

    _hold(ws, scenario)
    assert AppConfig().evidence.attempt_recorder.content_visibility == "off"
    assert seen["review"]["error"] is None  # served although the policy is off: it serves only the approval-scoped text
    assert seen["evidence"]["error"]["code"] == policy.EVIDENCE_CONTENT_POLICY  # and the general content path still refuses


@pytest.mark.parametrize("tamper, reason", [
    (lambda d: d["files"][0].__setitem__("diff", d["files"][0]["diff"].replace("repository.save(d)", "evil()")), "file_digest_mismatch"),
    (lambda d: d.__setitem__("candidate_digest", "sha256:" + "0" * 64), "identity_mismatch"),
    (lambda d: d.__setitem__("review_id", "ra-" + "1" * 24), "identity_mismatch"),
    (lambda d: d.__setitem__("workspace_identity", "other"), "identity_mismatch"),
    (lambda d: d["files"].reverse(), "file_set_mismatch"),
    (lambda d: d["files"].pop(), "file_set_mismatch"),
    (lambda d: d["files"].append({"filepath": "extra.py", "diff": "x"}), "file_set_mismatch"),
    (lambda d: d["files"][0].__setitem__("filepath", "src/Other.java"), "file_set_mismatch"),
])
def test_a_stored_diff_that_is_not_the_reviewed_one_is_never_served(ws, tamper, reason):
    seen = {}

    def scenario(canonical, pending):
        path = launch_review_diff_path(canonical, LID, pending["review_id"])
        document = json.load(open(path, encoding="utf-8"))
        tamper(document)
        json.dump(document, open(path, "w", encoding="utf-8"))  # a document the control writer did not produce keeps the stamp (it is in the file) but breaks a binding
        seen["env"] = _review(ws, pending["review_id"])
        return _reject(canonical, pending)

    _hold(ws, scenario)
    error = seen["env"]["error"]
    assert error is not None and seen["env"]["data"] is None
    assert error["code"] == policy.REVIEW_DIFF_UNVERIFIED and error["reason"] == reason


def test_a_missing_or_unstamped_diff_document_is_unavailable_never_partial(ws):
    seen = {}

    def scenario(canonical, pending):
        path = launch_review_diff_path(canonical, LID, pending["review_id"])
        document = json.load(open(path, encoding="utf-8"))
        document.pop("_workspace")
        json.dump(document, open(path, "w", encoding="utf-8"))
        seen["unstamped"] = _review(ws, pending["review_id"])
        os.unlink(path)
        seen["missing"] = _review(ws, pending["review_id"])
        return _reject(canonical, pending)

    _hold(ws, scenario)
    for key in ("unstamped", "missing"):
        assert seen[key]["error"]["code"] == policy.REVIEW_DIFF_UNAVAILABLE and seen[key]["data"] is None, key


def test_unknown_review_other_workspace_and_malformed_requests_are_refused(ws, tmp_path):
    seen = {}
    other = tmp_path / "other"
    other.mkdir()

    def scenario(canonical, pending):
        seen["unknown"] = _review(ws, "ra-" + "9" * 24)
        seen["other_workspace"] = _review(other, pending["review_id"])
        seen["bad_review"] = _review(ws, "../../etc/passwd")
        seen["bad_launch"] = _review(ws, pending["review_id"], launch_id="not-a-launch")
        return _reject(canonical, pending)

    _hold(ws, scenario)
    assert seen["unknown"]["error"]["code"] == policy.REVIEW_NOT_FOUND
    assert seen["other_workspace"]["error"]["code"] == policy.LAUNCH_NOT_FOUND  # the other folder has no such launch at all
    assert seen["bad_review"]["error"]["code"] == policy.INVALID_REQUEST and seen["bad_launch"]["error"]["code"] == policy.INVALID_REQUEST


def test_a_change_that_cannot_be_stored_for_inspection_is_not_offered_for_approval(ws, monkeypatch):
    def failing(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("kriya.control.persistence.save_launch_review_diff", failing)
    canonical, out = _hold(ws, lambda c, p: None)
    assert out["offered"] is False, "no inspectable change means no review is offered (the caller declines)"
    record = load_launch_record(canonical, LID)
    assert record["pending_approval"] is None and record["approval_history"] == []


def test_a_change_too_large_to_hold_is_not_offered_for_approval(ws, monkeypatch):
    monkeypatch.setattr(launch_module, "REVIEW_DIFF_MAX_BYTES", 100)
    canonical, out = _hold(ws, lambda c, p: None)
    assert out["offered"] is False
    assert load_launch_record(canonical, LID)["pending_approval"] is None


def test_a_new_candidate_supersedes_the_review_and_the_old_one_can_no_longer_be_approved(ws):
    seen = {}
    changed = [{"filepath": "src/Service.java", "content": DIFF_A.replace("repository.save(d)", "repository.saveAndFlush(d)")}]
    canonical, session = _session(ws)

    async def main_():
        first = asyncio.ensure_future(launch_module.request_change_approval(FILES, REASON))
        pending_a = await _wait_pending(canonical)
        seen["a"] = (await asyncio.to_thread(_review, ws, pending_a["review_id"]))["data"]
        # the candidate changes while the person is reading it: the gate asks again with the new diff
        second = asyncio.ensure_future(launch_module.request_change_approval(changed, REASON))
        pending_b = await _wait_pending(canonical, after_review=pending_a["review_id"])
        seen["b"] = (await asyncio.to_thread(_review, ws, pending_b["review_id"]))["data"]
        # the person decides on what they SAW (A): refused, the digest is no longer the pending one
        with pytest.raises(approval.ApprovalRefused) as stale:
            await asyncio.to_thread(approval.decide, canonical, LID, pending_a["review_id"], pending_a["candidate_digest"], "approve")
        seen["stale"] = stale.value.code
        seen["a_after"] = (await asyncio.to_thread(_review, ws, pending_a["review_id"]))["data"]
        await asyncio.to_thread(approval.decide, canonical, LID, pending_b["review_id"], pending_b["candidate_digest"], "reject")
        await asyncio.wait_for(second, 20)
        first.cancel()  # the superseded waiter is abandoned by the gate (as in the launch-approval tests)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(0, None, None)
    assert seen["stale"] == "STALE_REVIEW"
    assert seen["a"]["candidate_digest"] != seen["b"]["candidate_digest"]
    assert seen["b"]["files"][0]["hunks"][0]["lines"][3]["text"] == "    repository.saveAndFlush(d);"
    assert seen["a_after"]["status"] == "EXPIRED" and seen["a_after"]["verified"] is True  # the superseded review stays inspectable, marked expired


def test_a_record_whose_digest_was_rewritten_together_with_the_stored_document_is_still_refused(ws):
    """Both the launch record and the sidecar claim the same (forged) candidate digest; only recomputing it from the per-file digests exposes the forgery."""
    seen = {}

    def scenario(canonical, pending):
        forged = "sha256:" + "f" * 64
        record = load_launch_record(canonical, LID)
        record["pending_approval"]["candidate_digest"] = forged
        save_launch_record(canonical, LID, record)
        path = launch_review_diff_path(canonical, LID, pending["review_id"])
        document = json.load(open(path, encoding="utf-8"))
        document["candidate_digest"] = forged
        json.dump(document, open(path, "w", encoding="utf-8"))
        seen["env"] = _review(ws, pending["review_id"])
        record["pending_approval"]["candidate_digest"] = pending["candidate_digest"]
        save_launch_record(canonical, LID, record)
        return _reject(canonical, pending)

    _hold(ws, scenario)
    assert seen["env"]["error"]["code"] == policy.REVIEW_DIFF_UNVERIFIED and seen["env"]["error"]["reason"] == "candidate_digest_mismatch"


def test_a_launch_record_that_names_another_workspace_identity_serves_nothing(ws):
    seen = {}

    def scenario(canonical, pending):
        record = load_launch_record(canonical, LID)
        record["workspace"]["identity"] = "someone-elses-identity"
        save_launch_record(canonical, LID, record)
        seen["env"] = _review(ws, pending["review_id"])
        record["workspace"]["identity"] = workspace_identity(canonical)
        save_launch_record(canonical, LID, record)
        return _reject(canonical, pending)

    _hold(ws, scenario)
    assert seen["env"]["error"]["code"] == policy.REVIEW_NOT_FOUND and seen["env"]["error"]["reason"] == "wrong_workspace"


def test_a_diff_larger_than_one_response_can_carry_is_flagged_truncated_so_the_host_withholds_approval(ws, monkeypatch):
    import kriya.kup.review_ops as review_ops

    monkeypatch.setattr(review_ops, "MAX_LINES_TOTAL", 3)
    seen = {}

    def scenario(canonical, pending):
        seen["env"] = _review(ws, pending["review_id"])
        return _reject(canonical, pending)

    _hold(ws, scenario)
    data = seen["env"]["data"]
    assert data["truncated"] is True and any(f["truncated"] for f in data["files"])
    assert sum(len(h["lines"]) for f in data["files"] for h in f["hunks"]) == 3  # exactly the budget, no more
    assert [f["additions"] for f in data["files"]] == [1, 3]  # the counts still describe the WHOLE change


# ------------------------------------------------------------------ the parser

def test_parser_new_file_deleted_file_notes_and_numbers():
    new, used = parse_unified_diff(DIFF_NEW, 100)
    assert (new["change_type"], new["additions"], new["deletions"], used, new["truncated"]) == ("added", 3, 0, 3, False)
    deleted, _ = parse_unified_diff("--- a/x\n+++ b/x\n@@ -1,2 +0,0 @@\n-a\n-b\n", 100)
    assert (deleted["change_type"], deleted["additions"], deleted["deletions"]) == ("deleted", 0, 2)
    noted, _ = parse_unified_diff("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b\n", 100)
    assert [(line["kind"], line["old_line"], line["new_line"]) for line in noted["hunks"][0]["lines"]] == [("remove", 1, None), ("note", None, None), ("add", None, 1)]
    assert noted["change_type"] == "modified"


def test_parser_marks_a_file_truncated_when_the_line_budget_runs_out_but_still_counts_everything():
    body = "".join(f"+line {i}\n" for i in range(50))
    view, used = parse_unified_diff(f"--- a/x\n+++ b/x\n@@ -0,0 +1,50 @@\n{body}", 10)
    assert view["truncated"] is True and used == 10 and view["additions"] == 50 and len(view["hunks"][0]["lines"]) == 10
    assert MAX_LINES_TOTAL >= 1000


def test_split_reason_never_alters_the_text_and_has_a_none_for_no_reviewer_section():
    from kriya.control.launch_approval import REVIEW_SECTION_HEADER, split_reason

    assert split_reason(f"policy\n\n{REVIEW_SECTION_HEADER}\nreport  \n") == ("policy", "report")
    assert split_reason("only a policy note") == ("only a policy note", None)
    assert split_reason(f"policy\n\n{REVIEW_SECTION_HEADER}\nNOT ATTACHED: the review request failed (X). Review the diff without it.")[1].startswith("NOT ATTACHED")
    # the workflow composes the reason with the SAME constant
    import inspect

    import kriya.workflow.workflow as workflow
    assert "REVIEW_SECTION_HEADER" in inspect.getsource(workflow) and "=== Automated Code Review ===" not in inspect.getsource(workflow)
