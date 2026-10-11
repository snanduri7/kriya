/** The review-decision proxy (GUI final): a narrow, typed forwarder. It decides nothing - Kriya validates and records - so these tests pin what it WILL and WILL NOT send. */
import { describe, expect, it } from 'vitest';
import { ApprovalHost, type ApprovalDeps, type ApprovalRun } from '../src/main/approval_host';
import { buildLaunchDecisionArgv, isLaunchDecisionArgv, argvIsAllowed } from '../src/main/kriya_argv';

const WS = '/Users/x/project';
const ID = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const DIGEST = 'sha256:' + 'b2'.repeat(32);
const good = (over: Record<string, unknown> = {}) => ({ workspace: WS, launch_id: ID, review_id: REVIEW, candidate_digest: DIGEST, decision: 'approve', ...over });

function harness(answer: ApprovalRun | (() => Promise<ApprovalRun>) = { kind: 'json', json: { accepted: true, code: 'ACCEPTED', message: 'ok', launch_id: ID, review_id: REVIEW, decision: 'approve', decided_at: 't' }, exitCode: 0, stderrTail: '' }, over: Partial<ApprovalDeps> = {}) {
  const calls: Array<Parameters<ApprovalDeps['run']>[0]> = [];
  const deps: ApprovalDeps = {
    run: async (options) => { calls.push(options); return typeof answer === 'function' ? answer() : answer; },
    prepare: () => ({ ok: true, executable: '/opt/kriya/.venv/bin/kriya', env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' } }),
    currentWorkspace: () => WS, ...over,
  };
  return { host: new ApprovalHost(deps), calls };
}

describe('the decision command line', () => {
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
  it('forwards one valid decision to Kriya in the open folder with the fixed child environment and reports Kriya\'s answer', async () => {
    const { host, calls } = harness();
    const result = await host.decide(good());
    expect(result).toMatchObject({ accepted: true, code: 'ACCEPTED', decision: 'approve', review_id: REVIEW });
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ executable: '/opt/kriya/.venv/bin/kriya', cwd: WS, env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' }, limits: { timeoutMs: 30_000 } });
    expect(calls[0]!.argv).toEqual(buildLaunchDecisionArgv(good() as never));
  });

  it('refuses a malformed request before anything runs', async () => {
    for (const bad of [null, 'approve', {}, good({ extra: 1 }), good({ decision: 'approve-always' }), good({ decision: 'Approve' }), good({ review_id: 'x' }), good({ candidate_digest: 'sha256:1' }), good({ launch_id: '../x' }),
      good({ workspace: 'relative' }), good({ workspace: '/a\u0000b' }), { workspace: WS, launch_id: ID, review_id: REVIEW, decision: 'approve' }]) {
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

  it('serializes decisions: a second click while one is in flight is refused and runs nothing', async () => {
    let release: (value: ApprovalRun) => void = () => undefined;
    const pending = new Promise<ApprovalRun>((resolve) => { release = resolve; });
    const { host, calls } = harness(() => pending);
    const first = host.decide(good());
    const second = await host.decide(good({ decision: 'reject' }));
    expect(second).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    release({ kind: 'json', json: { accepted: true, code: 'ACCEPTED', message: 'ok', decision: 'approve' }, exitCode: 0, stderrTail: '' });
    expect(await first).toMatchObject({ accepted: true });
    expect(calls).toHaveLength(1);
    expect(await host.decide(good())).toMatchObject({ accepted: true }); // free again once the first finished
  });

  it('reports Kriya\'s refusal verbatim (code and message) and never turns it into an acceptance', async () => {
    const { host } = harness({ kind: 'json', json: { accepted: false, code: 'STALE_REVIEW', message: 'the change under review is no longer the one you were shown' }, exitCode: 2, stderrTail: '' });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'STALE_REVIEW', message: 'the change under review is no longer the one you were shown' });
  });

  it('accepts only when Kriya says accepted AND exits 0', async () => {
    expect(await harness({ kind: 'json', json: { accepted: true, code: 'ACCEPTED', message: 'ok' }, exitCode: 1, stderrTail: '' }).host.decide(good())).toMatchObject({ accepted: false });
    expect(await harness({ kind: 'json', json: { accepted: 'yes', code: 'ACCEPTED' }, exitCode: 0, stderrTail: '' }).host.decide(good())).toMatchObject({ accepted: false });
    expect(await harness({ kind: 'json', json: { code: 'ACCEPTED' }, exitCode: 0, stderrTail: '' }).host.decide(good())).toMatchObject({ accepted: false });
  });

  it('a process failure or a non-JSON answer is a host error that says nothing is known to be recorded', async () => {
    for (const run of [{ kind: 'error', message: 'spawn failed', exitCode: null, stderrTail: '' }, { kind: 'json', json: 'text', exitCode: 0, stderrTail: '' }, { kind: 'json', json: null, exitCode: 0, stderrTail: '' }] as ApprovalRun[]) {
      const result = await harness(run).host.decide(good());
      expect(result).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
      expect(result.message).toMatch(/nothing is known to be recorded/);
    }
  });

  it('forwards no text from Kriya\'s stderr to the renderer', async () => {
    const { host } = harness({ kind: 'error', message: 'boom', exitCode: 1, stderrTail: 'SECRET-TRACEBACK-TEXT' });
    expect(JSON.stringify(await host.decide(good()))).not.toContain('SECRET-TRACEBACK-TEXT');
  });
});
