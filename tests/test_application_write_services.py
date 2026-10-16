"""APPLICATION write/run-side services (full modularization, slice B3): golden envelopes through the KUP adapter.

The goldens in ``tests/golden/kup/application_write/`` were WRITTEN FROM THE PRE-B3 CODE (the KUP operation bodies at 03f2932, before any
of them delegated to ``kriya.application``) and are compared byte for byte after normalization, exactly like the B2 read side
(tests/test_application_services.py): every operation the B3 services now answer must produce exactly the envelope it produced before,
on success and on every refusal path exercised. Normalization replaces only values that differ between two runs of the SAME code
(temporary paths, the identities derived from them, wall-clock times, generated review ids and the digests that cover them).
Regenerate (only from a commit whose envelopes are known good) with ``KRIYA_WRITE_KUP_GOLDEN=1``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest
import yaml

import kriya.control.launch_approval as approval
from kriya.config import AppConfig
from kriya.config import authority_approval as aa
from kriya.context.workspace_identity import canonical_workspace, workspace_identity
from kriya.control import launch as launch_module
from kriya.control.launch import LaunchSession
from kriya.control.persistence import load_launch_record
from kriya.control.run_coordinator import begin_mutating_run
from kriya.kup.cli_ops import run_kup_operation
from kriya.kup.inspect import encode_response
from kriya.workflow.runtime_command_approval import build_subject

GOLDEN = Path(__file__).resolve().parent / "golden" / "kup" / "application_write"
WRITE = os.environ.get("KRIYA_WRITE_KUP_GOLDEN") == "1"
LID = "20261010T101010-abcdef01"
LID_RUNTIME = "20261010T101011-abcdef02"
# Keys whose values differ between two runs of identical code (MEASURED by generating twice).
VOLATILE_KEYS = frozenset({"requested_at", "expires_at", "decided_at", "at", "started_at", "ended_at", "observed_at", "pid", "nonce",
                           "opened_at", "updated_at", "created_at", "sealed_at"})
PROTECTED = {"llm": {"base_url": "http://operator:hunter2-pass@127.0.0.1:11434/v1"}}
CHANGE_FILES = [{"filepath": "src/Service.java", "content": "--- a/src/Service.java\n+++ b/src/Service.java\n@@ -1,2 +1,3 @@\n a\n+b\n c\n"}]
TOOLS = [{"tool": "git", "available": True, "path": "/usr/bin/git", "checked": "ran --version"}]


def _hold_and_reject(canonical: str, launch_id: str, request) -> str:
    """A real launch session inside a real run lock: one review is requested, then rejected by the real decision action."""
    session = LaunchSession(canonical, workspace_identity(canonical), launch_id, "goal", {"ready": True}, {})
    session.open()
    seen: Dict[str, str] = {}

    async def main_():
        task = asyncio.ensure_future(request())
        for _ in range(500):
            record = load_launch_record(canonical, launch_id)
            if record and record.get("pending_approval"):
                break
            await asyncio.sleep(0.01)
        pending = record["pending_approval"]
        seen["review_id"] = pending["review_id"]
        await asyncio.to_thread(approval.decide, canonical, launch_id, pending["review_id"], pending["candidate_digest"], "reject",
                                pending.get("kind", approval.KIND_CHANGE))
        await asyncio.wait_for(task, 20)

    try:
        with begin_mutating_run(canonical):
            asyncio.run(main_())
    finally:
        session.finish(1, {"status": "failure", "quality_gates_passed": False}, None)
    return seen["review_id"]


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    """A plain workspace with one reviewed change launch and one reviewed runtime-command launch, a folder whose configuration needs
    an operator approval, a parent folder for workspace.create and an unconfigured folder for project.initialize."""
    monkeypatch.setattr(approval, "POLL_SECONDS", 0.01)
    monkeypatch.setenv("KRIYA_AUTHORITY_HOME", str(tmp_path / "_authority_home"))
    monkeypatch.delenv("KRIYA_TRUST_FILE", raising=False)
    monkeypatch.setattr("kriya.kup.execution.tool_visibility", lambda: [dict(t) for t in TOOLS])
    ws = tmp_path / "Work Space"
    ws.mkdir()
    canonical = canonical_workspace(str(ws))
    review = _hold_and_reject(canonical, LID, lambda: launch_module.request_change_approval(CHANGE_FILES, "Human-in-the-loop review policy\n\nreview"))
    subject = build_subject(unit_id="s4", criterion="prints Hello", working_directory="/private/var/kriya/worktrees/run-1",
                            commands=[["javac", "A.java"], ["java", "A"]], input_channel="none", stdin_payload=None,
                            reason="Allow Kriya to execute these command(s)?")
    runtime_review = _hold_and_reject(canonical, LID_RUNTIME, lambda: launch_module.request_runtime_command_approval(subject))
    guarded = tmp_path / "guarded"
    guarded.mkdir()
    (guarded / "kriya.yaml").write_text(yaml.safe_dump(PROTECTED))
    refused = tmp_path / "refused"
    refused.mkdir()
    (refused / "kriya.yaml").write_text("execution_policy:\n  enabled: true\n")
    (tmp_path / "parent").mkdir()
    (tmp_path / "parent" / "taken").mkdir()
    (tmp_path / "fresh").mkdir()
    (tmp_path / "configured").mkdir()
    (tmp_path / "configured" / "kriya.yml").write_text("{}\n")
    state = {"ws": str(ws), "tmp": str(tmp_path), "wsid": workspace_identity(canonical),
            "review": review, "runtime_review": runtime_review,
            "guarded_wsid": workspace_identity(str(guarded)), "refused_wsid": workspace_identity(str(refused)), "fresh_wsid": workspace_identity(str(tmp_path / "parent" / "new-project"))}
    state["fingerprint"] = _fingerprint(state)  # bound to the folder's path: a per-run value, replaced by a token
    return state


def _fingerprint(s: Dict[str, Any]) -> str:
    return run_kup_operation(None, {"operation": "configuration.approval_status", "workspace": os.path.join(s["tmp"], "guarded")})["data"]["fingerprint"]


def _requests(s: Dict[str, Any]) -> List[Tuple[str, Dict[str, Any]]]:
    """(golden name, request) in order: every operation a B3 service answers, success and refusal paths (approve changes state)."""
    ws, tmp = s["ws"], s["tmp"]
    guarded, rev, rt = os.path.join(tmp, "guarded"), s["review"], s["runtime_review"]
    return [
        ("execution_preflight", {"operation": "execution.preflight", "workspace": ws}),
        ("execution_preflight_refused", {"operation": "execution.preflight", "workspace": os.path.join(tmp, "refused")}),
        ("execution_preflight_missing", {"operation": "execution.preflight", "workspace": ws + "-missing"}),
        ("launch_status", {"operation": "launch.status", "workspace": ws, "launch_id": LID}),
        ("launch_status_bad_id", {"operation": "launch.status", "workspace": ws, "launch_id": "nope"}),
        ("launch_status_missing", {"operation": "launch.status", "workspace": ws, "launch_id": "20201010T101010-00000000"}),
        ("change_review", {"operation": "change_approval.review", "workspace": ws, "launch_id": LID, "review_id": rev}),
        ("change_review_bad_review_id", {"operation": "change_approval.review", "workspace": ws, "launch_id": LID, "review_id": "x"}),
        ("change_review_unknown", {"operation": "change_approval.review", "workspace": ws, "launch_id": LID, "review_id": "ra-" + "0" * 24}),
        ("change_review_no_launch", {"operation": "change_approval.review", "workspace": ws, "launch_id": "20201010T101010-00000000", "review_id": rev}),
        ("runtime_review", {"operation": "runtime_command.review", "workspace": ws, "launch_id": LID_RUNTIME, "review_id": rt}),
        ("runtime_review_wrong_kind", {"operation": "runtime_command.review", "workspace": ws, "launch_id": LID, "review_id": rev}),
        ("runtime_review_wrong_folder", {"operation": "runtime_command.review", "workspace": guarded, "launch_id": LID_RUNTIME, "review_id": rt}),
        ("configuration_status_not_required", {"operation": "configuration.approval_status", "workspace": ws}),
        ("configuration_status_missing", {"operation": "configuration.approval_status", "workspace": os.path.join(tmp, "absent")}),
        ("configuration_status_required", {"operation": "configuration.approval_status", "workspace": guarded}),
        ("configuration_approve_bad_fingerprint", {"operation": "configuration.approve", "workspace": guarded, "fingerprint": "x"}),
        ("configuration_approve_stale", {"operation": "configuration.approve", "workspace": guarded, "fingerprint": "sha256:" + "0" * 64}),
        ("configuration_approve", {"operation": "configuration.approve", "workspace": guarded, "fingerprint": "<FINGERPRINT>"}),
        ("configuration_approve_again", {"operation": "configuration.approve", "workspace": guarded, "fingerprint": "<FINGERPRINT>"}),
        ("configuration_status_approved", {"operation": "configuration.approval_status", "workspace": guarded}),
        ("workspace_create", {"operation": "workspace.create", "parent": os.path.join(tmp, "parent"), "name": "new-project"}),
        ("workspace_create_taken", {"operation": "workspace.create", "parent": os.path.join(tmp, "parent"), "name": "taken"}),
        ("workspace_create_bad_name", {"operation": "workspace.create", "parent": os.path.join(tmp, "parent"), "name": ".hidden"}),
        ("workspace_create_bad_parent", {"operation": "workspace.create", "parent": "relative", "name": "x"}),
        ("workspace_create_no_parent", {"operation": "workspace.create", "parent": os.path.join(tmp, "absent"), "name": "x"}),
        ("project_initialize", {"operation": "project.initialize", "workspace": os.path.join(tmp, "fresh")}),
        ("project_initialize_already", {"operation": "project.initialize", "workspace": os.path.join(tmp, "configured")}),
        ("project_initialize_missing", {"operation": "project.initialize", "workspace": os.path.join(tmp, "absent")}),
    ]


_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


def normalize(value: Any, s: Dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {k: ("<volatile>" if k in VOLATILE_KEYS and v is not None else normalize(v, s)) for k, v in value.items()}
    if isinstance(value, list):
        return [normalize(v, s) for v in value]
    if isinstance(value, str):
        for real, token in ((os.environ["KRIYA_STATE_DIR"], "<STATE>"), (os.path.realpath(s["tmp"]), "<TMP>"), (s["tmp"], "<TMP>"),
                            (s["wsid"], "<WSID>"), (s["guarded_wsid"], "<GUARDED_WSID>"), (s["fresh_wsid"], "<FRESH_WSID>"),
                            (s["refused_wsid"], "<REFUSED_WSID>"), (s["fingerprint"], "<FINGERPRINT>"), (s.get("approval", "\0"), "<APPROVAL>"),
                            (s["review"], "<REVIEW>"), (s["runtime_review"], "<RUNTIME_REVIEW>")):
            value = value.replace(real, token)
    return value


def answer(request: Dict[str, Any], s: Dict[str, Any]) -> str:
    if request.get("fingerprint") == "<FINGERPRINT>":
        request = {**request, "fingerprint": _fingerprint(s)}
    encoded = encode_response(run_kup_operation(AppConfig(), request, request_id="golden"))
    artifact = aa.load_approval_artifact(aa.default_local_approval_path(canonical_workspace(os.path.join(s["tmp"], "guarded"))))
    if artifact is not None:  # the approval digest covers the folder's path: replaced by a token once the approval exists
        s["approval"] = artifact.set_digest[:16]
    return json.dumps(normalize(json.loads(encoded), s), indent=1, sort_keys=True, ensure_ascii=False) + "\n"


def test_every_write_and_run_side_operation_answers_the_pre_extraction_envelope(scenario):
    mismatched = []
    for name, request in _requests(scenario):
        text = answer(request, scenario)
        path = GOLDEN / f"{name}.json"
        if WRITE:
            GOLDEN.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            continue
        if path.read_text(encoding="utf-8") != text:
            mismatched.append(name)
    assert not WRITE, "goldens written; rerun without KRIYA_WRITE_KUP_GOLDEN"
    assert mismatched == [], f"envelopes differ from the pre-B3 goldens: {mismatched}"
    assert sorted(p.stem for p in GOLDEN.glob("*.json")) == sorted(name for name, _r in _requests(scenario)), "a golden without a case"


def test_the_goldens_cover_success_and_refusal_and_keep_every_decision_field(scenario):
    """The comparison cannot pass vacuously: each operation has a success golden with data and at least one refusal golden with a code,
    and the volatile set never hides an identity, digest or decision field."""
    assert not VOLATILE_KEYS & {"status", "code", "reason", "ready", "blockers", "fingerprint", "candidate_digest", "command_fingerprint",
                                "verified", "outcome", "approved_fields", "approval_digest", "identity", "liveness", "files", "commands"}
    goldens = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in GOLDEN.glob("*.json")}
    ops = {request["operation"] for _n, request in _requests(scenario)}
    for operation in ops:
        names = [n for n, r in _requests(scenario) if r["operation"] == operation]
        assert any(goldens[n]["error"] is None and goldens[n]["data"] for n in names), operation
        assert any(goldens[n]["error"] and goldens[n]["error"]["code"] for n in names), operation
    assert goldens["change_review"]["data"]["verified"] is True and goldens["runtime_review"]["data"]["verified"] is True
    assert goldens["configuration_approve"]["data"]["outcome"] == "approved"
    assert goldens["configuration_approve_again"]["data"]["outcome"] == "already_approved"
    assert goldens["configuration_approve_stale"]["error"]["code"] == "APPROVAL_STALE"
    assert goldens["execution_preflight"]["data"]["ready"] is True
