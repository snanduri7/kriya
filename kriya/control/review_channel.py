"""D2 M2 (owner sign-off 2026-10-23): the private host<->waiter review channel, waiter side.

A desktop launch's human decision no longer travels through a file a same-UID workload could write. The host (Electron) starts ``kriya generate --launch-id``
with ONE extra inherited descriptor, a bidirectional UNIX socket at ``CHANNEL_FD``. The waiter (this process) takes it over, makes it non-inheritable
immediately (``kriya/tools/process.py`` spawns every child with ``close_fds=True`` and nothing passes ``pass_fds``; the non-inheritable flag is the
second, independent barrier), and speaks three newline-delimited JSON frames over it:

* ``review``   waiter -> host   the pending review and a fresh capability (32 random bytes, hex) that exists only in this process's memory and in this frame;
* ``decision`` host -> waiter   the person's decision, echoing the review identity and the capability;
* ``result``   waiter -> host   accepted, or a closed refusal code.

The capability is bound to exactly one review (launch, review id, kind, candidate digest), compared in constant time, and consumed by the first valid
decision. A wrong, missing, replayed or cross-review decision is REFUSED without consuming the live capability, so a forger cannot burn the legitimate
decision. The persisted decision file is audit output only and is never read here.

Framing: explicit protocol version; the review identity on every frame; a bounded frame size; a buffer that holds partial reads until the newline and
yields every complete frame of a read; malformed JSON, an unknown version/type, unknown or missing fields, a wrong-direction frame, a decision when no
review is open (out of order) and a decision for an already decided review (duplicate) all FAIL CLOSED: the channel is closed and the review ends
unavailable. End of file before a valid decision ends it unavailable too. Nothing assumes one ``recv`` equals one frame.

Claim (narrow, owner-fixed): a Kriya-launched same-UID workload that can rewrite persistent files but cannot inherit or access this descriptor cannot
forge the approval. It is not a defence against ptrace, process-memory inspection or an account compromise.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import socket
import stat
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from kriya.policy.approval import KINDS

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = 1
CHANNEL_FD = 3
MAX_FRAME_BYTES = 16 * 1024
_READ_CHUNK = 4096
_POLL_SECONDS = 0.25

FRAME_REVIEW, FRAME_DECISION, FRAME_RESULT = "review", "decision", "result"
DECISIONS = ("approve", "reject")

LAUNCH_ID_RE = re.compile(r"^\d{8}T\d{6}-[0-9a-f]{8}$")
REVIEW_ID_RE = re.compile(r"^ra-[0-9a-f]{24}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
CAPABILITY_MAX_CHARS = 128

# The closed table of protocol failures (each one closes the channel and ends the open review unavailable).
MALFORMED_JSON, FRAME_TOO_LARGE, NOT_AN_OBJECT = "MALFORMED_JSON", "FRAME_TOO_LARGE", "NOT_AN_OBJECT"
UNKNOWN_VERSION, UNKNOWN_TYPE, INVALID_FIELDS = "UNKNOWN_VERSION", "UNKNOWN_TYPE", "INVALID_FIELDS"
WRONG_DIRECTION, OUT_OF_ORDER, DUPLICATE_DECISION = "WRONG_DIRECTION", "OUT_OF_ORDER", "DUPLICATE_DECISION"
PROTOCOL_ERROR_CODES = (MALFORMED_JSON, FRAME_TOO_LARGE, NOT_AN_OBJECT, UNKNOWN_VERSION, UNKNOWN_TYPE, INVALID_FIELDS, WRONG_DIRECTION, OUT_OF_ORDER, DUPLICATE_DECISION)

# What a well-formed decision frame can be REFUSED with without failing the channel (the live capability is not consumed).
RESULT_ACCEPTED = "ACCEPTED"
RESULT_CAPABILITY_MISMATCH, RESULT_STALE_REVIEW, RESULT_WRONG_KIND, RESULT_EXPIRED = "CAPABILITY_MISMATCH", "STALE_REVIEW", "WRONG_REVIEW_KIND", "REVIEW_EXPIRED"
RESULT_CODES = (RESULT_ACCEPTED, RESULT_CAPABILITY_MISMATCH, RESULT_STALE_REVIEW, RESULT_WRONG_KIND, RESULT_EXPIRED)

OUTCOME_DECIDED, OUTCOME_EXPIRED, OUTCOME_EOF, OUTCOME_FAILED = "decided", "expired", "eof", "failed"

# Exact key sets per frame type; a decision's ``capability`` is the one optional key (absent or null is a REFUSED decision, not a protocol error).
_FIELDS = {
    FRAME_REVIEW: {"v", "type", "launch_id", "review_id", "kind", "candidate_digest", "capability"},
    FRAME_DECISION: {"v", "type", "launch_id", "review_id", "kind", "candidate_digest", "decision", "capability"},
    FRAME_RESULT: {"v", "type", "review_id", "accepted", "code"},
}
_OPTIONAL = {FRAME_DECISION: {"capability"}, FRAME_REVIEW: set(), FRAME_RESULT: set()}


class ChannelProtocolError(Exception):
    """A frame the protocol does not allow. ``code`` is one of PROTOCOL_ERROR_CODES. The message never carries frame content (it may hold a capability)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ChannelUnavailable(Exception):
    """No private channel can carry a review: not provided, not a socket, already failed or closed."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _check_text(value: Any, pattern: "re.Pattern[str]") -> bool:
    return isinstance(value, str) and pattern.match(value) is not None


def parse_frame(line: bytes) -> Dict[str, Any]:
    """One frame (without its newline) as a dict, or ``ChannelProtocolError``. Strict: nothing is repaired or ignored."""
    try:
        text = line.decode("utf-8")
        payload = json.loads(text, parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ChannelProtocolError(MALFORMED_JSON) from error
    if not isinstance(payload, dict):
        raise ChannelProtocolError(NOT_AN_OBJECT)
    version = payload.get("v")
    if isinstance(version, bool) or version != PROTOCOL_VERSION or not isinstance(version, int):
        raise ChannelProtocolError(UNKNOWN_VERSION)
    frame_type = payload.get("type")
    if frame_type not in _FIELDS:
        raise ChannelProtocolError(UNKNOWN_TYPE)
    keys = set(payload)
    if keys - _FIELDS[frame_type] or (_FIELDS[frame_type] - _OPTIONAL[frame_type]) - keys:
        raise ChannelProtocolError(INVALID_FIELDS)
    if not _check_text(payload["review_id"], REVIEW_ID_RE):
        raise ChannelProtocolError(INVALID_FIELDS)
    if frame_type == FRAME_RESULT:
        if not isinstance(payload["accepted"], bool) or not isinstance(payload["code"], str):
            raise ChannelProtocolError(INVALID_FIELDS)
        return payload
    if not (_check_text(payload["launch_id"], LAUNCH_ID_RE) and _check_text(payload["candidate_digest"], DIGEST_RE) and payload["kind"] in KINDS):
        raise ChannelProtocolError(INVALID_FIELDS)
    capability = payload.get("capability")
    if frame_type == FRAME_REVIEW:
        if not isinstance(capability, str) or not capability or len(capability) > CAPABILITY_MAX_CHARS:
            raise ChannelProtocolError(INVALID_FIELDS)
    else:
        if payload["decision"] not in DECISIONS:
            raise ChannelProtocolError(INVALID_FIELDS)
        if capability is not None and (not isinstance(capability, str) or len(capability) > CAPABILITY_MAX_CHARS):
            raise ChannelProtocolError(INVALID_FIELDS)
    return payload


def _reject_constant(_name: str) -> Any:
    raise ValueError("NaN/Infinity is not JSON")


class FrameParser:
    """Buffers bytes until a newline, yields every complete frame, bounds the frame size. After any error it stays failed (never resynchronises)."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        self._max = max_frame_bytes
        self._buffer = bytearray()
        self._failure: Optional[str] = None

    def feed(self, data: bytes) -> List[Dict[str, Any]]:
        if self._failure is not None:
            raise ChannelProtocolError(self._failure)
        self._buffer.extend(data)
        frames: List[Dict[str, Any]] = []
        try:
            while True:
                end = self._buffer.find(b"\n")
                if end < 0:
                    if len(self._buffer) > self._max:
                        raise ChannelProtocolError(FRAME_TOO_LARGE)
                    return frames
                if end > self._max:
                    raise ChannelProtocolError(FRAME_TOO_LARGE)
                line = bytes(self._buffer[:end])
                del self._buffer[:end + 1]
                frames.append(parse_frame(line))
        except ChannelProtocolError as error:
            self._failure = error.code
            self._buffer.clear()
            raise


