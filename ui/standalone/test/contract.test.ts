import { describe, expect, it } from 'vitest';
import type { KupRequest } from '@kriya-ui/kup';
import { LIMITS, validateKupRequest, validateOpenInIde, validateSettingValue, validateStartRun } from '../src/main/ipc_contract';
import { ALLOWED_ARGV_PREFIXES, argvIsAllowed, buildKriyaArgv } from '../src/main/kriya_argv';

const SID = '20261004T093000000000Z-f1c70001';
const INJECTIONS = ['; rm -rf /', '$(id)', '`id`', 'a\nb', 'a\r\nb', 'x\0y', '--help', '-n 5', '../../etc/passwd', ' ', '', 'run id', 'run"id', "run'id", 'run|id', 'run&id', 'ruén'];

describe('KUP request validation (P-25, P-31)', () => {
  it('accepts the five operations with valid parameters', () => {
    expect(validateKupRequest({ operation: 'capabilities' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'history.list', snapshot_id: SID, limit: 200, cursor: 'WyIyMDI2IiwicnVuIl0=' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: 'run-0001.a:b_c' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'history.prompt', snapshot_id: SID, run_id: 'r1' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'snapshot.acquire' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'snapshot.list', verify: true }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'snapshot.prune', keep: 2 }).ok).toBe(true);
    // digest verified at pin (F-4): exactly one snapshot id, nothing else
    expect(validateKupRequest({ operation: 'snapshot.verify', snapshot_id: SID }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'snapshot.verify' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'snapshot.verify', snapshot_id: 'latest' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'snapshot.verify', snapshot_id: SID, verify: true }).ok).toBe(false);
    for (const bad of INJECTIONS) expect(validateKupRequest({ operation: 'snapshot.verify', snapshot_id: bad }).ok, `verify ${JSON.stringify(bad)}`).toBe(false);
    // pinned reads (gate C-2): no snapshot_id, 'latest', or a malformed id is refused
    expect(validateKupRequest({ operation: 'history.list', limit: 5 }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'history.detail', run_id: 'r1' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'history.list', snapshot_id: 'latest' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'workspace.status', workspace: '/Users/x/project' }).ok).toBe(true);
  });
  it('refuses every injection shape in run_id and cursor', () => {
    for (const bad of INJECTIONS) {
      expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: bad }).ok, `run_id ${JSON.stringify(bad)}`).toBe(false);
      expect(validateKupRequest({ operation: 'history.list', snapshot_id: SID, cursor: bad }).ok, `cursor ${JSON.stringify(bad)}`).toBe(false);
      expect(validateKupRequest({ operation: 'history.list', snapshot_id: bad }).ok, `snapshot_id ${JSON.stringify(bad)}`).toBe(false);
    }
  });
  it('refuses unknown operations, extra keys, bad limits, relative workspaces', () => {
    expect(validateKupRequest({ operation: 'history.delete', run_id: 'r' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'capabilities', extra: 1 }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: 'r', cursor: 'c' }).ok).toBe(false);
    for (const limit of [0, 201, 1.5, '50', -1, Number.NaN]) expect(validateKupRequest({ operation: 'history.list', snapshot_id: SID, limit }).ok, String(limit)).toBe(false);
    expect(validateKupRequest({ operation: 'snapshot.prune', keep: -1 }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'snapshot.list', verify: 'yes' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'workspace.status', workspace: 'relative/path' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'workspace.status', workspace: '/a\nb' }).ok).toBe(false);
    expect(validateKupRequest(null).ok).toBe(false);
    expect(validateKupRequest('capabilities').ok).toBe(false);
  });
  it('open-in-IDE and settings values are validated', () => {
    expect(validateOpenInIde({ path: '/w/a.py', line: 7 }).ok).toBe(true);
    expect(validateOpenInIde({ path: '/w/a.py', line: 0 }).ok).toBe(false);
    expect(validateOpenInIde({ path: '/w/a.py', line: '7' }).ok).toBe(false);
    expect(validateOpenInIde({ path: '/w/a.py\n', line: 7 }).ok).toBe(false);
    expect(validateOpenInIde({ path: '/w/a.py', line: 7, cmd: 'x' }).ok).toBe(false);
    expect(validateSettingValue('editor', 'vim').ok).toBe(false);
    expect(validateSettingValue('editor', 'intellij').ok).toBe(true);
    expect(validateSettingValue('workspacePath', null).ok).toBe(true);
    // the configuration directory setting (F-5): absolute path or null (= operator HOME)
    expect(validateSettingValue('configDirectory', '/Volumes/work/project').ok).toBe(true);
    expect(validateSettingValue('configDirectory', null).ok).toBe(true);
    for (const bad of ['relative', '', '/a\nb', '~/x']) expect(validateSettingValue('configDirectory', bad).ok, bad).toBe(false);
    expect(validateSettingValue('workspacePath', 'relative').ok).toBe(false);
  });
});

