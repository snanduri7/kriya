/** The review-decision proxy (GUI final, D2 M2): a narrow, typed relay. It decides nothing - Kriya validates and records - so these tests pin what it WILL and WILL NOT send over the run's private review channel. */
import { describe, expect, it } from 'vitest';
import { ApprovalHost, changeIsReviewable, type ApprovalDeps } from '../src/main/approval_host';
import type { ChannelAnswer, ChannelDecisionRequest } from '../src/main/review_channel';
import { buildLaunchDecisionArgv, isLaunchDecisionArgv, argvIsAllowed } from '../src/main/kriya_argv';

const WS = '/Users/x/project';
const ID = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const DIGEST = 'sha256:' + 'b2'.repeat(32);
const REVIEWABLE = { error: null, data: { verified: true, review_id: REVIEW, candidate_digest: DIGEST, status: 'PENDING', truncated: false, files: [{ path: 'a.py', truncated: false }] } };
const good = (over: Record<string, unknown> = {}) => ({ workspace: WS, launch_id: ID, review_id: REVIEW, candidate_digest: DIGEST, decision: 'approve', ...over });
const ACCEPTED: ChannelAnswer = { accepted: true, code: 'ACCEPTED' };
const NOW = new Date('2026-10-23T12:00:00.000Z');

