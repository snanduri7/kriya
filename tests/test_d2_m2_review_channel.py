"""D2 M2 slice 1 (owner sign-off 2026-10-23): the waiter side of the private review channel - parser, state machine, capability, fd takeover.

The host is a scripted peer on the other end of a REAL socket pair; nothing here mocks the socket. The protocol: three newline-delimited JSON frames
(review / decision / result), a version on every frame, the review identity on every frame, a bounded frame size, partial reads buffered, several frames per
read, and every protocol violation closing the channel (fail closed). A wrong / missing / replayed / cross-review capability is REFUSED and does not consume
the live capability.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
from datetime import datetime, timedelta, timezone

import pytest

from kriya.control import review_channel as rc
from kriya.control.review_channel import (
    CHANNEL_FD,
    MAX_FRAME_BYTES,
    ChannelOutcome,
    ChannelProtocolError,
    ChannelUnavailable,
    FrameParser,
    ReviewChannel,
    encode_frame,
    open_review_channel_from_fd,
    parse_frame,
)

LAUNCH = "20261023T120000-abcdef12"
RID = "ra-" + "1" * 24
RID2 = "ra-" + "2" * 24
DIGEST = "sha256:" + "a" * 64
DIGEST2 = "sha256:" + "b" * 64


def _decision(capability, *, review_id=RID, digest=DIGEST, kind="change_approval", decision="approve", launch=LAUNCH, **extra):
    frame = {"v": 1, "type": "decision", "launch_id": launch, "review_id": review_id, "kind": kind, "candidate_digest": digest, "decision": decision}
    if capability is not ...:
        frame["capability"] = capability
    frame.update(extra)
    return frame


def _line(frame) -> bytes:
    return json.dumps(frame).encode() + b"\n"


class Host:
    """The scripted peer: reads the waiter's frames through the same strict parser, writes arbitrary bytes."""

    def __init__(self, sock: socket.socket) -> None:
        sock.setblocking(False)
        self.sock = sock
        self.parser = FrameParser()
        self.pending = []

    async def frames(self, count: int = 1, timeout: float = 5.0):
        loop = asyncio.get_running_loop()
        while len(self.pending) < count:
            data = await asyncio.wait_for(loop.sock_recv(self.sock, 4096), timeout)
            if not data:
                raise EOFError("the waiter closed the channel")
            self.pending.extend(self.parser.feed(data))
        taken, self.pending = self.pending[:count], self.pending[count:]
        return taken

    async def send(self, *chunks: bytes) -> None:
        loop = asyncio.get_running_loop()
        for chunk in chunks:
            await loop.sock_sendall(self.sock, chunk)

    async def at_eof(self, timeout: float = 5.0) -> bool:
        loop = asyncio.get_running_loop()
        try:
            return await asyncio.wait_for(loop.sock_recv(self.sock, 1), timeout) == b""
        except ConnectionResetError:
            return True


def _soon(seconds: float = 30.0) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


@pytest.fixture(name="pair")
def _pair():
    waiter_sock, host_sock = socket.socketpair()
    channel = ReviewChannel(waiter_sock)
    host = Host(host_sock)
    yield channel, host
    channel.close()
    host_sock.close()


async def _open(channel, host, **overrides):
    args = {"launch_id": LAUNCH, "review_id": RID, "kind": "change_approval", "candidate_digest": DIGEST, **overrides}
    await channel.offer(**args)
    (review,) = await host.frames(1)
    return review


# --------------------------------------------------------------------------- parser

def test_a_valid_frame_of_each_type_parses_and_round_trips():
    review = {"v": 1, "type": "review", "launch_id": LAUNCH, "review_id": RID, "kind": "runtime_command", "candidate_digest": DIGEST, "capability": "ab" * 32}
    result = {"v": 1, "type": "result", "review_id": RID, "accepted": True, "code": "ACCEPTED"}
    for frame in (review, _decision("ab" * 32), result):
        assert parse_frame(encode_frame(frame).rstrip(b"\n")) == frame


def test_the_parser_buffers_partial_reads_until_the_newline():
    parser = FrameParser()
    data = _line(_decision("c" * 64))
    emitted = []
    for index in range(len(data)):  # one byte per read: no assumption that one recv is one frame
        emitted += parser.feed(data[index:index + 1])
    assert emitted == [_decision("c" * 64)]


