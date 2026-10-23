/**
 * The runtime-command decision proxy (Demo V2): a narrow, typed forwarder with a NATIVE confirmation. It decides nothing - Kriya validates, records and executes - so these tests pin what it
 * WILL and WILL NOT send, and the order: validate -> open workspace -> re-prove the pending subject -> native confirmation -> Kriya. Controlled executors only.
 */
import { describe, expect, it } from 'vitest';
import { DIALOG_COMMANDS_MAX, RuntimeCommandHost, commandsAreReviewable, confirmationDialog, dialogCommandLine, isRunResponse, CONFIRM_BUTTONS, CONFIRM_RUN_INDEX, type RuntimeCommandDeps } from '../src/main/runtime_command_host';
import type { ChannelAnswer, ChannelDecisionRequest } from '../src/main/review_channel';
import { buildRuntimeCommandDecisionArgv } from '../src/main/kriya_argv';
import { IPC_CHANNELS } from '../src/main/ipc_contract';

const WS = '/Users/x/project';
const ID = '20261010T101010-abcdef01';
const REVIEW = 'ra-' + 'a1'.repeat(12);
const FP = 'sha256:' + 'b2'.repeat(32);
const SUBJECT = { error: null, data: { verified: true, review_id: REVIEW, command_fingerprint: FP, status: 'PENDING', commands: [['mvn', '-q', 'test'], ['java', '-cp', 'target/classes', 'App']], working_directory: '/wt/path', input_channel: 'none' } };
const good = (over: Record<string, unknown> = {}) => ({ workspace: WS, launch_id: ID, review_id: REVIEW, command_fingerprint: FP, decision: 'approve', ...over });
const ACCEPTED: ChannelAnswer = { accepted: true, code: 'ACCEPTED' };

function harness(over: Partial<RuntimeCommandDeps> = {}, answer: ChannelAnswer | (() => Promise<ChannelAnswer>) = ACCEPTED) {
  const calls: ChannelDecisionRequest[] = [];
  const order: string[] = [];
  let shown = 0;
  const deps: RuntimeCommandDeps = {
    channel: { decide: async (request) => { order.push('relay'); calls.push(request); return typeof answer === 'function' ? answer() : answer; } },
    prepare: () => ({ ok: true, executable: '/opt/kriya/.venv/bin/kriya', env: { HOME: '/Users/x', PATH: '/usr/bin:/bin' } }),
    reviewable: async () => { order.push('prove'); return SUBJECT; },
    confirm: async (_commands) => { order.push('confirm'); shown += 1; return true; },
    currentWorkspace: () => WS, ...over,
  };
  return { host: new RuntimeCommandHost(deps), calls, order, shown: () => shown };
}

describe('the runtime-command decision command line', () => {
  it('has exactly one shape, ends with --kind runtime_command, and carries no command text', () => {
    const argv = buildRuntimeCommandDecisionArgv({ workspace: WS, launch_id: ID, review_id: REVIEW, command_fingerprint: FP, decision: 'reject' });
    expect(argv).toEqual(['launch-decision', '--launch-id', ID, '--review-id', REVIEW, '--candidate-digest', FP, '--decision', 'reject', '--kind', 'runtime_command']);
    expect(argv.join(' ')).not.toMatch(/(^| )(-y|--yes|--always|--auto|--autonomy|--mode)( |$)/);
  });
  it('has its own channel, distinct from the change review', () => {
    expect(IPC_CHANNELS.decideRuntimeCommand).toBe('kriya:host:execution:decide-runtime');
    expect(IPC_CHANNELS.decideRuntimeCommand).not.toBe(IPC_CHANNELS.decideApproval);
  });
});

describe('the native confirmation text: owner-approved sentences plus only what Kriya proved', () => {
  it('is the owner-approved wording, Cancel first/default/escape, and with no commands carries nothing from a review', () => {
    const d = confirmationDialog();
    expect(d.title).toBe('Run Verification Commands');
    expect(d.message).toBe('Allow Kriya to run the reviewed verification commands?');
    expect(d.detail).toBe('Kriya will run the commands you reviewed in its isolated worktree to verify the application.');
    expect(d.buttons).toEqual(['Cancel', 'Run Commands']);
    expect([d.defaultId, d.cancelId]).toEqual([0, 0]);
    expect(CONFIRM_BUTTONS[CONFIRM_RUN_INDEX]).toBe('Run Commands');
    expect(isRunResponse(1)).toBe(true);
    for (const r of [0, 2, -1]) expect(isRunResponse(r)).toBe(false);
  });
});