const EVIDENCE_REQUESTS = [
  { operation: 'evidence.manifest', run_id: 'r1' }, { operation: 'evidence.manifest', run_id: 'r1', snapshot_id: SID }, { operation: 'evidence.verify', run_id: 'r1' },
  { operation: 'evidence.records', run_id: 'r1' }, { operation: 'evidence.records', run_id: 'r1', unit: '-u', phase: 'attempt', role: 'developer', attempt: 2, kind: ['gate.result', 'model.request'], from_seq: 3, limit: 50, cursor: 'abc' },
] as const;

describe('M6 request grammar', () => {
  const LAUNCH = '20261010T101010-abcdef01';
  it('execution.preflight takes exactly an absolute workspace; launch.status exactly workspace + a launch id', () => {
    expect(validateKupRequest({ operation: 'execution.preflight', workspace: '/w' }).ok).toBe(true);
    expect(validateKupRequest({ operation: 'launch.status', workspace: '/w', launch_id: LAUNCH }).ok).toBe(true);
    for (const bad of [{ operation: 'execution.preflight' }, { operation: 'execution.preflight', workspace: 'w' }, { operation: 'execution.preflight', workspace: '/w', extra: 1 },
      { operation: 'launch.status', workspace: '/w' }, { operation: 'launch.status', launch_id: LAUNCH }, { operation: 'launch.status', workspace: '/w', launch_id: 'latest' },
      { operation: 'launch.status', workspace: '/w', launch_id: `${LAUNCH}/../x` }, { operation: 'launch.status', workspace: '/w', launch_id: LAUNCH, run_id: 'x' }]) {
      expect(validateKupRequest(bad).ok, JSON.stringify(bad)).toBe(false);
    }
  });
  it('their argv are fixed and allowlisted, the values attached as data', () => {
    const pre = buildKriyaArgv({ operation: 'execution.preflight', workspace: '/w x' });
    expect(pre).toEqual(['traces', '--json', '--execution-preflight', '/w x']);
    const status = buildKriyaArgv({ operation: 'launch.status', workspace: '/w', launch_id: LAUNCH });
    expect(status).toEqual(['traces', '--json', '--launch-status', LAUNCH, '--workspace', '/w']);
    expect(argvIsAllowed(pre) && argvIsAllowed(status)).toBe(true);
    expect(argvIsAllowed(['generate', '--json', '--launch-id', LAUNCH])).toBe(false); // an execution command line is never a READ argv
  });
  it('Start Run is exactly {workspace, goal}', () => {
    expect(validateStartRun({ workspace: '/w', goal: 'do it' })).toEqual({ ok: true, value: { workspace: '/w', goal: 'do it' } });
    for (const bad of [{ workspace: '/w' }, { goal: 'x' }, { workspace: '/w', goal: 'x', options: {} }, { workspace: '/w', goal: '' }, { workspace: '/w', goal: ' ' }, { workspace: '/w', goal: 5 }, { workspace: 'w', goal: 'x' }, { workspace: '/w', goal: 'a\0b' }, { workspace: '/w', goal: 'x'.repeat(LIMITS.goalMax + 1) }, null, 'x']) {
      expect(validateStartRun(bad).ok, JSON.stringify(bad)).toBe(false);
    }
    expect(validateStartRun({ workspace: '/w', goal: 'x'.repeat(LIMITS.goalMax) }).ok).toBe(true);
    expect(validateStartRun({ workspace: '/w', goal: 'multi\nline\ttext' }).ok).toBe(true);
  });
});