def test_the_parser_yields_every_frame_of_a_single_read_in_order():
    parser = FrameParser()
    frames = parser.feed(_line(_decision("x")) + _line(_decision("y", decision="reject")) + _line(_decision("z"))[:10])
    assert [f["capability"] for f in frames] == ["x", "y"] and frames[1]["decision"] == "reject"
    assert [f["capability"] for f in parser.feed(_line(_decision("z"))[10:])] == ["z"]


@pytest.mark.parametrize(("data", "code"), [
    (b"not json\n", rc.MALFORMED_JSON),
    (b"\n", rc.MALFORMED_JSON),
    (b"\xff\xfe\n", rc.MALFORMED_JSON),
    (b'{"v":1,"type":"decision","x":NaN}\n', rc.MALFORMED_JSON),
    (b"[1,2]\n", rc.NOT_AN_OBJECT),
    (b'"text"\n', rc.NOT_AN_OBJECT),
    (_line({"type": "decision"}), rc.UNKNOWN_VERSION),
    (_line({**_decision("c"), "v": 2}), rc.UNKNOWN_VERSION),
    (_line({**_decision("c"), "v": "1"}), rc.UNKNOWN_VERSION),
    (_line({**_decision("c"), "v": True}), rc.UNKNOWN_VERSION),
    (_line({**_decision("c"), "v": 1.0}), rc.UNKNOWN_VERSION),
    (_line({**_decision("c"), "type": "approve"}), rc.UNKNOWN_TYPE),
    (_line({**_decision("c"), "type": None}), rc.UNKNOWN_TYPE),
    (_line(_decision("c", surprise=1)), rc.INVALID_FIELDS),
    (_line({k: v for k, v in _decision("c").items() if k != "review_id"}), rc.INVALID_FIELDS),
    (_line({k: v for k, v in _decision("c").items() if k != "candidate_digest"}), rc.INVALID_FIELDS),
    (_line(_decision("c", review_id="ra-short")), rc.INVALID_FIELDS),
    (_line(_decision("c", digest="sha256:zz")), rc.INVALID_FIELDS),
    (_line(_decision("c", kind="anything")), rc.INVALID_FIELDS),
    (_line(_decision("c", decision="maybe")), rc.INVALID_FIELDS),
    (_line(_decision("c", launch="../escape")), rc.INVALID_FIELDS),
    (_line(_decision(7)), rc.INVALID_FIELDS),
    (_line(_decision("c" * (rc.CAPABILITY_MAX_CHARS + 1))), rc.INVALID_FIELDS),
    (_line({"v": 1, "type": "result", "review_id": RID, "accepted": "yes", "code": "ACCEPTED"}), rc.INVALID_FIELDS),
])
def test_every_malformed_or_unknown_frame_fails_closed_with_its_code(data, code):
    with pytest.raises(ChannelProtocolError) as refused:
        FrameParser().feed(data)
    assert refused.value.code == code and code in rc.PROTOCOL_ERROR_CODES


def test_a_frame_over_the_bound_is_refused_with_or_without_its_newline():
    big = b"x" * (MAX_FRAME_BYTES + 1)
    with pytest.raises(ChannelProtocolError) as no_newline:
        FrameParser().feed(big)
    with pytest.raises(ChannelProtocolError) as with_newline:
        FrameParser().feed(big + b"\n")
    assert no_newline.value.code == with_newline.value.code == rc.FRAME_TOO_LARGE
    FrameParser(max_frame_bytes=2000).feed(b"x" * 1999)  # a partial frame under the bound is simply buffered


def test_after_an_error_the_parser_never_resynchronises():
    parser = FrameParser()
    with pytest.raises(ChannelProtocolError):
        parser.feed(b"garbage\n")
    with pytest.raises(ChannelProtocolError) as again:
        parser.feed(_line(_decision("c")))  # a perfectly good frame after the failure is still refused
    assert again.value.code == rc.MALFORMED_JSON


# --------------------------------------------------------------------------- the state machine

@pytest.mark.asyncio
async def test_a_valid_decision_is_accepted_and_the_result_says_so(pair):
    channel, host = pair
    review = await _open(channel, host)
    assert review["type"] == "review" and review["review_id"] == RID and review["kind"] == "change_approval" and review["candidate_digest"] == DIGEST
    assert len(review["capability"]) == 64 and int(review["capability"], 16) >= 0
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(review["capability"], decision="reject")))
    assert await waiting == ChannelOutcome(rc.OUTCOME_DECIDED, decision="reject")
    (result,) = await host.frames(1)
    assert result == {"v": 1, "type": "result", "review_id": RID, "accepted": True, "code": "ACCEPTED"}


