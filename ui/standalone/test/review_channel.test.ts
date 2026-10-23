/**
 * The host side of the private review channel (D2 M2 slice 2). The peer is a controlled socket standing in for the waiter (kriya/control/review_channel.py): it plays the
 * `review` / `result` frames and reads what the host writes. Pinned here: the capability is relayed and never exposed; every deviation from the protocol closes the channel
 * (fail closed) and ends a decision in flight with CHANNEL_CLOSED; partial and merged socket reads are handled; nothing assumes one data event is one frame.
 */
import { describe, expect, it } from 'vitest';
import { MAX_FRAME_BYTES, ReviewChannelHost, decisionAnswer, parseWaiterFrame, type ChannelDecisionRequest, type ChannelSocket } from '../src/main/review_channel';

const LAUNCH = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const REVIEW2 = 'ra-' + 'c3'.repeat(12);
const DIGEST = 'sha256:' + 'b2'.repeat(32);
const CAPABILITY = 'ef'.repeat(32);

class Peer implements ChannelSocket {
  written: string[] = [];
  destroyed = false;
  private handlers: { data: Array<(c: Buffer | string) => void>; end: Array<() => void>; close: Array<() => void>; error: Array<(e: Error) => void> } = { data: [], end: [], close: [], error: [] };
  on(event: 'data' | 'end' | 'close' | 'error', cb: never): unknown { (this.handlers[event] as Array<unknown>).push(cb); return this; }
  write(data: string): unknown { this.written.push(data); return true; }
  destroy(): unknown { this.destroyed = true; return this; }
  send(text: string | Buffer): void { for (const cb of this.handlers.data) cb(text); }
  eof(): void { for (const cb of this.handlers.end) cb(); for (const cb of this.handlers.close) cb(); }
  frames(): Array<Record<string, unknown>> { return this.written.join('').split('\n').filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>); }
}

const reviewFrame = (over: Record<string, unknown> = {}) => `${JSON.stringify({ v: 1, type: 'review', launch_id: LAUNCH, review_id: REVIEW, kind: 'change_approval', candidate_digest: DIGEST, capability: CAPABILITY, ...over })}\n`;
const resultFrame = (over: Record<string, unknown> = {}) => `${JSON.stringify({ v: 1, type: 'result', review_id: REVIEW, accepted: true, code: 'ACCEPTED', ...over })}\n`;
const request = (over: Partial<ChannelDecisionRequest> = {}): ChannelDecisionRequest => ({ launch_id: LAUNCH, review_id: REVIEW, kind: 'change_approval', candidate_digest: DIGEST, decision: 'approve', ...over });

function attached(deps: ConstructorParameters<typeof ReviewChannelHost>[0] = {}) {
  const logs: string[] = [];
  const host = new ReviewChannelHost({ log: (l) => logs.push(l), ...deps });
  const peer = new Peer();
  host.attach(LAUNCH, peer);
  return { host, peer, logs };
}

