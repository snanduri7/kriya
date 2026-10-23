"""Test double for the HOST end of a desktop launch's private review channel (D2 M2): a real socket pair whose waiter end is attached to a ``LaunchSession`` and whose host end
is driven from a test thread with blocking reads - the same job the Electron host does (read the ``review`` frame, echo its capability with the person's decision, read the
``result``). It is a peer on a real socket, not a mock of the waiter: every frame goes through the production parser on both sides."""
from __future__ import annotations

import json
import socket
from collections import deque
from typing import Any, Deque, Dict, Optional

from kriya.control.review_channel import FrameParser, ReviewChannel

OMIT = ...  # pass as an override to leave a key out of the frame


class ScriptedHost:
    def __init__(self, host_sock: socket.socket, workspace: str) -> None:
        self.workspace = workspace
        self.sock = host_sock
        self._parser = FrameParser()
        self._frames: Deque[Dict[str, Any]] = deque()
        self.review_frame: Optional[Dict[str, Any]] = None

    def next_frame(self, timeout: float = 10.0) -> Dict[str, Any]:
        self.sock.settimeout(timeout)
        while not self._frames:
            data = self.sock.recv(4096)
            if not data:
                raise EOFError("the waiter closed the review channel")
            self._frames.extend(self._parser.feed(data))
        return self._frames.popleft()

    def review(self, timeout: float = 10.0) -> Dict[str, Any]:
        """The waiter's ``review`` frame for the review being waited on (read once, then remembered until a decision is accepted)."""
        if self.review_frame is None:
            frame = self.next_frame(timeout)
            assert frame["type"] == "review", frame
            self.review_frame = frame
        return self.review_frame

    def present(self, review_id: str, timeout: float = 10.0) -> Dict[str, Any]:
        """Read frames until the review the launch record says is pending has been presented (skipping the stale ones a newer review superseded), as the real host does."""
        while self.review_frame is None or self.review_frame["review_id"] != review_id:
            frame = self.next_frame(timeout)
            assert frame["type"] == "review", frame
            self.review_frame = frame
        return self.review_frame

    def next_review(self, timeout: float = 10.0) -> Dict[str, Any]:
        """Forget the remembered review and read the next ``review`` frame (a newer review replaced it)."""
        self.review_frame = None
        return self.review(timeout)

    def decision_frame(self, decision: str = "approve", **override: Any) -> Dict[str, Any]:
        review = self.review()
        frame: Dict[str, Any] = {"v": 1, "type": "decision", "launch_id": review["launch_id"], "review_id": review["review_id"], "kind": review["kind"],
                                 "candidate_digest": review["candidate_digest"], "decision": decision, "capability": review["capability"]}
        for key, value in override.items():
            if value is OMIT:
                frame.pop(key, None)
            else:
                frame[key] = value
        return frame

    def send(self, frame_or_bytes: Any) -> None:
        data = frame_or_bytes if isinstance(frame_or_bytes, bytes) else json.dumps(frame_or_bytes).encode("utf-8") + b"\n"
        self.sock.settimeout(10.0)
        self.sock.sendall(data)

    def decide(self, decision: str = "approve", **override: Any) -> Dict[str, Any]:
        """Sends the decision for the waiting review (overrides alter the frame) and returns the waiter's ``result`` frame."""
        self.send(self.decision_frame(decision, **override))
        result = self.next_frame()
        while result["type"] == "review":  # a newer review was presented meanwhile: remember it, keep waiting for the result
            self.review_frame = result
            result = self.next_frame()
        assert result["type"] == "result", result
        if result["accepted"]:
            self.review_frame = None
        return result

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def attach_review_channel(session: Any, *, clock: Any = None) -> ScriptedHost:
    """A socket pair: the waiter end becomes the session's review channel, the host end is returned as the scripted host (and remembered as ``session.test_host``)."""
    waiter_sock, host_sock = socket.socketpair()
    channel = ReviewChannel(waiter_sock, **({"clock": clock} if clock is not None else {}))
    session.attach_review_channel(channel)
    host = ScriptedHost(host_sock, session.workspace)
    session.test_host = host
    return host


def channel_decide(workspace: str, launch_id: str, review_id: str, candidate_digest: str, decision: str, kind: str = "change_approval") -> Dict[str, Any]:
    """The old ``launch_approval.decide`` signature, over the channel: a decision frame BOUND to the identities given (so a wrong review id / digest / kind / launch is
    refused by the waiter exactly as the typed action once refused it), carrying the genuine capability of the review being waited on. Returns the accepted shape or
    raises the domain's ``ApprovalRefused`` with the waiter's refusal code. Call it from a test thread, like the desktop host."""
    from kriya.control import launch as launch_module
    from kriya.policy.approval import KINDS, ApprovalRefused

    session = launch_module._ACTIVE
    host = getattr(session, "test_host", None)
    if host is None:
        raise ApprovalRefused("LAUNCH_NOT_ACTIVE", "no active launch with a review channel")
    if kind not in KINDS or decision not in ("approve", "reject"):
        raise ApprovalRefused("INVALID_REQUEST", "the request is outside the protocol grammar")
    from kriya.control.persistence import load_launch_record
    pending = ((load_launch_record(session.workspace, launch_id) or {}).get("pending_approval") or {}) if launch_id == session.launch_id else {}
    if pending.get("review_id"):
        host.present(pending["review_id"])  # the waiter presents the review the record shows as pending; the capability used is THAT review's
    result = host.decide(decision, launch_id=launch_id, review_id=review_id, candidate_digest=candidate_digest, kind=kind)
    if not result["accepted"]:
        raise ApprovalRefused(result["code"], f"the waiter refused the decision ({result['code']})")
    return {"accepted": True, "code": "ACCEPTED", "launch_id": launch_id, "review_id": review_id, "decision": decision}