@pytest.mark.asyncio
async def test_a_wrong_or_missing_capability_is_refused_and_does_not_consume_the_live_one(pair):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision("0" * 64)), _line(_decision(...)), _line(_decision(None)), _line(_decision("")))
    refusals = await host.frames(4)
    assert [r["code"] for r in refusals] == ["CAPABILITY_MISMATCH"] * 4 and not any(r["accepted"] for r in refusals)
    assert not waiting.done()
    await host.send(_line(_decision(review["capability"])))
    assert await waiting == ChannelOutcome(rc.OUTCOME_DECIDED, decision="approve")  # the legitimate decision still lands after four refused attempts


@pytest.mark.asyncio
async def test_a_capability_of_another_review_cannot_decide_this_one(pair):
    channel, host = pair
    first = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(first["capability"])))
    assert (await waiting).kind == rc.OUTCOME_DECIDED
    await host.frames(1)
    second = await _open(channel, host, review_id=RID2, candidate_digest=DIGEST2)
    assert second["capability"] != first["capability"]
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(first["capability"], review_id=RID2, digest=DIGEST2)))  # review 1's capability on review 2's identity
    (refused,) = await host.frames(1)
    assert refused["code"] == "CAPABILITY_MISMATCH" and not waiting.done()
    await host.send(_line(_decision(second["capability"], review_id=RID2, digest=DIGEST2)))
    assert (await waiting).kind == rc.OUTCOME_DECIDED


@pytest.mark.asyncio
@pytest.mark.parametrize(("override", "code"), [
    ({"review_id": RID2}, "STALE_REVIEW"),
    ({"digest": DIGEST2}, "STALE_REVIEW"),
    ({"launch": "20261023T120000-00000000"}, "STALE_REVIEW"),
    ({"kind": "runtime_command"}, "WRONG_REVIEW_KIND"),
])
async def test_a_decision_bound_to_another_review_launch_digest_or_kind_is_refused_without_consuming(pair, override, code):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(review["capability"], **override)))  # the RIGHT capability on the WRONG binding
    (refused,) = await host.frames(1)
    assert refused["code"] == code and refused["accepted"] is False and not waiting.done()
    await host.send(_line(_decision(review["capability"])))
    assert (await waiting).kind == rc.OUTCOME_DECIDED


@pytest.mark.asyncio
async def test_a_decision_is_one_shot_a_replay_fails_the_channel_closed(pair):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    frame = _line(_decision(review["capability"]))
    await host.send(frame)
    assert (await waiting).kind == rc.OUTCOME_DECIDED
    await host.frames(1)
    await host.send(frame)  # the same bytes again: a duplicate for an already decided review
    await asyncio.sleep(0.05)
    with pytest.raises(ChannelUnavailable) as stray:
        await channel.offer(launch_id=LAUNCH, review_id=RID2, kind="change_approval", candidate_digest=DIGEST2)
    assert stray.value.code == rc.DUPLICATE_DECISION and channel.failure == rc.DUPLICATE_DECISION
    assert await host.at_eof()  # the channel was closed on the peer


@pytest.mark.asyncio
async def test_a_replayed_decision_of_a_finished_review_while_the_next_review_is_open_fails_closed(pair):
    channel, host = pair
    first = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    replayable = _line(_decision(first["capability"]))
    await host.send(replayable)
    assert (await waiting).kind == rc.OUTCOME_DECIDED
    await host.frames(1)
    await _open(channel, host, review_id=RID2, candidate_digest=DIGEST2)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(replayable)  # review 1's accepted decision, captured and replayed while review 2 waits
    assert await waiting == ChannelOutcome(rc.OUTCOME_FAILED, code=rc.DUPLICATE_DECISION)


@pytest.mark.asyncio
async def test_a_second_valid_looking_decision_in_the_same_read_is_a_duplicate_for_the_next_use(pair):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    frame = _line(_decision(review["capability"]))
    await host.send(frame + frame)  # two frames in one read
    assert (await waiting).kind == rc.OUTCOME_DECIDED  # the first is honoured
    with pytest.raises(ChannelUnavailable) as stray:  # the second is out of order the moment the channel is used again
        await channel.offer(launch_id=LAUNCH, review_id=RID2, kind="change_approval", candidate_digest=DIGEST2)
    assert stray.value.code == rc.DUPLICATE_DECISION