describe('the native confirmation shows the first commands exactly as KRIYA proved them (independent review I2)', () => {
  it('lists up to three, numbered, strips control/bidi characters, bounds long lines and counts the rest', () => {
    const d = confirmationDialog([['mvn', '-q', 'package'], ['java', '-cp', 'target/classes', 'App'], ['echo', 'a\u202eb\u0000c'], ['x'], ['y']]);
    expect(d.detail.split('\n')).toEqual(['Kriya will run the commands you reviewed in its isolated worktree to verify the application.', '', '1. mvn -q package', '2. java -cp target/classes App', '3. echo a b c', '…and 2 more']);
    expect(d.detail).not.toMatch(/\u202e|\u0000/);
    expect(dialogCommandLine(['x'.repeat(500)]).length).toBe(100);
    expect(DIALOG_COMMANDS_MAX).toBe(3);
    expect(d.title).toBe('Run Verification Commands');
  });
  it('the host hands the dialog the commands of KRIYA\'s envelope, never anything the renderer put in the request', async () => {
    const seen: Array<ReadonlyArray<readonly string[]>> = [];
    const { host } = harness({ confirm: async (commands) => { seen.push(commands); return false; } });
    await host.decide(good());
    expect(seen).toEqual([SUBJECT.data.commands]);
    const forged = harness({ confirm: async (commands) => { seen.push(commands); return false; } });
    await forged.host.decide(good({ commands: [['rm', '-rf', '/']] }));   // refused by the grammar before any dialog
    expect(seen).toHaveLength(1);
  });
});

describe('the subject proof', () => {
  it('accepts only THIS review, THIS fingerprint, PENDING, with a well-formed non-empty command set', () => {
    expect(commandsAreReviewable(SUBJECT, REVIEW, FP)).toBe(true);
    const mutate = (patch: Record<string, unknown>) => ({ error: null, data: { ...SUBJECT.data, ...patch } });
    for (const bad of [
      null, { error: { code: 'X' }, data: null }, mutate({ verified: false }), mutate({ review_id: 'ra-' + 'c3'.repeat(12) }), mutate({ command_fingerprint: 'sha256:' + 'd4'.repeat(32) }),
      mutate({ status: 'APPROVED' }), mutate({ status: 'EXPIRED' }), mutate({ commands: [] }), mutate({ commands: [[]] }), mutate({ commands: [['mvn', '']] }), mutate({ commands: 'mvn test' }),
      mutate({ working_directory: '' }), mutate({ working_directory: undefined }),
    ]) expect(commandsAreReviewable(bad, REVIEW, FP), JSON.stringify(bad)).toBe(false);
  });
});

