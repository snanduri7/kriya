"""REVIEW-CHANNEL-LOST-DECISION-001: ``ReviewChannel.await_decision`` must never discard a frame it has received.

MEASURED (2026-10-27, tests/ + the pre-S7 baseline alike): each 250 ms poll was ``asyncio.wait_for(loop.sock_recv(...), timeout)``. When the event loop was delayed across the
timeout boundary the receive had already taken the bytes out of the kernel buffer and the cancellation then dropped them: the person's decision was silently lost, the host was
given no ``result`` frame, and the engine waited until the review's wall-clock expiry (an hour). Under load this surfaced as hour-scale stalls in the Greenfield / authority review
tests. The poll now waits for READINESS only and reads in the same synchronous step that absorbs the bytes, so a timeout or a cancellation has nothing to lose.

Every test below stalls the LOOP THREAD synchronously (``time.sleep`` inside the coroutine) - exactly what CPU/IO starvation does - so the failure is deterministic, not statistical."""
import ast
import asyncio
import socket
import time
from datetime import timedelta
from pathlib import Path

import pytest
from _review_channel_host import ScriptedHost

from kriya.control import review_channel
from kriya.control.review_channel import (
    DUPLICATE_DECISION,
    OUTCOME_DECIDED,
    OUTCOME_EOF,
    OUTCOME_EXPIRED,
    OUTCOME_FAILED,
    ChannelUnavailable,
    ReviewChannel,
)

LID = "20261010T101010-abcdef01"
RID = "ra-0123456789abcdef01234567"
DIGEST = "sha256:" + "ab" * 32
POLL = review_channel._POLL_SECONDS            # pylint: disable=protected-access


class _Open:
    """One open review with its waiter parked in its first poll and the host holding the review frame (so it can build a valid decision)."""

    def __init__(self, seconds: float = 30.0) -> None:
        waiter, host_sock = socket.socketpair()
        self.channel = ReviewChannel(waiter)
        self.host = ScriptedHost(host_sock, "/nowhere")
        self.seconds = seconds
        self.task: "asyncio.Task" = None  # type: ignore[assignment]

    async def start(self) -> "_Open":
        await self.channel.offer(launch_id=LID, review_id=RID, kind="change_approval", candidate_digest=DIGEST)
        self.task = asyncio.ensure_future(self.channel.await_decision(review_channel._now() + timedelta(seconds=self.seconds)))      # pylint: disable=protected-access
        await asyncio.sleep(0.05)                      # the waiter is now parked in its first poll
        self.host.review(timeout=2)
        return self

    def close(self) -> None:
        self.channel.close()
        self.host.close()


def _no_reader_registered(channel: ReviewChannel, loop) -> bool:
    fd = channel._sock.fileno()                        # pylint: disable=protected-access
    return loop.remove_reader(fd) is False             # False: nothing was registered


def _leaked_tasks() -> set:
    return {t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()}


@pytest.mark.asyncio
@pytest.mark.parametrize("stall", [0.3, 0.6, 1.0], ids=lambda s: f"stall-{s}s")
async def test_a_decision_that_is_readable_while_the_loop_is_stalled_past_the_poll_is_delivered(stall):
    assert stall > POLL
    review = await _Open().start()
    try:
        review.host.send(review.host.decision_frame("approve"))
        time.sleep(stall)                              # the loop thread is blocked: the socket is readable AND the poll timer has expired when the loop runs again
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "approve")
        result = review.host.next_frame(timeout=2)
        assert result["type"] == "result" and result["accepted"] is True        # the host is answered, never left waiting
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_decision_with_no_stall_is_still_delivered():
    review = await _Open().start()
    try:
        review.host.send(review.host.decision_frame("approve"))
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "approve")
        assert review.host.next_frame(timeout=2)["accepted"] is True
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_decision_that_arrives_after_several_poll_intervals_is_delivered():
    review = await _Open().start()
    try:
        await asyncio.sleep(POLL * 4)                  # four polls elapse with nothing to read
        assert not review.task.done()
        review.host.send(review.host.decision_frame("reject"))
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "reject")
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_wrong_kind_refusal_then_the_correct_decision_in_one_stall_both_get_their_answer():
    """The production signature: tests/test_greenfield_runtime_end_to_end.py::test_a_decision_of_the_wrong_kind_cannot_approve_the_runtime_review stalled for the whole review window."""
    review = await _Open().start()
    try:
        review.host.send(review.host.decision_frame("approve", kind="runtime_command"))       # refused: the wrong kind
        review.host.send(review.host.decision_frame("approve"))                               # the person then decides correctly
        time.sleep(POLL * 2)
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "approve")
        first, second = review.host.next_frame(timeout=2), review.host.next_frame(timeout=2)
        assert (first["accepted"], first["code"]) == (False, "WRONG_REVIEW_KIND") and second["accepted"] is True
    finally:
        review.close()