describe('executable setting (reviewer P3-4)', () => {
  it('the Kriya executable setting must be an absolute path without control characters, like the other path settings', () => {
    expect(validateSettingValue('kriyaExecutable', '/opt/kriya/bin/kriya').ok).toBe(true);
    for (const bad of ['kriya', './kriya', '../bin/kriya', '', '/opt/kriya\nx']) expect(validateSettingValue('kriyaExecutable', bad).ok, JSON.stringify(bad)).toBe(false);
    expect(validateSettingValue('kriyaExecutable', null).ok).toBe(true); // clearing it is always allowed
  });
});

describe('evidence argv (Run Cockpit v1.1)', () => {
  it('builds the exact CLI grammar, values attached with = so a value can never be read as an option', () => {
    expect(buildKriyaArgv({ operation: 'evidence.verify', run_id: 'r1' })).toEqual(['evidence', 'verify', 'r1', '--json', '--kup-version', '1']);
    expect(buildKriyaArgv({ operation: 'evidence.manifest', run_id: 'r1', snapshot_id: SID })).toEqual(['evidence', 'manifest', 'r1', '--json', '--kup-version', '1', `--snapshot-id=${SID}`]);
    expect(buildKriyaArgv({ operation: 'evidence.change_content', run_id: 'r1', seq: 28, part: 'diff' })).toEqual(['evidence', 'change-content', 'r1', '--json', '--kup-version', '1', '--seq=28', '--part=diff']);
    expect(buildKriyaArgv({ operation: 'evidence.call_content', run_id: 'r1', seq: 5, part: 'messages' })).toEqual(['evidence', 'call-content', 'r1', '--json', '--kup-version', '1', '--seq=5', '--part=messages']);
    expect(buildKriyaArgv({ operation: 'evidence.manifest', run_id: 't1', snapshot_id: SID, from_trace: true })).toEqual(['evidence', 'manifest', 't1', '--json', '--kup-version', '1', `--snapshot-id=${SID}`, '--from-trace']);
    expect(buildKriyaArgv({ operation: 'evidence.records', run_id: 'r1', unit: '-u', kind: ['gate.result', 'model.request'], attempt: 2, from_seq: 3, limit: 50, cursor: 'abc', phase: 'p', role: 'r' })).toEqual(
      ['evidence', 'records', 'r1', '--json', '--kup-version', '1', '--unit=-u', '--phase=p', '--role=r', '--attempt=2', '--from-seq=3', '--limit=50', '--cursor=abc', '--kind=gate.result', '--kind=model.request']);
  });
  it('the allowlist pins the subcommand, the run id position and the KUP flags', () => {
    for (const bad of [['evidence', 'prune', 'r', '--json', '--kup-version', '1'], ['evidence', 'show', 'r', '--json', '--kup-version', '1'], ['evidence', 'verify', 'r', '--json'], ['evidence', 'verify', 'r', '--kup-version', '1', '--json'], ['evidence', 'verify', 'r', '--json', '--kup-version', '2'], ['evidence', 'candidate', 'r', '--out', '/x'], ['evidence', 'records', '--json', '--kup-version', '1'], ['evidence']]) expect(argvIsAllowed(bad), bad.join(' ')).toBe(false);
  });
});