def encode_frame(frame: Dict[str, Any]) -> bytes:
    data = json.dumps(frame, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if len(data) > MAX_FRAME_BYTES:
        raise ChannelProtocolError(FRAME_TOO_LARGE)
    return data


@dataclass(frozen=True)
class ChannelOutcome:
    """How one review's wait ended. ``decision`` is set only for OUTCOME_DECIDED; ``code`` carries the protocol error for OUTCOME_FAILED."""

    kind: str
    decision: Optional[str] = None
    code: Optional[str] = None


@dataclass
class _OpenReview:
    launch_id: str
    review_id: str
    kind: str
    candidate_digest: str
    capability: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ReviewChannel:
    """The waiter's end of the private socket. One review is open at a time; the capability lives only in ``_open`` and is dropped when it is consumed."""

    def __init__(self, sock: socket.socket, *, max_frame_bytes: int = MAX_FRAME_BYTES, clock: Callable[[], datetime] = _now) -> None:
        sock.setblocking(False)
        self._sock: Optional[socket.socket] = sock
        self._parser = FrameParser(max_frame_bytes)
        self._backlog: Deque[Dict[str, Any]] = deque()
        self._open: Optional[_OpenReview] = None
        self._decided: Set[str] = set()
        self._failure: Optional[str] = None
        self._eof = False
        self._clock = clock

    def __repr__(self) -> str:  # never shows the capability
        return f"<ReviewChannel open={self._open.review_id if self._open else None} failed={self._failure} eof={self._eof}>"

    # ------------------------------------------------------------------ lifecycle
    @property
    def failure(self) -> Optional[str]:
        return self._failure

    def close(self) -> None:
        sock, self._sock = self._sock, None
        self._open = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def abandon(self) -> None:
        """The wait for the open review ended without an outcome (cancelled): forget it - and its capability - so the channel can carry the next review."""
        self._open = None

    def _fail(self, code: str) -> ChannelOutcome:
        self._failure = code
        logger.warning("review channel failed closed: %s", code)
        self.close()
        return ChannelOutcome(OUTCOME_FAILED, code=code)

    # ------------------------------------------------------------------ io
    async def _send(self, frame: Dict[str, Any]) -> bool:
        if self._sock is None:
            return False
        try:
            await asyncio.get_running_loop().sock_sendall(self._sock, encode_frame(frame))
            return True
        except OSError:
            self._eof = True
            return False

    def _absorb(self, data: bytes) -> Optional[ChannelOutcome]:
        if not data:
            self._eof = True
            return None
        try:
            self._backlog.extend(self._parser.feed(data))
        except ChannelProtocolError as error:
            return self._fail(error.code)
        return None

    def _drain_nowait(self) -> Optional[ChannelOutcome]:
        """Reads whatever is already waiting (stray frames, EOF) without blocking."""
        while self._sock is not None and not self._eof:
            try:
                data = self._sock.recv(_READ_CHUNK)
            except (BlockingIOError, InterruptedError):
                return None
            except OSError:
                self._eof = True
                return None
            failed = self._absorb(data)
            if failed is not None or not data:
                return failed
        return None

    async def _wait_readable(self, timeout: float) -> bool:
        """True when the socket has bytes (or EOF) to read within ``timeout``. It waits for READINESS only and consumes nothing: a poll that times out, or a wait that is cancelled,
        cannot discard a received frame (a ``wait_for(sock_recv(...))`` poll could: when the loop was delayed across the timeout the receive had already taken the bytes and
        the cancellation then dropped them - a legitimate decision silently lost, the host given no result frame, the engine left waiting until the review expired)."""
        sock = self._sock
        if sock is None:
            return False
        loop = asyncio.get_running_loop()
        ready: "asyncio.Future[None]" = loop.create_future()

        def on_readable() -> None:
            if not ready.done():
                ready.set_result(None)
        try:
            fd = sock.fileno()
            loop.add_reader(fd, on_readable)
        except (OSError, ValueError):
            return False
        try:
            await asyncio.wait_for(ready, timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            try:
                loop.remove_reader(fd)
            except (OSError, ValueError, KeyError):
                pass

    def _read_once(self) -> Optional[ChannelOutcome]:
        """Reads ONE chunk that readiness reported and absorbs it in the same synchronous step (the bytes are never held anywhere a cancellation could drop them)."""
        if self._sock is None:
            return None
        try:
            data = self._sock.recv(_READ_CHUNK)
        except (BlockingIOError, InterruptedError):
            return None  # another reader took it, or a spurious wakeup: nothing to absorb, poll again
        except OSError:
            self._eof = True
            return None
        return self._absorb(data)

    # ------------------------------------------------------------------ one review
    async def offer(self, *, launch_id: str, review_id: str, kind: str, candidate_digest: str) -> None:
        """Opens the review: draws its capability and sends the ``review`` frame. Raises ``ChannelUnavailable`` when the channel cannot carry it."""
        if self._failure is not None or self._sock is None:
            raise ChannelUnavailable(self._failure or "CHANNEL_CLOSED")
        self._open = None  # a newer review SUPERSEDES the open one: its capability is dropped and its waiter ends expired (await_decision checks it is still the open one)
        failed = self._drain_nowait()
        if failed is not None:
            raise ChannelUnavailable(failed.code or "CHANNEL_FAILED")
        if self._backlog:  # a frame arrived while no review was open: nothing may be waiting for it
            stray = self._backlog.popleft()
            code = DUPLICATE_DECISION if stray.get("type") == FRAME_DECISION and stray.get("review_id") in self._decided else OUT_OF_ORDER
            raise ChannelUnavailable(self._fail(code).code or "CHANNEL_FAILED")
        if self._eof:
            raise ChannelUnavailable("CHANNEL_EOF")
        capability = secrets.token_hex(32)
        self._open = _OpenReview(launch_id, review_id, kind, candidate_digest, capability)
        frame = {"v": PROTOCOL_VERSION, "type": FRAME_REVIEW, "launch_id": launch_id, "review_id": review_id, "kind": kind,
                 "candidate_digest": candidate_digest, "capability": capability}
        if not await self._send(frame):
            self._open = None
            raise ChannelUnavailable("CHANNEL_EOF")

    async def await_decision(self, expires_at: datetime) -> ChannelOutcome:
        """Waits for the open review's decision until the wall-clock ``expires_at``. Never raises for a bad peer: it returns the outcome."""
        review = self._open
        if review is None:
            raise ValueError("no review is open")
        while True:
            if self._open is not review:  # superseded by a newer review (or abandoned): this review is over and its capability is gone
                return ChannelOutcome(OUTCOME_EXPIRED)
            while self._backlog:
                outcome = await self._handle(self._backlog.popleft(), review, expires_at)
                if outcome is not None:
                    return outcome
            if self._failure is not None:
                return ChannelOutcome(OUTCOME_FAILED, code=self._failure)
            if self._eof or self._sock is None:
                self._open = None
                return ChannelOutcome(OUTCOME_EOF)
            remaining = (expires_at - self._clock()).total_seconds()
            if remaining <= 0:
                self._open = None
                return ChannelOutcome(OUTCOME_EXPIRED)
            if not await self._wait_readable(min(remaining, _POLL_SECONDS)):
                continue
            failed = self._read_once()
            if failed is not None:
                self._open = None
                return failed

    async def _handle(self, frame: Dict[str, Any], review: _OpenReview, expires_at: datetime) -> Optional[ChannelOutcome]:
        if frame["type"] != FRAME_DECISION:
            self._open = None
            return self._fail(WRONG_DIRECTION)
        if frame["review_id"] in self._decided:
            self._open = None
            return self._fail(DUPLICATE_DECISION)
        supplied = frame.get("capability")
        # the comparison always runs, whatever else is wrong with the frame, and always in constant time
        capability_ok = isinstance(supplied, str) and hmac.compare_digest(supplied.encode("utf-8"), review.capability.encode("utf-8"))
        refusal: Optional[str] = None
        if frame["review_id"] != review.review_id or frame["launch_id"] != review.launch_id or frame["candidate_digest"] != review.candidate_digest:
            refusal = RESULT_STALE_REVIEW
        elif frame["kind"] != review.kind:
            refusal = RESULT_WRONG_KIND
        elif not capability_ok:
            refusal = RESULT_CAPABILITY_MISMATCH
        elif self._clock() > expires_at:
            refusal = RESULT_EXPIRED
        if refusal is not None:
            await self._send({"v": PROTOCOL_VERSION, "type": FRAME_RESULT, "review_id": frame["review_id"], "accepted": False, "code": refusal})
            if refusal == RESULT_EXPIRED:
                self._open = None
                return ChannelOutcome(OUTCOME_EXPIRED)
            return None
        self._open = None  # CONSUMED: one valid decision per capability; the capability is dropped with the open review
        self._decided.add(review.review_id)
        await self._send({"v": PROTOCOL_VERSION, "type": FRAME_RESULT, "review_id": review.review_id, "accepted": True, "code": RESULT_ACCEPTED})
        return ChannelOutcome(OUTCOME_DECIDED, decision=frame["decision"])


def open_review_channel_from_fd(fd: Optional[int] = None, *, clock: Callable[[], datetime] = _now) -> ReviewChannel:
    """Takes over the inherited descriptor: it must be a socket, and it is made non-inheritable IMMEDIATELY (before anything can spawn). Raises
    ``ChannelUnavailable`` otherwise - a launch without a channel has no way to be approved. ``fd`` defaults to the dedicated ``CHANNEL_FD``."""
    fd = CHANNEL_FD if fd is None else fd
    try:
        mode = os.fstat(fd).st_mode
    except OSError as error:
        raise ChannelUnavailable("CHANNEL_NOT_PROVIDED") from error
    if not stat.S_ISSOCK(mode):
        raise ChannelUnavailable("CHANNEL_NOT_A_SOCKET")
    os.set_inheritable(fd, False)
    return ReviewChannel(socket.socket(fileno=fd), clock=clock)


__all__ = [
    "CHANNEL_FD", "MAX_FRAME_BYTES", "PROTOCOL_VERSION", "PROTOCOL_ERROR_CODES", "RESULT_CODES", "ChannelOutcome", "ChannelProtocolError", "ChannelUnavailable",
    "FrameParser", "ReviewChannel", "encode_frame", "open_review_channel_from_fd", "parse_frame",
]