@pytest.mark.asyncio
async def test_no_decision_is_delivered_twice_and_the_replay_still_fails_the_channel_closed():
    review = await _Open().start()
    try:
        frame = review.host.decision_frame("approve")
        review.host.send(frame)
        review.host.send(frame)                        # a replay of the one-shot decision, already readable
        time.sleep(POLL * 2)
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "approve")
        assert review.host.next_frame(timeout=2)["accepted"] is True
        with pytest.raises(TimeoutError):              # exactly ONE result frame: the decision was not accepted a second time
            review.host.next_frame(timeout=0.3)
        with pytest.raises(ChannelUnavailable) as refused:      # and the replay is found, not swallowed: the next review cannot be carried
            await review.channel.offer(launch_id=LID, review_id="ra-" + "f" * 24, kind="change_approval", candidate_digest=DIGEST)
        assert refused.value.code == DUPLICATE_DECISION
    finally:
        review.close()


@pytest.mark.asyncio
async def test_cancelling_the_wait_leaves_no_task_and_no_reader_and_consumes_no_bytes():
    review = await _Open().start()
    loop = asyncio.get_running_loop()
    try:
        review.host.send(review.host.decision_frame("approve"))
        review.task.cancel()                           # the decision is in the kernel buffer; the wait is cancelled before the loop ever services it
        with pytest.raises(asyncio.CancelledError):
            await review.task
        assert _no_reader_registered(review.channel, loop)
        assert _leaked_tasks() == set()
        pending = review.channel._sock.recv(65536, socket.MSG_PEEK)           # pylint: disable=protected-access
        assert b'"type": "decision"' in pending       # a cancellation consumed nothing: the frame is still there for the next waiter
    finally:
        review.close()


@pytest.mark.asyncio
async def test_the_expiry_ends_the_wait_with_no_task_and_no_reader_left():
    review = await _Open(seconds=0.4).start()
    loop = asyncio.get_running_loop()
    try:
        outcome = await asyncio.wait_for(review.task, 3)
        assert outcome.kind == OUTCOME_EXPIRED
        assert _no_reader_registered(review.channel, loop)
        assert _leaked_tasks() == set()
    finally:
        review.close()


@pytest.mark.asyncio
async def test_end_of_file_during_a_stall_is_still_unavailable():
    review = await _Open().start()
    try:
        review.host.close()
        time.sleep(POLL * 2)
        outcome = await asyncio.wait_for(review.task, 3)
        assert outcome.kind == OUTCOME_EOF
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_protocol_violation_during_a_stall_still_fails_the_channel_closed():
    review = await _Open().start()
    try:
        review.host.send(b"this is not a frame\n")
        time.sleep(POLL * 2)
        outcome = await asyncio.wait_for(review.task, 3)
        assert outcome.kind == OUTCOME_FAILED and review.channel.failure is not None
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_valid_decision_is_honoured_before_a_later_chunk_that_violates_the_protocol_ends_the_channel():
    """Unchanged semantics: ONE chunk is read per poll, so a valid decision in the first chunk is answered even though the rest of the same burst (more than a frame bound of
    newline-less bytes, arriving in later chunks) is a violation - which then ends the channel for the NEXT use (draining everything first would void the decision)."""
    review = await _Open().start()
    try:
        review.host.sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 18)         # host and waiter share this thread: the whole burst must fit the kernel buffer
        review.host.send(review.host.decision_frame("approve"))
        review.host.send(b"x" * (review_channel.MAX_FRAME_BYTES + 4 * review_channel._READ_CHUNK))        # pylint: disable=protected-access
        time.sleep(POLL * 2)
        outcome = await asyncio.wait_for(review.task, 3)
        assert (outcome.kind, outcome.decision) == (OUTCOME_DECIDED, "approve")
        assert review.host.next_frame(timeout=2)["accepted"] is True
        with pytest.raises(ChannelUnavailable):
            await review.channel.offer(launch_id=LID, review_id="ra-" + "e" * 24, kind="change_approval", candidate_digest=DIGEST)
        assert review.channel.failure is not None
    finally:
        review.close()


@pytest.mark.asyncio
async def test_a_closed_channel_or_socket_is_never_reported_readable():
    waiter, host_sock = socket.socketpair()
    channel = ReviewChannel(waiter)
    try:
        waiter.close()                                 # the socket object is closed under the channel: no usable descriptor
        assert await channel._wait_readable(0.1) is False         # pylint: disable=protected-access
        channel.close()                                # and a closed channel has no socket at all
        assert await channel._wait_readable(0.1) is False         # pylint: disable=protected-access
    finally:
        host_sock.close()


def test_an_empty_nonblocking_read_is_nothing_to_absorb_and_a_reset_is_end_of_file():
    class Stub:
        def __init__(self, real, error):
            self.real, self.error = real, error

        def recv(self, _size):
            raise self.error

        def __getattr__(self, name):
            return getattr(self.real, name)

    waiter, host_sock = socket.socketpair()
    channel = ReviewChannel(waiter)
    try:
        # nothing to read is not EOF; an OS error on the socket is end of file
        assert channel._read_once() is None and channel._eof is False             # pylint: disable=protected-access
        channel._sock = Stub(waiter, ConnectionResetError())                     # pylint: disable=protected-access
        assert channel._read_once() is None and channel._eof is True              # pylint: disable=protected-access
    finally:
        host_sock.close()
        waiter.close()


def test_the_poll_never_wraps_a_receive_in_a_cancelling_timeout():
    """Tripwire: no ``sock_recv`` anywhere in the channel - a receive that a timeout can cancel is what lost the decisions."""
    tree = ast.parse(Path(review_channel.__file__).read_text(encoding="utf-8"))
    calls = [node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute) and node.attr == "sock_recv"]
    assert calls == []
