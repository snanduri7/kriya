/**
 * The verifier semantic review proxy and the per-kind decision vocabulary of the review channel (VERIFIER-SEMANTIC-REVIEW-001, owner ruling 2026-11-14). The proxy decides nothing - Kriya
 * validates, records and re-proves every binding - so these tests pin what it WILL and WILL NOT send, and the order: validate -> open workspace -> (for a word that carries authority)
 * re-prove the pending subject -> native confirmation -> Kriya. Controlled executors only.
 */
import { describe, expect, it } from 'vitest';
import {
  AUTHORITY_DECISIONS, SEMANTIC_CONFIRM_BUTTON, SEMANTIC_CONFIRM_COMMIT_INDEX, SEMANTIC_CONFIRM_MESSAGE, VERIFIER_SEMANTIC_KIND, VerifierSemanticHost, isSemanticCommitResponse,
  semanticConfirmationDialog, semanticReviewIsReviewable, semanticVerifierPaths, type VerifierSemanticDeps,
} from '../src/main/verifier_semantic_host';
import {
  BINARY_DECISIONS, DECISIONS_BY_KIND, KINDS, REVIEW_CHANNEL_VERSION, ReviewChannelHost, VERIFIER_SEMANTIC_DECISIONS, decisionAnswer, decisionValidForKind, parseWaiterFrame,
  type ChannelAnswer, type ChannelDecisionRequest, type ChannelSocket, type ReviewKind,
} from '../src/main/review_channel';
import { IPC_CHANNELS, validateDecideVerifierRepair, validateDecideVerifierSemantic } from '../src/main/ipc_contract';

const WS = '/Users/x/project';
const ID = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const FP = 'sha256:' + 'b2'.repeat(32);
const CAPABILITY = 'ef'.repeat(32);
const PATH = 'src/test/java/com/example/WidgetTest.java';
const pending = (over: Record<string, unknown> = {}) => ({
  kind: VERIFIER_SEMANTIC_KIND, review_id: REVIEW, candidate_digest: FP, semantic_fingerprint: FP, status: 'PENDING', verified: true,
  verifier_files: [{ path: PATH, sha256: 'a0'.repeat(32), source: 'class WidgetTest {}' }], contract_text: 'count() is the number of items', ...over,
});
const status = (over: Record<string, unknown> = {}) => ({ error: null, data: { launch: { pending_approval: pending(over) } } });
const good = (over: Record<string, unknown> = {}) => ({ workspace: WS, launch_id: ID, review_id: REVIEW, semantic_fingerprint: FP, decision: 'expectation_valid', ...over });
const ACCEPTED: ChannelAnswer = { accepted: true, code: 'ACCEPTED' };

