/**
 * The verifier-defect repair authorization proxy (owner rulings 2026-11-13): a narrow, typed forwarder with a NATIVE confirmation. It decides nothing - Kriya validates, records and
 * re-proves every binding before any repair - so these tests pin what it WILL and WILL NOT send, and the order: validate -> open workspace -> re-prove the pending subject against
 * Kriya's own launch record -> native confirmation -> Kriya. Controlled executors only.
 */
import { describe, expect, it } from 'vitest';
import {
  CONFIRM_AUTHORIZE_INDEX, CONFIRM_BUTTONS, CONFIRM_MESSAGE, DIALOG_FILES_MAX, VERIFIER_REPAIR_KIND, VerifierRepairHost, authorizationIsReviewable, confirmationDialog, dialogLine,
  isAuthorizeResponse, reviewedVerifierPaths, type VerifierRepairDeps,
} from '../src/main/verifier_repair_host';
import { KINDS, type ChannelAnswer, type ChannelDecisionRequest } from '../src/main/review_channel';
import { IPC_CHANNELS, validateDecideRuntimeCommand, validateDecideVerifierRepair } from '../src/main/ipc_contract';

const WS = '/Users/x/project';
const ID = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const FP = 'sha256:' + 'b2'.repeat(32);
const PATH = 'src/test/java/com/example/ProtocolParserTest.java';
const pending = (over: Record<string, unknown> = {}) => ({
  kind: VERIFIER_REPAIR_KIND, review_id: REVIEW, candidate_digest: FP, authorization_fingerprint: FP, status: 'PENDING', verified: true,
  verifier_files: [{ path: PATH, sha256: 'a0'.repeat(32), source: 'class ProtocolParserTest {}' }], contract_text: 'dataLength is the byte length of body', ...over,
});
const status = (over: Record<string, unknown> = {}) => ({ error: null, data: { launch: { pending_approval: pending(over) } } });
const good = (over: Record<string, unknown> = {}) => ({ workspace: WS, launch_id: ID, review_id: REVIEW, authorization_fingerprint: FP, decision: 'approve', ...over });
const ACCEPTED: ChannelAnswer = { accepted: true, code: 'ACCEPTED' };