describe('the relay', () => {
  it('echoes the capability of the presented review with the decision, in exactly the protocol shape, and reports the waiter\'s result', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    const pending = host.decide(request({ decision: 'reject' }));
    expect(peer.frames()).toEqual([{ v: 1, type: 'decision', launch_id: LAUNCH, review_id: REVIEW, kind: 'change_approval', candidate_digest: DIGEST, decision: 'reject', capability: CAPABILITY }]);
    peer.send(resultFrame());
    expect(await pending).toEqual({ accepted: true, code: 'ACCEPTED' });
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false); // consumed: the capability is dropped
  });

  it('refuses without sending anything when no review has been presented, for an unknown launch, or for another review, digest or kind', async () => {
    const { host, peer } = attached();
    expect(await host.decide(request())).toEqual({ accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' });
    expect(await host.decide(request({ launch_id: '20261010T101010-00000000' }))).toEqual({ accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' });
    peer.send(reviewFrame());
    expect(await host.decide(request({ review_id: REVIEW2 }))).toEqual({ accepted: false, code: 'STALE_REVIEW' });
    expect(await host.decide(request({ candidate_digest: 'sha256:' + 'dd'.repeat(32) }))).toEqual({ accepted: false, code: 'STALE_REVIEW' });
    expect(await host.decide(request({ kind: 'runtime_command' }))).toEqual({ accepted: false, code: 'WRONG_REVIEW_KIND' });
    expect(peer.written).toEqual([]); // not one frame, so the capability never left the host
  });

  it('a refusal by the waiter keeps the review (the capability is not spent), so the person can decide again; an accept or an expiry ends it', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    const first = host.decide(request());
    peer.send(resultFrame({ accepted: false, code: 'CAPABILITY_MISMATCH' }));
    expect(await first).toEqual({ accepted: false, code: 'CAPABILITY_MISMATCH' });
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    const second = host.decide(request());
    peer.send(resultFrame({ accepted: false, code: 'REVIEW_EXPIRED' }));
    expect(await second).toEqual({ accepted: false, code: 'REVIEW_EXPIRED' });
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false);
  });

  it('serializes: a second decision while one awaits its result is refused and sends nothing', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    const first = host.decide(request());
    expect(await host.decide(request({ decision: 'reject' }))).toEqual({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    expect(peer.frames()).toHaveLength(1);
    peer.send(resultFrame());
    expect((await first).accepted).toBe(true);
  });

  it('a newer review replaces the pending one (the older one was abandoned by the waiter) and uses its own, fresh capability', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    peer.send(reviewFrame({ review_id: REVIEW2, capability: '12'.repeat(32) }));
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false);
    const pending = host.decide(request({ review_id: REVIEW2 }));
    expect(peer.frames()[0]).toMatchObject({ review_id: REVIEW2, capability: '12'.repeat(32) });
    peer.send(resultFrame({ review_id: REVIEW2 }));
    expect((await pending).accepted).toBe(true);
  });
});

describe('the framing', () => {
  it('buffers partial reads, yields every frame of one read, and handles multi-byte characters split across reads', async () => {
    const { host, peer } = attached();
    const wire = Buffer.from(reviewFrame());
    for (let i = 0; i < wire.length; i += 5) peer.send(wire.subarray(i, i + 5)); // five bytes at a time
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    const other = Buffer.from(reviewFrame({ review_id: REVIEW2 }) + reviewFrame({ review_id: REVIEW }));
    peer.send(other); // two frames in one read: the later one wins
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    expect(peer.destroyed).toBe(false);
  });

  it.each([
    ['malformed JSON', 'not json\n'],
    ['a non-object', '[1]\n'],
    ['an unknown version', reviewFrame({ v: 2 })],
    ['an unknown type', `${JSON.stringify({ v: 1, type: 'ping' })}\n`],
    ['an unknown field', reviewFrame({ extra: 1 })],
    ['a missing field', `${JSON.stringify({ v: 1, type: 'review', launch_id: LAUNCH, review_id: REVIEW, kind: 'change_approval', candidate_digest: DIGEST })}\n`],
    ['a malformed capability', reviewFrame({ capability: 'short' })],
    ['an unknown kind', reviewFrame({ kind: 'anything' })],
    ['a decision sent BY the waiter', `${JSON.stringify({ v: 1, type: 'decision', review_id: REVIEW })}\n`],
    ['a result for a review that is not the one awaited', resultFrame({ review_id: REVIEW2 })],
    ['a review for another launch', reviewFrame({ launch_id: '20261010T101010-00000000' })],
    ['a frame over the bound', 'x'.repeat(MAX_FRAME_BYTES + 1)],
    ['a frame over the bound with its newline', `${'x'.repeat(MAX_FRAME_BYTES + 1)}\n`],
  ])('%s closes the channel (fail closed) - judged on its own, with nothing in flight that could mask it', async (_name, bad) => {
    const { host, peer, logs } = attached();
    peer.send(reviewFrame()); // a healthy channel with a presented review ...
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    peer.send(bad); // ... then ONE deviation
    expect(peer.destroyed, 'the channel must be closed').toBe(true);
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false); // the capability is dropped with the channel
    expect(await host.decide(request())).toEqual({ accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' });
    expect(peer.written).toEqual([]);
    expect(logs.join('\n')).not.toContain(CAPABILITY); // log lines carry codes, never a frame
  });

  it.each([['malformed JSON', 'not json\n'], ['an unknown version', reviewFrame({ v: 2 })], ['a frame over the bound', 'x'.repeat(MAX_FRAME_BYTES + 1)],
    ['a result for a review that is not the one awaited', resultFrame({ review_id: REVIEW2 })]])(
    '%s while a decision awaits its result ends that decision CHANNEL_CLOSED (nothing is known to be recorded)', async (_name, bad) => {
      const { host, peer } = attached();
      peer.send(reviewFrame());
      const pending = host.decide(request());
      peer.send(bad);
      expect(await pending).toEqual({ accepted: false, code: 'CHANNEL_CLOSED' });
      expect(peer.destroyed).toBe(true);
    });

  it('a result when no decision is awaiting is out of order and closes the channel', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    peer.send(resultFrame());
    expect(peer.destroyed).toBe(true);
    expect(await host.decide(request())).toEqual({ accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' });
  });

  it('a review that arrives while a decision awaits its result is out of order', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    const pending = host.decide(request());
    peer.send(reviewFrame({ review_id: REVIEW2 }));
    expect(await pending).toEqual({ accepted: false, code: 'CHANNEL_CLOSED' });
    expect(peer.destroyed).toBe(true);
  });
});

describe('endings', () => {
  it('end of file ends a decision in flight and forgets the review', async () => {
    const { host, peer } = attached();
    peer.send(reviewFrame());
    const pending = host.decide(request());
    peer.eof();
    expect(await pending).toEqual({ accepted: false, code: 'CHANNEL_CLOSED' });
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false);
  });

  it('detach closes the channel; attaching again for the same launch replaces and closes the first', async () => {
    const { host, peer } = attached();
    const second = new Peer();
    host.attach(LAUNCH, second);
    expect(peer.destroyed).toBe(true);
    second.send(reviewFrame());
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(true);
    host.detach(LAUNCH);
    expect(second.destroyed).toBe(true);
    expect(host.hasReview(LAUNCH, REVIEW)).toBe(false);
  });

  it('a waiter that never answers ends CHANNEL_TIMEOUT without claiming anything was recorded', async () => {
    const { host, peer } = attached({ resultTimeoutMs: 20 });
    peer.send(reviewFrame());
    expect(await host.decide(request())).toEqual({ accepted: false, code: 'CHANNEL_TIMEOUT' });
    expect(decisionAnswer(request(), { accepted: false, code: 'CHANNEL_TIMEOUT' }, new Date(0)).message).toMatch(/nothing is known to be recorded/);
  });
});

