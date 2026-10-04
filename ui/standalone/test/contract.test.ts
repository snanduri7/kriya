import { describe, expect, it } from 'vitest';
import { LIMITS, validateKupRequest, validateOpenInIde, validateSettingValue } from '../src/main/ipc_contract';
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
    for (const r of [{ operation: 'capabilities' }, { operation: 'snapshot.acquire' }, { operation: 'snapshot.list' }, { operation: 'snapshot.prune' }, { operation: 'snapshot.verify', snapshot_id: SID }, { operation: 'history.list', snapshot_id: SID, limit: 3 }, { operation: 'history.detail', snapshot_id: SID, run_id: 'x' }, { operation: 'workspace.status', workspace: '/w' }] as const) expect(argvIsAllowed(buildKriyaArgv(r))).toBe(true);
    for (const bad of [['generate', 'goal'], ['fix'], ['traces', '--migrate-legacy'], ['traces', '--all'], ['traces', '--json', '-n', '5'], ['traces', '--json', '--run-id', 'r'], ['traces', '--json', '--verify'], ['runs', 'resume'], [], ['sh', '-c', 'traces']]) expect(argvIsAllowed(bad), bad.join(' ')).toBe(false);
    expect(ALLOWED_ARGV_PREFIXES.length).toBe(7);
  });
  it('a run_id can never become an option: the validator rejects leading dashes before argv is built', () => {
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: '--all' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: '-n' }).ok).toBe(false);
  });
});