function harness(over: Partial<VerifierRepairDeps> = {}, answer: ChannelAnswer | (() => Promise<ChannelAnswer>) = ACCEPTED) {
  const calls: ChannelDecisionRequest[] = [];
  const order: string[] = [];
  const confirmedWith: Array<readonly string[]> = [];
  const deps: VerifierRepairDeps = {
    channel: { decide: async (request) => { order.push('relay'); calls.push(request); return typeof answer === 'function' ? answer() : answer; } },
    prepare: () => ({ ok: true, executable: '/opt/kriya/.venv/bin/kriya', env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' } }),
    status: async () => { order.push('prove'); return status(); },
    confirm: async (paths) => { order.push('confirm'); confirmedWith.push(paths); return true; },
    currentWorkspace: () => WS, ...over,
  };
  return { host: new VerifierRepairHost(deps), calls, order, confirmedWith };
}

describe('the third review kind is a subject of its own', () => {
  it('is in the channel table next to the two existing kinds, which are unchanged', () => {
    // VERIFIER-SEMANTIC-REVIEW-001 added a fourth kind after these (verifier_semantic_host.test.ts pins the whole table). OLD pinned exactly three; NEW INVARIANT: the three
    // earlier kinds keep their names and order; the old pin was a count tripwire.
    expect([...KINDS].slice(0, 3)).toEqual(['change_approval', 'runtime_command', 'verifier_defect_repair_authorization']);
    expect(VERIFIER_REPAIR_KIND).toBe('verifier_defect_repair_authorization');
  });
  it('has its own IPC channel, distinct from the change review and the runtime-command review', () => {
    expect(IPC_CHANNELS.decideVerifierRepair).toBe('kriya:host:execution:decide-verifier-repair');
    expect(new Set([IPC_CHANNELS.decideVerifierRepair, IPC_CHANNELS.decideRuntimeCommand, IPC_CHANNELS.decideApproval]).size).toBe(3);
    expect(IPC_CHANNELS.decideRuntimeCommand).toBe('kriya:host:execution:decide-runtime');
    expect(IPC_CHANNELS.decideApproval).toBe('kriya:host:execution:decide');
  });
});

describe('the request grammar: exactly five identities, nothing a renderer could put a file, a text or a contract in', () => {
  it('accepts the exact shape', () => {
    expect(validateDecideVerifierRepair(good())).toEqual({ ok: true, value: good() });
    expect(validateDecideVerifierRepair(good({ decision: 'reject' }))).toMatchObject({ ok: true });
  });
  it.each([
    ['an extra field', good({ source: 'class X {}' })],
    ['a missing field', (() => { const { decision: _d, ...rest } = good(); return rest; })()],
    ['the runtime field name', { workspace: WS, launch_id: ID, review_id: REVIEW, command_fingerprint: FP, decision: 'approve' }],
    ['a malformed fingerprint', good({ authorization_fingerprint: 'b2'.repeat(32) })],
    ['a malformed review id', good({ review_id: 'review-1' })],
    ['a malformed launch id', good({ launch_id: 'latest' })],
    ['a relative workspace', good({ workspace: 'project' })],
    ['another decision word', good({ decision: 'authorize' })],
    ['not an object', 'approve'],
  ])('refuses %s', (_name, raw) => { expect(validateDecideVerifierRepair(raw).ok).toBe(false); });
  it('the runtime-command grammar is unchanged and does not accept this request', () => {
    expect(validateDecideRuntimeCommand(good()).ok).toBe(false);
    expect(validateDecideRuntimeCommand({ workspace: WS, launch_id: ID, review_id: REVIEW, command_fingerprint: FP, decision: 'approve' }).ok).toBe(true);
  });
});

describe('the native confirmation: the fixed question plus only what Kriya proved', () => {
  it('asks the owner-approved question, Cancel first/default/escape, and only the explicit button commits', () => {
    const d = confirmationDialog();
    expect(d.title).toBe('Authorize Verifier Repair');
    expect(d.message).toBe('Does this verifier expectation conflict with the canonical contract, authorizing one verifier repair?');
    expect(CONFIRM_MESSAGE).toBe(d.message);
    expect(d.detail).toContain('Kriya never repairs a verifier on its own.');
    expect(d.detail).toContain('ONE repair');
    expect(d.detail).toContain('separate source approval');
    expect(d.buttons).toEqual(['Cancel', 'Authorize One Repair']);
    expect([d.defaultId, d.cancelId]).toEqual([0, 0]);
    expect(CONFIRM_BUTTONS[CONFIRM_AUTHORIZE_INDEX]).toBe('Authorize One Repair');
    expect(isAuthorizeResponse(1)).toBe(true);
    for (const r of [0, 2, -1]) expect(isAuthorizeResponse(r)).toBe(false);
  });
  it('lists up to three recorded paths, strips control/bidi characters, bounds long lines and counts the rest', () => {
    const d = confirmationDialog(['a/ATest.java', 'b/B‮Test\u0000.java', 'c/CTest.java', 'd', 'e']);
    expect(d.detail.split('\n').slice(-5)).toEqual(['', '1. a/ATest.java', '2. b/B Test .java', '3. c/CTest.java', '…and 2 more']);
    expect(d.detail).not.toMatch(/‮|\u0000/);
    expect(dialogLine('x'.repeat(500)).length).toBe(120);
    expect(DIALOG_FILES_MAX).toBe(3);
  });
});

describe('what counts as a proved authorization review', () => {
  it('accepts only this review, of this kind, pending, with this fingerprint, verified by Kriya, with its verifier source and contract', () => {
    expect(authorizationIsReviewable(status(), REVIEW, FP)).toBe(true);
    expect(reviewedVerifierPaths(status())).toEqual([PATH]);
  });
  it.each([
    ['not verified by Kriya', { verified: false }],
    ['verified missing', { verified: undefined }],
    ['a runtime-command review', { kind: 'runtime_command' }],
    ['a change review', { kind: 'change_approval' }],
    ['another review id', { review_id: 'ra-' + 'c3'.repeat(12) }],
    ['another fingerprint', { candidate_digest: 'sha256:' + 'dd'.repeat(32) }],
    ['a fingerprint that is not the candidate digest', { authorization_fingerprint: 'sha256:' + 'dd'.repeat(32) }],
    ['already approved', { status: 'APPROVED' }],
    ['expired', { status: 'EXPIRED' }],
    ['no verifier file', { verifier_files: [] }],
    ['a verifier file without source', { verifier_files: [{ path: PATH, sha256: 'a0'.repeat(32), source: '' }] }],
    ['no contract text', { contract_text: '' }],
  ])('refuses %s', (_name, over) => { expect(authorizationIsReviewable(status(over), REVIEW, FP)).toBe(false); });
  it('refuses an error envelope, a launch with nothing pending and anything that is not an envelope', () => {
    expect(authorizationIsReviewable({ error: { code: 'LAUNCH_NOT_FOUND' }, data: null }, REVIEW, FP)).toBe(false);
    expect(authorizationIsReviewable({ error: null, data: { launch: { pending_approval: null } } }, REVIEW, FP)).toBe(false);
    for (const junk of [null, undefined, 'x', 3, [], {}]) expect(authorizationIsReviewable(junk, REVIEW, FP)).toBe(false);
    expect(reviewedVerifierPaths(null)).toEqual([]);
  });
});

describe('an APPROVE: prove, then the native confirmation, then exactly one typed relay', () => {
  it('re-proves the subject through Kriya, confirms with the paths KRIYA recorded, and relays the third kind with the fingerprint', async () => {
    const { host, calls, order, confirmedWith } = harness();
    const answer = await host.decide(good());
    expect(order).toEqual(['prove', 'confirm', 'relay']);
    expect(confirmedWith).toEqual([[PATH]]);
    expect(calls).toEqual([{ launch_id: ID, review_id: REVIEW, kind: 'verifier_defect_repair_authorization', candidate_digest: FP, decision: 'approve' }]);
    expect(answer).toMatchObject({ accepted: true, code: 'ACCEPTED', launch_id: ID, review_id: REVIEW, decision: 'approve' });
  });
  it('a stale or mismatched fingerprint cannot authorize: nothing is confirmed and nothing is sent', async () => {
    const other = 'sha256:' + 'dd'.repeat(32);
    const { host, calls, order } = harness();
    expect(await host.decide(good({ authorization_fingerprint: other }))).toMatchObject({ accepted: false, code: 'AUTHORIZATION_NOT_REVIEWABLE' });
    expect(order).toEqual(['prove']);
    expect(calls).toEqual([]);
  });
  it.each([
    ['Kriya did not verify the recorded evidence', async () => status({ verified: false })],
    ['the pending review is of another kind', async () => status({ kind: 'runtime_command' })],
    ['the review is no longer pending', async () => status({ status: 'EXPIRED' })],
    ['Kriya answers with an error', async () => ({ error: { code: 'LAUNCH_NOT_FOUND' }, data: null })],
    ['the query fails', async () => { throw new Error('kriya went away'); }],
  ])('sends nothing and shows no confirmation when %s', async (_name, statusFn) => {
    const { host, calls, order } = harness({ status: statusFn as VerifierRepairDeps['status'] });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'AUTHORIZATION_NOT_REVIEWABLE' });
    expect(order.includes('confirm')).toBe(false);
    expect(calls).toEqual([]);
  });
  it('native CANCEL is no decision: nothing is sent and the answer says the request is still waiting', async () => {
    const { host, calls, order } = harness({ confirm: async () => false });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'CONFIRMATION_DECLINED' });
    expect(order).toEqual(['prove']);
    expect(calls).toEqual([]);
  });
  it('a confirmation that cannot be shown sends nothing', async () => {
    const { host, calls } = harness({ confirm: async () => { throw new Error('no window'); } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
    expect(calls).toEqual([]);
  });
  it('sends nothing when the open folder changed while the confirmation was shown', async () => {
    let open = WS;
    const { host, calls } = harness({ currentWorkspace: () => open, confirm: async () => { open = '/Users/x/other'; return true; } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(calls).toEqual([]);
  });
  it('passes Kriya\'s refusal through (a decision that no longer matches the live review authorizes nothing)', async () => {
    for (const code of ['STALE_REVIEW', 'WRONG_REVIEW_KIND', 'REVIEW_EXPIRED', 'CAPABILITY_MISMATCH']) {
      const { host } = harness({}, { accepted: false, code });
      expect(await host.decide(good())).toMatchObject({ accepted: false, code });
    }
  });
});

describe('a REJECT is the explicit "no": one relay, no proof needed, no confirmation', () => {
  it('relays the rejection with the third kind', async () => {
    const { host, calls, order } = harness();
    expect(await host.decide(good({ decision: 'reject' }))).toMatchObject({ accepted: true, decision: 'reject' });
    expect(order).toEqual(['relay']);
    expect(calls[0]).toMatchObject({ kind: 'verifier_defect_repair_authorization', decision: 'reject', candidate_digest: FP });
  });
});

describe('what is refused before anything is asked', () => {
  it('a malformed request, a folder that is not open, a build without a backend', async () => {
    expect(await harness().host.decide({ ...good(), extra: 1 })).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    expect(await harness({ currentWorkspace: () => '/Users/x/other' }).host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(await harness({ prepare: () => null }).host.decide(good())).toMatchObject({ accepted: false, code: 'EXECUTION_UNAVAILABLE' });
    expect(await harness({ prepare: () => ({ ok: false, message: 'no venv' }) as never }).host.decide(good())).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
  });
  it('a second decision while one is in flight', async () => {
    let release: (answer: ChannelAnswer) => void = () => undefined;
    const { host } = harness({}, () => new Promise<ChannelAnswer>((resolve) => { release = resolve; }));
    const first = host.decide(good({ decision: 'reject' }));
    await Promise.resolve();
    expect(await host.decide(good({ decision: 'reject' }))).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    release(ACCEPTED);
    expect(await first).toMatchObject({ accepted: true });
  });
});