@pytest.mark.asyncio
async def test_a_decision_when_no_review_is_open_is_out_of_order(pair):
    channel, host = pair
    await host.send(_line(_decision("c" * 64)))
    await asyncio.sleep(0.05)
    with pytest.raises(ChannelUnavailable) as stray:
        await channel.offer(launch_id=LAUNCH, review_id=RID, kind="change_approval", candidate_digest=DIGEST)
    assert stray.value.code == rc.OUT_OF_ORDER


@pytest.mark.asyncio
async def test_a_wrong_direction_frame_fails_closed(pair):
    channel, host = pair
    await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line({"v": 1, "type": "result", "review_id": RID, "accepted": True, "code": "ACCEPTED"}))
    assert await waiting == ChannelOutcome(rc.OUTCOME_FAILED, code=rc.WRONG_DIRECTION)


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "code"), [
    (b"{broken\n", rc.MALFORMED_JSON),
    (_line({"v": 2, "type": "decision"}), rc.UNKNOWN_VERSION),
    (_line({"v": 1, "type": "ping"}), rc.UNKNOWN_TYPE),
    (b"x" * (MAX_FRAME_BYTES + 10), rc.FRAME_TOO_LARGE),
])
async def test_a_protocol_violation_ends_the_review_unavailable_and_closes_the_channel(pair, data, code):
    channel, host = pair
    await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(data)
    assert await waiting == ChannelOutcome(rc.OUTCOME_FAILED, code=code)
    assert await host.at_eof()
    with pytest.raises(ChannelUnavailable):  # and no later review can use it
        await channel.offer(launch_id=LAUNCH, review_id=RID2, kind="change_approval", candidate_digest=DIGEST2)


@pytest.mark.asyncio
async def test_a_good_frame_after_a_bad_one_in_the_same_read_is_never_honoured(pair):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(b"garbage\n" + _line(_decision(review["capability"])))
    assert (await waiting).kind == rc.OUTCOME_FAILED


@pytest.mark.asyncio
async def test_end_of_file_before_a_valid_decision_is_unavailable(pair):
    channel, host = pair
    await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision("0" * 64))[:25])  # half a frame ...
    host.sock.close()  # ... and the host goes away
    assert await waiting == ChannelOutcome(rc.OUTCOME_EOF)


@pytest.mark.asyncio
async def test_a_review_nobody_answers_expires(pair):
    channel, host = pair
    await _open(channel, host)
    assert await channel.await_decision(datetime.now(timezone.utc) + timedelta(milliseconds=300)) == ChannelOutcome(rc.OUTCOME_EXPIRED)


@pytest.mark.asyncio
async def test_a_valid_decision_that_arrives_after_the_expiry_is_refused_and_not_honoured():
    waiter_sock, host_sock = socket.socketpair()
    expires = datetime(2026, 10, 23, 12, 0, 0, tzinfo=timezone.utc)
    times = iter([expires - timedelta(seconds=5)] + [expires + timedelta(seconds=5)] * 20)
    channel = ReviewChannel(waiter_sock, clock=lambda: next(times))  # before the expiry when the wait starts, after it when the frame is judged
    host = Host(host_sock)
    try:
        review = await _open(channel, host)
        waiting = asyncio.ensure_future(channel.await_decision(expires))
        await host.send(_line(_decision(review["capability"])))
        assert await waiting == ChannelOutcome(rc.OUTCOME_EXPIRED)
        (result,) = await host.frames(1)
        assert result["code"] == "REVIEW_EXPIRED" and result["accepted"] is False
    finally:
        channel.close()
        host_sock.close()


@pytest.mark.asyncio
async def test_the_waiter_handles_frames_split_across_reads_and_several_frames_in_one_read(pair):
    channel, host = pair
    review = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    wrong, right = _line(_decision("1" * 64)), _line(_decision(review["capability"]))
    blob = wrong + right
    for start in range(0, len(blob), 7):  # seven bytes at a time, frames straddling the reads
        await host.send(blob[start:start + 7])
        await asyncio.sleep(0)
    assert await waiting == ChannelOutcome(rc.OUTCOME_DECIDED, decision="approve")
    first, second = await host.frames(2)
    assert (first["accepted"], first["code"], second["accepted"]) == (False, "CAPABILITY_MISMATCH", True)


# --------------------------------------------------------------------------- the capability

