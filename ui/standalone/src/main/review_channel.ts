/**
 * Review channel, host side (D2 M2). Pure: no Electron import, no `child_process` import - the socket is the execution child's dedicated extra stdio entry
 * (`stdio[3]`, a bidirectional socket), injected, so the rules are tested with a controlled peer. The waiter's side is kriya/control/review_channel.py;
 * the two share one protocol: three newline-delimited JSON frames, a version and the review id on every frame, a bounded frame size.
 *
 *   waiter -> host  {"v":1,"type":"review","launch_id","review_id","kind","candidate_digest","capability"}
 *   host -> waiter  {"v":1,"type":"decision","launch_id","review_id","kind","candidate_digest","decision","capability"}
 *   waiter -> host  {"v":1,"type":"result","review_id","accepted","code"}
 *
 * What this module IS: the relay of ONE capability. It holds the capability the waiter drew for the pending review (in a private field, never in a
 * log, a return value or a rendered state), and echoes it back with the person's decision. The renderer never sees it and cannot supply one.
 *
 * What it is NOT: an authority. The waiter validates everything (identity, kind, digest, capability, expiry, one-shot) and answers with a `result`.
 * Anything that is not exactly the protocol - malformed JSON, an unknown version/type/field, a frame over the bound, an unsolicited `result`, a
 * `decision` from the waiter, a `review` while a decision is awaiting its result - closes the channel (fail closed), and any decision in flight
 * ends with `CHANNEL_CLOSED`: nothing is known to be recorded.
 */
import { CANDIDATE_DIGEST_RE, LAUNCH_ID_RE, REVIEW_ID_RE } from './ipc_contract';

export const REVIEW_CHANNEL_VERSION = 1;
export const MAX_FRAME_BYTES = 16 * 1024;
const CAPABILITY_RE = /^[0-9a-f]{64}$/;
const KINDS = ['change_approval', 'runtime_command'] as const;
export type ReviewKind = (typeof KINDS)[number];

/** The slice of a Node socket the channel uses (net.Socket satisfies it; a test peer can too). */
export interface ChannelSocket {
  on(event: 'data', cb: (chunk: Buffer | string) => void): unknown;
  on(event: 'end' | 'close', cb: () => void): unknown;
  on(event: 'error', cb: (error: Error) => void): unknown;
  write(data: string): unknown;
  destroy(): unknown;
}

export interface ChannelDecisionRequest { launch_id: string; review_id: string; kind: ReviewKind; candidate_digest: string; decision: 'approve' | 'reject' }
export interface ChannelAnswer { accepted: boolean; code: string }

export interface ReviewChannelDeps {
  /** How long a decision waits for the waiter's `result` before the host reports that nothing is known to be recorded. */
  resultTimeoutMs?: number;
  /** Developer-log lines: codes only, never a frame. */
  log?(line: string): void;
}

interface PendingReview { review_id: string; kind: ReviewKind; candidate_digest: string; capability: string }
interface Awaiting { review_id: string; settle(answer: ChannelAnswer): void }
interface Channel { socket: ChannelSocket; buffer: string; review: PendingReview | null; awaiting: Awaiting | null; closed: boolean }

class FrameError extends Error {}

function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }
const hasExactly = (v: Record<string, unknown>, keys: readonly string[]): boolean => Object.keys(v).length === keys.length && keys.every((k) => k in v);