function harness(answer: ChannelAnswer | (() => Promise<ChannelAnswer>) = ACCEPTED, over: Partial<ApprovalDeps> = {}) {
  const calls: ChannelDecisionRequest[] = [];
  const deps: ApprovalDeps = {
    channel: { decide: async (request) => { calls.push(request); return typeof answer === 'function' ? answer() : answer; } }, now: () => NOW,
    prepare: () => ({ ok: true, executable: '/opt/kriya/.venv/bin/kriya', env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' } }),
    reviewable: async () => REVIEWABLE, currentWorkspace: () => WS, ...over,
  };
  return { host: new ApprovalHost(deps), calls };
}

describe('the decision command line (retired in D2 M2 slice 4 with the CLI action)', () => {
  it('has exactly one shape and nothing that could approve more than the one review', () => {
    const argv = buildLaunchDecisionArgv({ workspace: WS, launch_id: ID, review_id: REVIEW, candidate_digest: DIGEST, decision: 'reject' });
    expect(argv).toEqual(['launch-decision', '--launch-id', ID, '--review-id', REVIEW, '--candidate-digest', DIGEST, '--decision', 'reject']);
    expect(argv.join(' ')).not.toMatch(/(^| )(-y|--yes|--always|--auto|--autonomy|--mode)( |$)/);
    expect(argv).not.toContain(WS); // the workspace is the working directory, never an argument
    expect(argvIsAllowed(argv)).toBe(true);
  });
  it('is recognised only in that exact shape', () => {
    const argv = buildLaunchDecisionArgv({ workspace: WS, launch_id: ID, review_id: REVIEW, candidate_digest: DIGEST, decision: 'approve' });
    for (const bad of [[...argv, '-y'], [...argv, '--yes'], argv.slice(0, -1), ['launch-decision', ...argv.slice(1, 8), 'maybe'], ['generate', '--yes'], [...argv.slice(0, 2), '../x', ...argv.slice(3)]]) {
      expect(isLaunchDecisionArgv(bad), bad.join(' ')).toBe(false);
      expect(argvIsAllowed(bad), bad.join(' ')).toBe(false);
    }
  });
});

describe('ApprovalHost.decide', () => {
  it('relays one valid decision over the run\'s channel, as a change_approval, with no capability of its own, and reports the waiter\'s answer', async () => {
    const { host, calls } = harness();
    const result = await host.decide(good());
    expect(result).toEqual({ accepted: true, code: 'ACCEPTED', message: expect.any(String), launch_id: ID, review_id: REVIEW, decision: 'approve', decided_at: NOW.toISOString() });
    expect(calls).toEqual([{ launch_id: ID, review_id: REVIEW, kind: 'change_approval', candidate_digest: DIGEST, decision: 'approve' }]);
    expect(Object.keys(calls[0]!)).not.toContain('capability'); // the capability is added inside the channel, never by the caller or the renderer
  });

  it('refuses a malformed request before anything is relayed', async () => {
    for (const bad of [null, 'approve', {}, good({ extra: 1 }), good({ decision: 'approve-always' }), good({ decision: 'Approve' }), good({ review_id: 'x' }), good({ candidate_digest: 'sha256:1' }), good({ launch_id: '../x' }),
      good({ workspace: 'relative' }), good({ workspace: '/a\u0000b' }), good({ capability: 'ab'.repeat(32) }), { workspace: WS, launch_id: ID, review_id: REVIEW, decision: 'approve' }]) {
      const { host, calls } = harness();
      expect(await host.decide(bad), JSON.stringify(bad)).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
      expect(calls).toHaveLength(0);
    }
  });

  it('refuses a folder that is not the one open in this host', async () => {
    const { host, calls } = harness(undefined, { currentWorkspace: () => '/Users/x/other' });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(calls).toHaveLength(0);
  });

  it('has no backend in a fixture build and reports a host that cannot start Kriya', async () => {
    expect(await harness(undefined, { prepare: () => null }).host.decide(good())).toMatchObject({ accepted: false, code: 'EXECUTION_UNAVAILABLE' });
    const { host, calls } = harness(undefined, { prepare: () => ({ ok: false, message: 'executable missing' }) });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
    expect(calls).toHaveLength(0);
  });

  it('serializes decisions: a second click while one is in flight is refused and relays nothing', async () => {
    let release: (value: ChannelAnswer) => void = () => undefined;
    const pending = new Promise<ChannelAnswer>((resolve) => { release = resolve; });
    const { host, calls } = harness(() => pending);
    const first = host.decide(good());
    const second = await host.decide(good({ decision: 'reject' }));
    expect(second).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    release(ACCEPTED);
    expect(await first).toMatchObject({ accepted: true });
    expect(calls).toHaveLength(1);
    expect(await host.decide(good())).toMatchObject({ accepted: true }); // free again once the first finished
  });

  it.each([
    ['STALE_REVIEW', 'the change under review is no longer the one you were shown'],
    ['CAPABILITY_MISMATCH', 'did not accept the decision'],
    ['REVIEW_EXPIRED', 'expired'],
    ['REVIEW_CHANNEL_NOT_READY', 'private channel'],
    ['WRONG_REVIEW_KIND', 'not of this kind'],
  ])('reports the waiter\'s refusal %s with its code and never turns it into an acceptance or echoes identities', async (code, fragment) => {
    const result = await harness({ accepted: false, code }).host.decide(good());
    expect(result).toMatchObject({ accepted: false, code, launch_id: null, review_id: null, decision: null, decided_at: null });
    expect(result.message).toContain(fragment);
  });

  it('an accepted flag is the only acceptance: a closed or silent channel is a host-visible failure that says nothing is known to be recorded', async () => {
    for (const code of ['CHANNEL_CLOSED', 'CHANNEL_TIMEOUT']) {
      const result = await harness({ accepted: false, code }).host.decide(good());
      expect(result).toMatchObject({ accepted: false, code });
      expect(result.message).toMatch(/nothing is known to be recorded/);
    }
  });
});


describe('an approval is only forwarded while Kriya can still prove the exact change under review', () => {
  it('asks Kriya for exactly this review BEFORE the approve and forwards it only on a proof', async () => {
    const asked: unknown[][] = [];
    const { host, calls } = harness(undefined, { reviewable: async (...a) => { asked.push(a); return REVIEWABLE; } });
    expect((await host.decide(good())).accepted).toBe(true);
    expect(asked).toEqual([[WS, ID, REVIEW]]);
    expect(calls).toHaveLength(1);
  });
  it.each([
    ['an error answer', { error: { code: 'REVIEW_DIFF_UNAVAILABLE' }, data: null }],
    ['an error next to apparently valid data', { error: { code: 'X' }, data: REVIEWABLE.data }],
    ['not verified', { error: null, data: { ...REVIEWABLE.data, verified: false } }],
    ['another review', { error: null, data: { ...REVIEWABLE.data, review_id: 'ra-' + 'cc'.repeat(12) } }],
    ['another digest', { error: null, data: { ...REVIEWABLE.data, candidate_digest: 'sha256:' + 'dd'.repeat(32) } }],
    ['no longer pending', { error: null, data: { ...REVIEWABLE.data, status: 'EXPIRED' } }],
    ['truncated', { error: null, data: { ...REVIEWABLE.data, truncated: true } }],
    ['a truncated file', { error: null, data: { ...REVIEWABLE.data, files: [{ path: 'a', truncated: true }] } }],
    ['no files', { error: null, data: { ...REVIEWABLE.data, files: [] } }],
    ['nothing', null],
    ['a non-envelope', 'ok'],
  ])('refuses to send an approve when Kriya answers: %s', async (_name, answer) => {
    const { host, calls } = harness(undefined, { reviewable: async () => answer });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'CHANGE_NOT_REVIEWABLE' });
    expect(calls).toEqual([]); // nothing was relayed: nothing reached Kriya
    expect(changeIsReviewable(answer, REVIEW, DIGEST)).toBe(false);
  });
  it('a failing check is a refusal, not an approval, and the guard is released', async () => {
    let fail = true;
    const { host, calls } = harness(undefined, { reviewable: async () => { if (fail) throw new Error('service down'); return REVIEWABLE; } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'CHANGE_NOT_REVIEWABLE' });
    fail = false;
    expect((await host.decide(good())).accepted).toBe(true);
    expect(calls).toHaveLength(1);
  });
  it('a REJECTION is never gated: it needs no proof of the change', async () => {
    let asked = 0;
    const { host, calls } = harness(undefined, { reviewable: async () => { asked += 1; return null; } });
    expect((await host.decide(good({ decision: 'reject' }))).accepted).toBe(true);
    expect(asked).toBe(0);
    expect(calls).toHaveLength(1);
  });
});