describe('the capability', () => {
  it('appears only in the one decision frame: not in an answer, a log, an enumeration of the host, or a later frame', async () => {
    const { host, peer, logs } = attached({ resultTimeoutMs: 20 });
    peer.send(reviewFrame());
    const answer = await host.decide(request());
    const shown = decisionAnswer(request(), answer, new Date(0));
    expect(peer.written.filter((w) => w.includes(CAPABILITY))).toHaveLength(1);
    expect(JSON.stringify(answer) + JSON.stringify(shown) + logs.join('\n') + JSON.stringify(host) + JSON.stringify(Object.getOwnPropertyNames(host))).not.toContain(CAPABILITY);
  });

  it('the answer echoes identities only for an accepted decision', () => {
    expect(decisionAnswer(request(), { accepted: true, code: 'ACCEPTED' }, new Date('2026-10-23T12:00:00Z'))).toMatchObject({ accepted: true, launch_id: LAUNCH, review_id: REVIEW, decision: 'approve', decided_at: '2026-10-23T12:00:00.000Z' });
    expect(decisionAnswer(request(), { accepted: false, code: 'STALE_REVIEW' }, new Date())).toMatchObject({ accepted: false, launch_id: null, review_id: null, decision: null, decided_at: null });
  });

  it('parseWaiterFrame accepts exactly the two waiter-to-host shapes', () => {
    expect(parseWaiterFrame(reviewFrame().trim())).toMatchObject({ type: 'review', launch_id: LAUNCH });
    expect(parseWaiterFrame(resultFrame().trim())).toMatchObject({ type: 'result', review_id: REVIEW, accepted: true });
    expect(() => parseWaiterFrame(JSON.stringify({ v: true, type: 'result' }))).toThrow();
  });
});