function harness(over: Partial<VerifierSemanticDeps> = {}, answer: ChannelAnswer = ACCEPTED) {
  const calls: ChannelDecisionRequest[] = [];
  const order: string[] = [];
  const confirmedWith: Array<[string, readonly string[]]> = [];
  const deps: VerifierSemanticDeps = {
    channel: { decide: async (request) => { order.push('relay'); calls.push(request); return answer; } },
    prepare: () => ({ ok: true, executable: '/opt/kriya/.venv/bin/kriya', env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' } }),
    status: async () => { order.push('prove'); return status(); },
    confirm: async (decision, paths) => { order.push('confirm'); confirmedWith.push([decision, paths]); return true; },
    currentWorkspace: () => WS, ...over,
  };
  return { host: new VerifierSemanticHost(deps), calls, order, confirmedWith };
}

describe('decision validity is per review kind', () => {
  it('the fourth kind follows the three earlier ones, which keep exactly the binary words; the protocol version is unchanged', () => {
    expect([...KINDS]).toEqual(['change_approval', 'runtime_command', 'verifier_defect_repair_authorization', 'verifier_semantic_review']);
    expect(VERIFIER_SEMANTIC_KIND).toBe('verifier_semantic_review');
    expect(REVIEW_CHANNEL_VERSION).toBe(1);
    expect(Object.keys(DECISIONS_BY_KIND).sort()).toEqual([...KINDS].sort());
    for (const kind of KINDS.slice(0, 3)) expect([...DECISIONS_BY_KIND[kind]]).toEqual(['approve', 'reject']);
    expect([...DECISIONS_BY_KIND.verifier_semantic_review]).toEqual(['expectation_valid', 'expectation_invalid', 'cannot_decide']);
    for (const kind of KINDS) {
      for (const word of [...BINARY_DECISIONS, ...VERIFIER_SEMANTIC_DECISIONS, 'maybe', '']) {
        expect(decisionValidForKind(kind, word)).toBe((DECISIONS_BY_KIND[kind] as readonly string[]).includes(word));
      }
    }
  });
  it('the host accepts a review frame of the fourth kind (an older host, which does not list it, refuses that frame and the channel fails closed)', () => {
    const frame = JSON.stringify({ v: 1, type: 'review', launch_id: ID, review_id: REVIEW, kind: 'verifier_semantic_review', candidate_digest: FP, capability: CAPABILITY });
    expect(parseWaiterFrame(frame)).toMatchObject({ type: 'review', review: { kind: 'verifier_semantic_review' } });
    expect(() => parseWaiterFrame(frame.replace('verifier_semantic_review', 'a_kind_this_host_does_not_list'))).toThrow('INVALID_FIELDS');
  });
});

class Peer implements ChannelSocket {
  written: string[] = [];
  private handlers: { data: Array<(c: Buffer | string) => void>; end: Array<() => void>; close: Array<() => void>; error: Array<(e: Error) => void> } = { data: [], end: [], close: [], error: [] };
  on(event: 'data' | 'end' | 'close' | 'error', cb: never): unknown { (this.handlers[event] as Array<unknown>).push(cb); return this; }
  write(data: string): unknown { this.written.push(data); return true; }
  destroy(): unknown { return this; }
  send(text: string): void { for (const cb of this.handlers.data) cb(text); }
  frames(): Array<Record<string, unknown>> { return this.written.join('').split('\n').filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>); }
}
function channelWith(kind: ReviewKind) {
  const host = new ReviewChannelHost({});
  const peer = new Peer();
  host.attach(ID, peer);
  peer.send(`${JSON.stringify({ v: 1, type: 'review', launch_id: ID, review_id: REVIEW, kind, candidate_digest: FP, capability: CAPABILITY })}\n`);
  return { host, peer };
}
const relay = (kind: ReviewKind, decision: ChannelDecisionRequest['decision']): ChannelDecisionRequest => ({ launch_id: ID, review_id: REVIEW, kind, candidate_digest: FP, decision });

describe('the channel host never sends a word of another kind', () => {
  it.each(['approve', 'reject'] as const)('%s on a semantic review is refused before any frame is written, and the review stays presentable', async (word) => {
    const { host, peer } = channelWith('verifier_semantic_review');
    expect(await host.decide(relay('verifier_semantic_review', word))).toEqual({ accepted: false, code: 'WRONG_DECISION_FOR_REVIEW_KIND' });
    expect(peer.frames()).toEqual([]);
    expect(host.hasReview(ID, REVIEW)).toBe(true);
  });
  it.each(KINDS.slice(0, 3))('a semantic word on a %s review is refused before any frame is written', async (kind) => {
    for (const word of VERIFIER_SEMANTIC_DECISIONS) {
      const { host, peer } = channelWith(kind);
      expect(await host.decide(relay(kind, word))).toEqual({ accepted: false, code: 'WRONG_DECISION_FOR_REVIEW_KIND' });
      expect(peer.frames()).toEqual([]);
    }
  });
  it.each(VERIFIER_SEMANTIC_DECISIONS)('%s travels exactly, with the capability, in the protocol shape, and the waiter\'s result is reported', async (word) => {
    const { host, peer } = channelWith('verifier_semantic_review');
    const waiting = host.decide(relay('verifier_semantic_review', word));
    expect(peer.frames()).toEqual([{ v: 1, type: 'decision', launch_id: ID, review_id: REVIEW, kind: 'verifier_semantic_review', candidate_digest: FP, decision: word, capability: CAPABILITY }]);
    peer.send(`${JSON.stringify({ v: 1, type: 'result', review_id: REVIEW, accepted: true, code: 'ACCEPTED' })}\n`);
    expect(await waiting).toEqual({ accepted: true, code: 'ACCEPTED' });
  });
  it('the waiter\'s own typed refusal of a word is reported with its code and a message, and echoes no identity', () => {
    const answer = decisionAnswer(relay('verifier_semantic_review', 'cannot_decide'), { accepted: false, code: 'WRONG_DECISION_FOR_REVIEW_KIND' }, new Date(0));
    expect(answer).toMatchObject({ accepted: false, code: 'WRONG_DECISION_FOR_REVIEW_KIND', launch_id: null, review_id: null, decision: null });
    expect(answer.message).toContain('does not exist for this kind of review');
  });
});

describe('the request grammar: exactly five identities and one of the three words', () => {
  it('accepts each of the three words and has its own IPC channel', () => {
    for (const decision of VERIFIER_SEMANTIC_DECISIONS) expect(validateDecideVerifierSemantic(good({ decision }))).toEqual({ ok: true, value: good({ decision }) });
    expect(IPC_CHANNELS.decideVerifierSemantic).toBe('kriya:host:execution:decide-verifier-semantic');
    expect(new Set([IPC_CHANNELS.decideVerifierSemantic, IPC_CHANNELS.decideVerifierRepair, IPC_CHANNELS.decideRuntimeCommand, IPC_CHANNELS.decideApproval]).size).toBe(4);
  });
  it.each([
    ['approve', good({ decision: 'approve' })], ['reject', good({ decision: 'reject' })], ['another word', good({ decision: 'valid' })], ['an upper-case word', good({ decision: 'EXPECTATION_VALID' })],
    ['an extra field', good({ source: 'class X {}' })], ['the repair fingerprint field', { workspace: WS, launch_id: ID, review_id: REVIEW, authorization_fingerprint: FP, decision: 'expectation_valid' }],
    ['a missing field', { workspace: WS, launch_id: ID, review_id: REVIEW, decision: 'expectation_valid' }], ['a malformed fingerprint', good({ semantic_fingerprint: 'sha256:xyz' })],
    ['a malformed review id', good({ review_id: 'ra-1' })], ['a relative workspace', good({ workspace: 'project' })], ['not an object', 'expectation_valid'],
  ])('refuses %s', (_name, raw) => {
    expect(validateDecideVerifierSemantic(raw).ok).toBe(false);
  });
  it('the repair request still takes only approve / reject: no semantic word is valid there', () => {
    const repair = { workspace: WS, launch_id: ID, review_id: REVIEW, authorization_fingerprint: FP };
    expect(validateDecideVerifierRepair({ ...repair, decision: 'approve' }).ok).toBe(true);
    for (const decision of VERIFIER_SEMANTIC_DECISIONS) expect(validateDecideVerifierRepair({ ...repair, decision }).ok).toBe(false);
  });
});

describe('the proxy', () => {
  it.each(AUTHORITY_DECISIONS)('%s: re-proves the pending subject, then the native confirmation for THAT decision, then one relay of exactly that word', async (decision) => {
    const { host, calls, order, confirmedWith } = harness();
    const result = await host.decide(good({ decision }));
    expect(order).toEqual(['prove', 'confirm', 'relay']);
    expect(confirmedWith).toEqual([[decision, [PATH]]]);
    expect(calls).toEqual([{ launch_id: ID, review_id: REVIEW, kind: 'verifier_semantic_review', candidate_digest: FP, decision }]);
    expect(result).toMatchObject({ accepted: true, code: 'ACCEPTED', decision });
  });
  it('cannot_decide carries no authority: it is relayed without a proof round-trip or a confirmation, as its own word', async () => {
    const { host, calls, order } = harness({ status: async () => { throw new Error('must not be asked'); }, confirm: async () => { throw new Error('must not be shown'); } });
    const result = await host.decide(good({ decision: 'cannot_decide' }));
    expect(order).toEqual(['relay']);
    expect(calls[0]).toMatchObject({ kind: 'verifier_semantic_review', decision: 'cannot_decide' });
    expect(result).toMatchObject({ accepted: true, decision: 'cannot_decide' });
  });
  it('a native Cancel is NO decision: nothing is sent, and it is never turned into cannot_decide', async () => {
    for (const decision of AUTHORITY_DECISIONS) {
      const { host, calls } = harness({ confirm: async () => false });
      expect(await host.decide(good({ decision }))).toMatchObject({ accepted: false, code: 'CONFIRMATION_DECLINED', decision: null });
      expect(calls).toEqual([]);
    }
  });
  it.each([
    ['another kind', { kind: 'verifier_defect_repair_authorization' }], ['another review', { review_id: 'ra-' + 'ff'.repeat(12) }], ['another fingerprint', { candidate_digest: 'sha256:' + '0'.repeat(64) }],
    ['a fingerprint field that disagrees', { semantic_fingerprint: 'sha256:' + '0'.repeat(64) }], ['already decided', { status: 'DECIDED' }], ['expired', { status: 'EXPIRED' }],
    ['not verified by Kriya', { verified: false }], ['no verifier files', { verifier_files: [] }], ['a file without source', { verifier_files: [{ path: PATH, sha256: 'a0'.repeat(32), source: '' }] }],
    ['no contract text', { contract_text: '' }],
  ])('a pending subject Kriya cannot prove (%s) gets no confirmation and nothing is sent', async (_name, over) => {
    const { host, calls, order } = harness({ status: async () => status(over) });
    expect(await host.decide(good({ decision: 'expectation_invalid' }))).toMatchObject({ accepted: false, code: 'SEMANTIC_REVIEW_NOT_REVIEWABLE' });
    expect(order).toEqual([]);
    expect(calls).toEqual([]);
    expect(semanticReviewIsReviewable(status(over), REVIEW, FP)).toBe(false);
  });
  it('refuses before anything else: a bad request, another folder, a second request while one is in flight, a status that failed', async () => {
    expect(await harness().host.decide(good({ decision: 'approve' }))).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    expect(await harness({ currentWorkspace: () => '/other' }).host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(await harness({ prepare: () => null }).host.decide(good())).toMatchObject({ accepted: false, code: 'EXECUTION_UNAVAILABLE' });
    expect(await harness({ status: async () => { throw new Error('down'); } }).host.decide(good())).toMatchObject({ accepted: false, code: 'SEMANTIC_REVIEW_NOT_REVIEWABLE' });
    let release: (v: boolean) => void = () => undefined;
    const slow = harness({ confirm: () => new Promise<boolean>((resolve) => { release = resolve; }) });
    const first = slow.host.decide(good());
    await Promise.resolve(); await Promise.resolve();
    expect(await slow.host.decide(good({ decision: 'cannot_decide' }))).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    release(true);
    expect(await first).toMatchObject({ accepted: true });
    expect(slow.calls).toHaveLength(1);
  });
  it('the folder changing while the confirmation is up sends nothing', async () => {
    let open = WS;
    const { host, calls } = harness({ currentWorkspace: () => open, confirm: async () => { open = '/other'; return true; } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(calls).toEqual([]);
  });
  it('a refusal by Kriya is reported with its code', async () => {
    const { host } = harness({}, { accepted: false, code: 'STALE_REVIEW' });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'STALE_REVIEW', decision: null });
  });
});

describe('the native confirmation says which decision it records, in words', () => {
  it('Cancel is first, the default and the Escape action; the commit button names the decision and is never Approve / Reject', () => {
    for (const decision of ['expectation_valid', 'expectation_invalid'] as const) {
      const dialog = semanticConfirmationDialog(decision, [PATH]);
      expect(dialog.message).toBe(SEMANTIC_CONFIRM_MESSAGE);
      expect(dialog.buttons).toEqual(['Cancel', SEMANTIC_CONFIRM_BUTTON[decision]]);
      expect([dialog.defaultId, dialog.cancelId]).toEqual([0, 0]);
      expect(dialog.buttons.join(' ')).not.toMatch(/approve|reject/i);
      expect(dialog.detail).toContain(PATH);
    }
    expect(SEMANTIC_CONFIRM_BUTTON.expectation_valid).toContain('Valid');
    expect(SEMANTIC_CONFIRM_BUTTON.expectation_invalid).toContain('Invalid');
    expect(semanticConfirmationDialog('expectation_valid').detail).toContain('authorizes no change');
    expect(semanticConfirmationDialog('expectation_invalid').detail).toContain('ONE regeneration');
    expect(isSemanticCommitResponse(SEMANTIC_CONFIRM_COMMIT_INDEX)).toBe(true);
    expect(isSemanticCommitResponse(0)).toBe(false);
    expect(semanticVerifierPaths(status())).toEqual([PATH]);
  });
});