@pytest.mark.asyncio
async def test_the_capability_is_fresh_per_review_high_entropy_and_compared_in_constant_time(pair, monkeypatch):
    channel, host = pair
    seen = []
    real = rc.hmac.compare_digest
    monkeypatch.setattr(rc.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    first = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision("9" * 64)), _line(_decision(first["capability"])))
    await waiting
    assert len(seen) == 2 and all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in seen)
    await host.frames(2)
    second = await _open(channel, host, review_id=RID2, candidate_digest=DIGEST2)
    assert first["capability"] != second["capability"] and len(set(first["capability"])) > 8


@pytest.mark.asyncio
async def test_the_capability_never_reaches_a_repr_a_log_an_exception_or_the_channel_after_it_is_consumed(pair, caplog):
    channel, host = pair
    caplog.set_level(logging.DEBUG)
    review = await _open(channel, host)
    capability = review["capability"]
    assert capability not in repr(channel) and capability not in str(vars(channel).get("_parser", ""))
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(capability)))
    await waiting
    assert capability not in repr(vars(channel)) and capability not in caplog.text  # consumed: dropped with the open review
    with pytest.raises(ChannelProtocolError) as error:  # a rejected frame that CARRIES the capability must not echo it
        FrameParser().feed(_line({**_decision(capability), "extra": 1}))
    assert capability not in str(error.value) and capability not in repr(error.value)


# --------------------------------------------------------------------------- the descriptor

def test_the_inherited_descriptor_is_taken_over_and_made_non_inheritable_at_once():
    waiter_sock, host_sock = socket.socketpair()
    fd = os.dup(waiter_sock.fileno())
    os.set_inheritable(fd, True)  # exactly what the host's spawn hands the waiter (MEASURED: inheritable in the Python child)
    waiter_sock.close()
    channel = open_review_channel_from_fd(fd)
    try:
        assert os.get_inheritable(fd) is False
    finally:
        channel.close()
        host_sock.close()


def test_a_descriptor_that_is_not_a_socket_or_not_open_is_refused():
    read_end, write_end = os.pipe()
    try:
        with pytest.raises(ChannelUnavailable) as pipe_error:
            open_review_channel_from_fd(read_end)
        assert pipe_error.value.code == "CHANNEL_NOT_A_SOCKET"
    finally:
        os.close(read_end)
        os.close(write_end)
    closed = os.dup(0)
    os.close(closed)
    with pytest.raises(ChannelUnavailable) as closed_error:
        open_review_channel_from_fd(closed)
    assert closed_error.value.code == "CHANNEL_NOT_PROVIDED"


def test_the_dedicated_descriptor_is_three():
    assert CHANNEL_FD == 3


# --------------------------------------------------------------------------- a newer review supersedes the open one

@pytest.mark.asyncio
async def test_a_newer_review_supersedes_the_open_one_and_the_old_capability_dies_with_it(pair):
    channel, host = pair
    first = await _open(channel, host)
    old_wait = asyncio.ensure_future(channel.await_decision(_soon()))
    await asyncio.sleep(0.05)
    second = await _open(channel, host, review_id=RID2, candidate_digest=DIGEST2)  # offered while the first still waits: no error, the first is replaced
    assert second["capability"] != first["capability"]
    assert await asyncio.wait_for(old_wait, 5) == ChannelOutcome(rc.OUTCOME_EXPIRED)  # the superseded waiter ends expired, it never judges a frame again
    new_wait = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(first["capability"])))  # the OLD review's decision, with its own (dead) capability
    (stale,) = await host.frames(1)
    assert stale["code"] == "STALE_REVIEW" and not new_wait.done()
    await host.send(_line(_decision(second["capability"], review_id=RID2, digest=DIGEST2)))
    assert await new_wait == ChannelOutcome(rc.OUTCOME_DECIDED, decision="approve")


@pytest.mark.asyncio
async def test_an_abandoned_review_is_forgotten_so_the_channel_can_carry_the_next_one(pair):
    channel, host = pair
    first = await _open(channel, host)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await asyncio.sleep(0.05)
    waiting.cancel()
    channel.abandon()
    second = await _open(channel, host, review_id=RID2, candidate_digest=DIGEST2)
    waiting = asyncio.ensure_future(channel.await_decision(_soon()))
    await host.send(_line(_decision(first["capability"], review_id=RID2, digest=DIGEST2)))  # the abandoned review's capability on the new identity
    (refused,) = await host.frames(1)
    assert refused["code"] == "CAPABILITY_MISMATCH"
    assert second["review_id"] == RID2