describe('argv builder (the only command-line source)', () => {
  it('produces exactly the P-25 grammar', () => {
    expect(buildKriyaArgv({ operation: 'capabilities' })).toEqual(['traces', '--json', '--capabilities']);
    expect(buildKriyaArgv({ operation: 'snapshot.acquire' })).toEqual(['traces', '--json', '--snapshot']);
    expect(buildKriyaArgv({ operation: 'snapshot.acquire', workspace: '/w' })).toEqual(['traces', '--json', '--snapshot', '--workspace', '/w']);
    expect(buildKriyaArgv({ operation: 'snapshot.list', verify: true })).toEqual(['traces', '--json', '--snapshots', '--verify']);
    expect(buildKriyaArgv({ operation: 'snapshot.prune', keep: 0 })).toEqual(['traces', '--json', '--snapshot-prune', '--keep', '0']);
    expect(buildKriyaArgv({ operation: 'snapshot.verify', snapshot_id: SID })).toEqual(['traces', '--json', '--snapshot-verify', SID]);
    expect(buildKriyaArgv({ operation: 'history.list', snapshot_id: SID })).toEqual(['traces', '--json', '--snapshot-id', SID, '-n', String(LIMITS.listDefault)]);
    expect(buildKriyaArgv({ operation: 'history.list', snapshot_id: SID, limit: 7, cursor: 'abc' })).toEqual(['traces', '--json', '--snapshot-id', SID, '-n', '7', '--cursor', 'abc']);
    expect(buildKriyaArgv({ operation: 'history.detail', snapshot_id: SID, run_id: 'r1' })).toEqual(['traces', '--json', '--snapshot-id', SID, '--run-id', 'r1']);
    expect(buildKriyaArgv({ operation: 'history.prompt', snapshot_id: SID, run_id: 'r1' })).toEqual(['traces', '--json', '--snapshot-id', SID, '--run-id', 'r1', '--include-prompt']);
    expect(buildKriyaArgv({ operation: 'workspace.status', workspace: '/w' })).toEqual(['runs', 'status', '--workspace', '/w', '--json', '--kup-version', '1']);
  });
  it('every built argv is allowlisted; anything else is not', () => {
    for (const r of [{ operation: 'capabilities' }, { operation: 'snapshot.acquire' }, { operation: 'snapshot.list' }, { operation: 'snapshot.prune' }, { operation: 'snapshot.verify', snapshot_id: SID }, { operation: 'history.list', snapshot_id: SID, limit: 3 }, { operation: 'history.detail', snapshot_id: SID, run_id: 'x' }, { operation: 'workspace.status', workspace: '/w' }, { operation: 'workspace.describe', workspace: '/w' }, { operation: 'history.list', snapshot_id: SID, workspace: '/w' }] as const) expect(argvIsAllowed(buildKriyaArgv(r))).toBe(true);
    for (const r of EVIDENCE_REQUESTS) expect(argvIsAllowed(buildKriyaArgv(r as unknown as KupRequest)), r.operation).toBe(true);
    for (const bad of [['generate', 'goal'], ['fix'], ['traces', '--migrate-legacy'], ['traces', '--all'], ['traces', '--json', '-n', '5'], ['traces', '--json', '--run-id', 'r'], ['traces', '--json', '--verify'], ['runs', 'resume'], [], ['sh', '-c', 'traces']]) expect(argvIsAllowed(bad), bad.join(' ')).toBe(false);
    expect(ALLOWED_ARGV_PREFIXES.length).toBe(10);
    expect(buildKriyaArgv({ operation: 'workspace.describe', workspace: '/w' })).toEqual(['traces', '--json', '--describe-workspace', '/w']);
    expect(buildKriyaArgv({ operation: 'history.list', snapshot_id: SID, limit: 5, workspace: '/w' })).toEqual(['traces', '--json', '--snapshot-id', SID, '-n', '5', '--workspace', '/w']);
  });
  it('a run_id can never become an option: the validator rejects leading dashes before argv is built', () => {
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: '--all' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: '-n' }).ok).toBe(false);
  });
});