/** One incoming frame (a line without its newline), validated against the protocol; anything else is a FrameError. */
export function parseWaiterFrame(line: string): { type: 'review'; launch_id: string; review: PendingReview } | { type: 'result'; review_id: string; accepted: boolean; code: string } {
  let value: unknown;
  try { value = JSON.parse(line); } catch { throw new FrameError('MALFORMED_JSON'); }
  if (!isObject(value)) throw new FrameError('NOT_AN_OBJECT');
  if (value.v !== REVIEW_CHANNEL_VERSION) throw new FrameError('UNKNOWN_VERSION');
  if (value.type === 'review') {
    if (!hasExactly(value, ['v', 'type', 'launch_id', 'review_id', 'kind', 'candidate_digest', 'capability'])) throw new FrameError('INVALID_FIELDS');
    const { launch_id: launch, review_id: review, kind, candidate_digest: digest, capability } = value;
    if (typeof launch !== 'string' || !LAUNCH_ID_RE.test(launch) || typeof review !== 'string' || !REVIEW_ID_RE.test(review) || typeof digest !== 'string' || !CANDIDATE_DIGEST_RE.test(digest)
      || !KINDS.includes(kind as ReviewKind) || typeof capability !== 'string' || !CAPABILITY_RE.test(capability)) throw new FrameError('INVALID_FIELDS');
    return { type: 'review', launch_id: launch, review: { review_id: review, kind: kind as ReviewKind, candidate_digest: digest, capability } };
  }
  if (value.type === 'result') {
    if (!hasExactly(value, ['v', 'type', 'review_id', 'accepted', 'code'])) throw new FrameError('INVALID_FIELDS');
    if (typeof value.review_id !== 'string' || !REVIEW_ID_RE.test(value.review_id) || typeof value.accepted !== 'boolean' || typeof value.code !== 'string' || value.code.length === 0 || value.code.length > 64) throw new FrameError('INVALID_FIELDS');
    return { type: 'result', review_id: value.review_id, accepted: value.accepted, code: value.code };
  }
  if (value.type === 'decision') throw new FrameError('WRONG_DIRECTION');
  throw new FrameError('UNKNOWN_TYPE');
}

export class ReviewChannelHost {
  readonly #channels = new Map<string, Channel>();
  readonly #timeoutMs: number;
  readonly #log: ((line: string) => void) | undefined;

  constructor(deps: ReviewChannelDeps = {}) {
    this.#timeoutMs = deps.resultTimeoutMs ?? 30_000;
    this.#log = deps.log;
  }