describe('RuntimeCommandHost.decide', () => {
  it('approve: proves the subject, THEN the native confirmation, THEN relays once over the run\'s channel as a runtime_command and reports the waiter\'s answer', async () => {
    const { host, calls, order } = harness();
    expect(await host.decide(good())).toMatchObject({ accepted: true, code: 'ACCEPTED', decision: 'approve', review_id: REVIEW });
    expect(order).toEqual(['prove', 'confirm', 'relay']);
    expect(calls).toEqual([{ launch_id: ID, review_id: REVIEW, kind: 'runtime_command', candidate_digest: FP, decision: 'approve' }]);
    expect(Object.keys(calls[0]!)).not.toContain('capability'); // added inside the channel, never by the caller or the renderer
  });

  it('REJECT is the explicit refusal: no proof, no native dialog, Kriya is told', async () => {
    const { host, calls, order, shown } = harness();
    expect(await host.decide(good({ decision: 'reject' }))).toMatchObject({ accepted: true });
    expect(order).toEqual(['relay']);
    expect(shown()).toBe(0);
    expect(calls[0]).toMatchObject({ kind: 'runtime_command', decision: 'reject', candidate_digest: FP });
  });

  it('native CANCEL is "no decision yet": nothing is sent, no rejection is recorded, the review stays pending', async () => {
    const { host, calls, order } = harness({ confirm: async () => false });
    const result = await host.decide(good());
    expect(result).toMatchObject({ accepted: false, code: 'CONFIRMATION_DECLINED', decision: null });
    expect(calls).toEqual([]);
    expect(order).not.toContain('relay');
  });

  it('a renderer cannot invent or alter a command, a directory, another run or another unit: the request grammar has no such field', async () => {
    const { host, calls, shown } = harness();
    for (const extra of [{ commands: [['rm', '-rf', '/']] }, { command: 'rm -rf /' }, { argv: ['x'] }, { cwd: '/' }, { working_directory: '/' }, { stdin: 'x' }, { env: { A: 'b' } }, { unit_id: 's1' }, { kind: 'change_approval' }]) {
      expect(await host.decide(good(extra)), JSON.stringify(extra)).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    }
    for (const bad of [null, [], 'x', {}, good({ workspace: 'relative' }), good({ launch_id: '../x' }), good({ review_id: 'ra-short' }), good({ command_fingerprint: 'sha256:' + 'B2'.repeat(32) }), good({ decision: 'always' }), good({ decision: 'approve ' })]) {
      expect(await host.decide(bad), JSON.stringify(bad)).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    }
    expect(calls).toEqual([]);
    expect(shown()).toBe(0);
  });

  it('a stale or unverifiable subject is never shown and never sent: no native dialog, no Kriya call', async () => {
    for (const envelope of [null, { error: { code: 'REVIEW_NOT_FOUND' }, data: null }, { error: null, data: { ...SUBJECT.data, command_fingerprint: 'sha256:' + 'e5'.repeat(32) } }, { error: null, data: { ...SUBJECT.data, status: 'APPROVED' } }, { error: null, data: { ...SUBJECT.data, verified: false } }]) {
      const { host, calls, shown } = harness({ reviewable: async () => envelope });
      expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'COMMAND_NOT_REVIEWABLE' });
      expect(calls).toEqual([]);
      expect(shown()).toBe(0);
    }
    const { host, calls, shown } = harness({ reviewable: async () => { throw new Error('boom'); } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'COMMAND_NOT_REVIEWABLE' });
    expect([calls.length, shown()]).toEqual([0, 0]);
  });

  it('a wrong-kind or other-run review is a Kriya refusal passed through, and a change-review id is never approved here', async () => {
    const refusal: ChannelAnswer = { accepted: false, code: 'WRONG_REVIEW_KIND' };
    const { host } = harness({}, refusal);
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'WRONG_REVIEW_KIND' });
    const stale: ChannelAnswer = { accepted: false, code: 'STALE_REVIEW' };
    expect(await harness({}, stale).host.decide(good())).toMatchObject({ accepted: false, code: 'STALE_REVIEW' });
  });

  it('only the open folder: another folder, or no folder, reaches neither the proof, the dialog nor Kriya', async () => {
    const other = harness({ currentWorkspace: () => '/Users/x/other' });
    expect(await other.host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    const none = harness({ currentWorkspace: () => null });
    expect(await none.host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect([other.order, none.order]).toEqual([[], []]);
  });

  it('a folder switched while the native dialog was up sends nothing', async () => {
    let open: string | null = WS;
    const { host, calls } = harness({ confirm: async () => { open = '/Users/x/other'; return true; }, currentWorkspace: () => open });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(calls).toEqual([]);
  });

  it('one decision at a time, from before the dialog: a second request while the native dialog is up never reaches the proof or Kriya', async () => {
    let release: (v: boolean) => void = () => undefined;
    const gate = new Promise<boolean>((r) => { release = r; });
    const { host, calls, order } = harness({ confirm: () => gate });
    const first = host.decide(good());
    await new Promise((r) => setTimeout(r, 0));
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    expect(await host.decide(good({ decision: 'reject' }))).toMatchObject({ accepted: false, code: 'DECISION_IN_FLIGHT' });
    release(true);
    expect(await first).toMatchObject({ accepted: true });
    expect(calls).toHaveLength(1);
    expect(order.filter((s) => s === 'prove')).toHaveLength(1);
    expect(await host.decide(good({ decision: 'reject' }))).toMatchObject({ accepted: true }); // released afterwards
  });

  it('a dialog that cannot be shown sends nothing; a Kriya that does not answer is not claimed as recorded', async () => {
    const { host, calls } = harness({ confirm: async () => { throw new Error('no window'); } });
    expect(await host.decide(good())).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
    expect(calls).toEqual([]);
    const silent: ChannelAnswer = { accepted: false, code: 'CHANNEL_TIMEOUT' };
    const result = await harness({}, silent).host.decide(good());
    expect(result).toMatchObject({ accepted: false, code: 'CHANNEL_TIMEOUT' });
    expect(result.message).toMatch(/nothing is known to be recorded/);
  });

  it('a fixture build (no backend) and a failed launch preparation refuse before any proof or dialog', async () => {
    const fixture = harness({ prepare: () => null });
    expect(await fixture.host.decide(good())).toMatchObject({ accepted: false, code: 'EXECUTION_UNAVAILABLE' });
    const broken = harness({ prepare: () => ({ ok: false, message: 'no executable' }) });
    expect(await broken.host.decide(good())).toMatchObject({ accepted: false, code: 'HOST_ERROR' });
    expect([fixture.order, broken.order]).toEqual([[], []]);
  });

  it('the waiter refusing an approve (it did not match the live review) is reported as refused, never as accepted', async () => {
    const refusal: ChannelAnswer = { accepted: false, code: 'CAPABILITY_MISMATCH' };
    expect(await harness({}, refusal).host.decide(good())).toMatchObject({ accepted: false, code: 'CAPABILITY_MISMATCH', decision: null });
  });
});