  /** Takes the execution child's channel socket for its launch. A second attach for the same launch replaces (and closes) the first. */
  attach(launchId: string, socket: ChannelSocket): void {
    if (!LAUNCH_ID_RE.test(launchId)) throw new Error('launch id outside the grammar');
    const previous = this.#channels.get(launchId);
    if (previous) this.#close(launchId, previous, 'REPLACED');
    const channel: Channel = { socket, buffer: '', review: null, awaiting: null, closed: false };
    this.#channels.set(launchId, channel);
    socket.on('data', (chunk) => this.#onData(launchId, channel, typeof chunk === 'string' ? chunk : chunk.toString('utf8')));
    socket.on('end', () => this.#close(launchId, channel, 'EOF'));
    socket.on('close', () => this.#close(launchId, channel, 'EOF'));
    socket.on('error', () => this.#close(launchId, channel, 'SOCKET_ERROR'));
  }

  detach(launchId: string): void {
    const channel = this.#channels.get(launchId);
    if (channel) this.#close(launchId, channel, 'DETACHED');
  }

  /** True when the waiter has presented the review and a decision for exactly it can be relayed (no capability is exposed). */
  hasReview(launchId: string, reviewId: string): boolean {
    return this.#channels.get(launchId)?.review?.review_id === reviewId;
  }

  async decide(request: ChannelDecisionRequest): Promise<ChannelAnswer> {
    const channel = this.#channels.get(request.launch_id);
    if (!channel || channel.closed) return { accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' };
    const review = channel.review;
    if (!review) return { accepted: false, code: 'REVIEW_CHANNEL_NOT_READY' };
    if (review.review_id !== request.review_id || review.candidate_digest !== request.candidate_digest) return { accepted: false, code: 'STALE_REVIEW' };
    if (review.kind !== request.kind) return { accepted: false, code: 'WRONG_REVIEW_KIND' };
    if (channel.awaiting) return { accepted: false, code: 'DECISION_IN_FLIGHT' };
    return new Promise<ChannelAnswer>((resolve) => {
      let timer: NodeJS.Timeout | undefined;
      const settle = (answer: ChannelAnswer) => {
        if (timer) clearTimeout(timer);
        if (channel.awaiting?.review_id === request.review_id) channel.awaiting = null;
        resolve(answer);
      };
      channel.awaiting = { review_id: request.review_id, settle };
      timer = setTimeout(() => settle({ accepted: false, code: 'CHANNEL_TIMEOUT' }), this.#timeoutMs);
      try {
        // the capability leaves this field only here, inside the frame to the waiter
        channel.socket.write(`${JSON.stringify({ v: REVIEW_CHANNEL_VERSION, type: 'decision', launch_id: request.launch_id, review_id: request.review_id, kind: request.kind,
          candidate_digest: request.candidate_digest, decision: request.decision, capability: review.capability })}\n`);
      } catch {
        this.#close(request.launch_id, channel, 'WRITE_FAILED');
      }
    });
  }

  #onData(launchId: string, channel: Channel, text: string): void {
    if (channel.closed) return;
    channel.buffer += text;
    for (;;) {
      const end = channel.buffer.indexOf('\n');
      if (end < 0) {
        if (Buffer.byteLength(channel.buffer, 'utf8') > MAX_FRAME_BYTES) return this.#close(launchId, channel, 'FRAME_TOO_LARGE');
        return;
      }
      const line = channel.buffer.slice(0, end);
      channel.buffer = channel.buffer.slice(end + 1);
      if (Buffer.byteLength(line, 'utf8') > MAX_FRAME_BYTES) return this.#close(launchId, channel, 'FRAME_TOO_LARGE');
      try {
        this.#handle(launchId, channel, parseWaiterFrame(line));
      } catch (error) {
        return this.#close(launchId, channel, error instanceof FrameError ? error.message : 'INVALID_FIELDS');
      }
      if (channel.closed) return;
    }
  }

  #handle(launchId: string, channel: Channel, frame: ReturnType<typeof parseWaiterFrame>): void {
    if (frame.type === 'review') {
      if (frame.launch_id !== launchId || channel.awaiting) throw new FrameError('OUT_OF_ORDER'); // another launch's review, or a review before the result of the awaited decision
      channel.review = frame.review;
      return;
    }
    const awaiting = channel.awaiting;
    if (!awaiting || awaiting.review_id !== frame.review_id) throw new FrameError('OUT_OF_ORDER'); // a result nobody asked for
    if (frame.accepted || frame.code === 'REVIEW_EXPIRED') channel.review = null; // consumed (or over): the capability is dropped
    awaiting.settle({ accepted: frame.accepted, code: frame.code });
  }

  #close(launchId: string, channel: Channel, reason: string): void {
    if (channel.closed) return;
    channel.closed = true;
    channel.review = null;
    if (this.#channels.get(launchId) === channel) this.#channels.delete(launchId);
    try { channel.socket.destroy(); } catch { /* already gone */ }
    if (reason !== 'DETACHED' && reason !== 'EOF' && reason !== 'REPLACED') this.#log?.(`review channel ${launchId} closed: ${reason}`);
    channel.awaiting?.settle({ accepted: false, code: 'CHANNEL_CLOSED' });
  }
}

/** What the decision hosts return to the renderer (the shape DecideApprovalResult / DecideRuntimeCommandResult already have). */
export interface DecisionAnswer { accepted: boolean; code: string; message: string; launch_id: string | null; review_id: string | null; decision: string | null; decided_at: string | null }

const MESSAGES: Readonly<Record<string, string>> = {
  ACCEPTED: 'recorded; the waiting run records it and continues',
  REVIEW_CHANNEL_NOT_READY: 'Kriya has not presented this review on its private channel (yet), so nothing was sent. Wait a moment and try again.',
  STALE_REVIEW: 'the change under review is no longer the one you were shown; review it again',
  WRONG_REVIEW_KIND: 'the review waiting is not of this kind; nothing was recorded',
  REVIEW_EXPIRED: 'this review expired; nothing was applied',
  CAPABILITY_MISMATCH: 'Kriya did not accept the decision (it did not match the live review); nothing was recorded',
  DECISION_IN_FLIGHT: 'a decision is already being recorded',
  CHANNEL_CLOSED: 'the connection to the running Kriya process closed; nothing is known to be recorded - check the run before deciding again',
  CHANNEL_TIMEOUT: 'Kriya did not answer the decision in time; nothing is known to be recorded - check the run before deciding again',
};

/** The renderer-facing answer for one relayed decision. Identities are echoed only for an ACCEPTED decision (Kriya's `result` is the only proof). */
export function decisionAnswer(request: ChannelDecisionRequest, answer: ChannelAnswer, now: Date): DecisionAnswer {
  const message = MESSAGES[answer.code] ?? (answer.accepted ? 'recorded' : 'Kriya refused the decision');
  return answer.accepted
    ? { accepted: true, code: answer.code, message, launch_id: request.launch_id, review_id: request.review_id, decision: request.decision, decided_at: now.toISOString() }
    : { accepted: false, code: answer.code, message, launch_id: null, review_id: null, decision: null, decided_at: null };
}
