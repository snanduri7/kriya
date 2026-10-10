/**
 * The persistent transport against the REAL `kriya kup-stdio` and the REAL one-shot commands, on the same state: parity of typed results, the one
 * warm process, death and restart, and Kriya-owned authority evaluated per request (a durable approval revoked while the child is running).
 * Needs the repository's .venv (skipped, loudly, when absent). Never calls a model.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { validateKupRequest, type KupRequest } from '../src/main/ipc_contract';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { runKriya } from '../src/main/kriya_process';
import { KriyaService, SERVICE_ARGUMENTS } from '../src/main/kriya_service';
import { SEED } from './fixtures/real_seed';

const ROOT = join(__dirname, '..', '..', '..');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const available = existsSync(KRIYA) && existsSync(PYTHON);

const WORKSPACES = `
import os, subprocess, sys
from kriya.control.workspace_identity import workspace_identity
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.trace import TraceLogger
state, base = os.environ['KRIYA_STATE_DIR'], sys.argv[1]
def git(path, *args): subprocess.run(['git', *args], cwd=path, check=True, capture_output=True, env={**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@t', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@t'})
for name in ('wsA', 'wsB', 'empty', 'plain', 'repo', 'dirty'): os.makedirs(os.path.join(base, name))
open(os.path.join(base, 'plain', 'a.txt'), 'w').write('a')
for name in ('repo', 'dirty'):
    p = os.path.join(base, name); git(p, 'init', '-q', '-b', 'trunk'); open(os.path.join(p, 'f'), 'w').write('1'); git(p, 'add', 'f'); git(p, 'commit', '-qm', 'c')
open(os.path.join(base, 'dirty', 'f'), 'w').write('2'); open(os.path.join(base, 'dirty', 'new'), 'w').write('n')
log = TraceLogger(os.path.join(state, 'traces.db'))
for ws, store, trace in (('wsA', 'evA', 't-wsA'), ('wsB', 'evB', 't-wsB')):
    path = os.path.join(base, ws)
    w = AttemptEvidenceWriter(state, store, capture='full', manifest={'workspace_identity': workspace_identity(path), 'workspace_path': path})
    w.append('run.opened', {}); w.seal('run_closed')
    log.log_run(run_id=trace, goal='goal ' + ws, duration_sec=1.0, attempts=1, status='success', files_modified=[], run_events=[{'kind': 'evidence.attempt_store', 'attempt': 0, 'source': 'workflow', 'authority': 'auxiliary', 'message': 'attempt evidence store', 'failure_type': None, 'operation': None, 'details': {'run_id': store, 'status': 'CLOSED', 'capture': 'full'}, 'created_at': 1.0}])
`;

const VOLATILE = new Set(['observed_at', 'request_id', 'duration_ms', 'verified_at']);
const strip = (v: unknown): unknown => (Array.isArray(v) ? v.map(strip) : v && typeof v === 'object' ? Object.fromEntries(Object.entries(v as Record<string, unknown>).filter(([k]) => !VOLATILE.has(k)).map(([k, x]) => [k, strip(x)])) : v);
const alive = (pid: number | null) => { if (pid === null) return false; try { process.kill(pid, 0); return true; } catch { return false; } };

describe.skipIf(!available)('persistent transport vs the one-shot commands, real Kriya', () => {
  let base = ''; let state = ''; let config = ''; let home = '';
  let env: Record<string, string> = {};
  let service: KriyaService;
  let snapshotId = '';

  const oneShot = async (request: KupRequest): Promise<unknown> => {
    const out = await runKriya({ executable: KRIYA, argv: buildKriyaArgv(request), env, cwd: config });
    if (out.kind !== 'json') throw new Error(`one-shot failed: ${out.code} ${out.message}`);
    return out.json;
  };
  const persistent = async (request: KupRequest): Promise<unknown> => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(`service failed: ${out.code} ${out.message}`);
    return out.json;
  };
  const both = async (request: KupRequest) => { const a = await oneShot(request); const b = await persistent(request); return { a, b }; };

  beforeAll(async () => {
    base = mkdtempSync(join(tmpdir(), 'kriya-gui-svc-'));
    state = join(base, 'state'); config = join(base, 'config'); home = join(base, 'home');
    for (const d of [state, config, home]) mkdirSync(d);
    env = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1' };
    const pyEnv = { PATH: process.env.PATH, ...env };
    execFileSync(PYTHON, ['-c', SEED], { env: pyEnv, cwd: config });
    execFileSync(PYTHON, ['-c', WORKSPACES, join(base, 'ws')], { env: pyEnv, cwd: config });
    service = new KriyaService({ executable: KRIYA, cwd: config, env });
    const acquired = (await oneShot({ operation: 'snapshot.acquire' })) as { data: { snapshot_id: string } };
    snapshotId = acquired.data.snapshot_id;
  }, 180_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  it('the host allowlist table and kriya/kup/serve.py::ARGUMENTS name the same operations and the same arguments', () => {
    const python = execFileSync(PYTHON, ['-c', 'import json; from kriya.kup.serve import ARGUMENTS; print(json.dumps({k: sorted(v) for k, v in ARGUMENTS.items()}))'], { env: { PATH: process.env.PATH, ...env }, cwd: config }).toString();
    const kriya = JSON.parse(python) as Record<string, string[]>;
    const host = Object.fromEntries(Object.entries(SERVICE_ARGUMENTS).map(([k, v]) => [k, [...v].sort()]));
    expect(host).toEqual(kriya);
  }, 60_000);

  it('typed results are equal through either transport, excluding per-call clocks and the echoed request id (read operations, success and refusal alike)', async () => {
    const ws = (name: string) => join(base, 'ws', name);
    const corpus: unknown[] = [
      { operation: 'capabilities' }, { operation: 'snapshot.list' }, { operation: 'snapshot.verify', snapshot_id: snapshotId },
      { operation: 'history.list', snapshot_id: snapshotId, limit: 3 }, { operation: 'history.list', snapshot_id: snapshotId, limit: 200 },
      { operation: 'history.list', snapshot_id: snapshotId, limit: 50, workspace: ws('wsA') }, { operation: 'history.list', snapshot_id: snapshotId, limit: 50, workspace: ws('wsB') },
      { operation: 'history.list', snapshot_id: snapshotId, limit: 50, workspace: ws('empty') },
      { operation: 'history.detail', snapshot_id: snapshotId, run_id: 't-ok' }, { operation: 'history.detail', snapshot_id: snapshotId, run_id: 'no-such-run' },
      { operation: 'history.prompt', snapshot_id: snapshotId, run_id: 't-ok' },
      ...['empty', 'plain', 'repo', 'dirty', 'wsA'].map((n) => ({ operation: 'workspace.describe', workspace: ws(n) })),
      { operation: 'workspace.describe', workspace: ws('missing') }, { operation: 'workspace.describe', workspace: join(ws('plain'), 'a.txt') },
      ...['empty', 'repo'].map((n) => ({ operation: 'workspace.status', workspace: ws(n) })),
      { operation: 'evidence.manifest', run_id: 'ev-ok' }, { operation: 'evidence.manifest', run_id: 't-ok', snapshot_id: snapshotId, from_trace: true },
      { operation: 'evidence.manifest', run_id: 't-pruned', snapshot_id: snapshotId, from_trace: true }, { operation: 'evidence.manifest', run_id: 'ev-gone' },
      ...['ev-ok', 'ev-open', 'ev-digest', 'ev-broken', 'ev-gone'].map((id) => ({ operation: 'evidence.verify', run_id: id })),
      { operation: 'evidence.records', run_id: 'ev-ok' }, { operation: 'evidence.records', run_id: 'ev-ok', limit: 3 }, { operation: 'evidence.records', run_id: 'ev-ok', kind: ['gate.result', 'diagnosis'] },
      { operation: 'evidence.records', run_id: 'ev-ok', attempt: 1, role: 'developer' }, { operation: 'evidence.records', run_id: 'ev-open' }, { operation: 'evidence.records', run_id: 'ev-broken' }, { operation: 'evidence.records', run_id: 'ev-gone' },
      { operation: 'evidence.change_content', run_id: 'ev-ok', seq: 5, part: 'diff' }, { operation: 'evidence.change_content', run_id: 'ev-ok', seq: 5, part: 'message' },
      { operation: 'evidence.call_content', run_id: 'ev-ok', seq: 3, part: 'messages' }, { operation: 'evidence.call_content', run_id: 'ev-ok', seq: 5, part: 'diff' },
    ];
    let compared = 0;
    for (const raw of corpus) {
      const v = validateKupRequest(raw);
      expect(v.ok, JSON.stringify(raw)).toBe(true);
      if (!v.ok) continue;
      const { a, b } = await both(v.value);
      expect(strip(b), JSON.stringify(raw)).toEqual(strip(a));
      compared += 1;
    }
    expect(compared).toBe(corpus.length);
    expect(service.stats.starts).toBe(1); // the whole corpus went through ONE warm child
  }, 300_000);

  it('a cursor issued through one transport is honoured by the other (same store binding, same chain head)', async () => {
    const first = (await persistent({ operation: 'evidence.records', run_id: 'ev-ok', limit: 3 })) as { data: { next_cursor: string; records: Array<{ seq: number }> } };
    expect(first.data.next_cursor).toBeTruthy();
    const viaOneShot = (await oneShot({ operation: 'evidence.records', run_id: 'ev-ok', limit: 3, cursor: first.data.next_cursor })) as { data: { records: Array<{ seq: number }> } };
    const viaService = (await persistent({ operation: 'evidence.records', run_id: 'ev-ok', limit: 3, cursor: first.data.next_cursor })) as { data: { records: Array<{ seq: number }> } };
    expect(viaOneShot.data.records.map((r) => r.seq)).toEqual(viaService.data.records.map((r) => r.seq));
    expect(viaService.data.records[0]!.seq).toBe(first.data.records[2]!.seq + 1);
  }, 120_000);

  it('Kriya describes folders, and the workspace scope lists only runs whose manifests name the folder', async () => {
    const ws = (n: string) => join(base, 'ws', n);
    const d = async (n: string) => ((await persistent({ operation: 'workspace.describe', workspace: ws(n) })) as { data: { checkout: Record<string, unknown>; identity: string; name: string } }).data;
    expect((await d('empty')).checkout).toMatchObject({ state: 'not_a_repository', head: null, dirty: null });
    expect((await d('plain')).checkout.state).toBe('not_a_repository');
    expect((await d('repo')).checkout).toMatchObject({ state: 'checkout_root', branch: 'trunk', dirty: false, changed_entries: 0 });
    expect((await d('dirty')).checkout).toMatchObject({ state: 'checkout_root', branch: 'trunk', dirty: true, changed_entries: 2 });
    const listA = (await persistent({ operation: 'history.list', snapshot_id: snapshotId, workspace: ws('wsA') })) as { data: { runs: Array<{ run_id: string }>; workspace_scope: Record<string, number> } };
    expect(listA.data.runs.map((r) => r.run_id)).toEqual(['t-wsA']);
    // seeded: four stores whose manifests record the identity 'ws' plus wsB's run belong to OTHER workspaces; the pruned store, the pre-recorder row and the
    // capture-off row have no readable manifest link at all and are set aside as unattributed, never guessed into either group
    expect(listA.data.workspace_scope).toMatchObject({ matched: 1, other: 5, unattributed: 3, scanned: 9, truncated: false });
  }, 120_000);

  it('opening folders writes nothing to them', async () => {
    const before = execFileSync('find', [join(base, 'ws'), '-type', 'f', '-newer', join(config)], { env: { PATH: '/usr/bin:/bin' } }).toString();
    void before;
    const snapshot = () => execFileSync('sh', ['-c', `cd ${join(base, 'ws')} && find . -type f -exec stat -f '%N %m %z' {} + | sort`]).toString();
    const a = snapshot();
    for (const n of ['empty', 'plain', 'repo', 'dirty']) await persistent({ operation: 'workspace.describe', workspace: join(base, 'ws', n) });
    expect(snapshot()).toBe(a);
    expect(existsSync(join(base, 'ws', 'empty', '.kriya'))).toBe(false);
  }, 60_000);

  it('a killed child is replaced by the next request; a request in flight when the child is killed is a typed HOST_ERROR', async () => {
    const pid = service.pid!;
    expect(alive(pid)).toBe(true);
    const inFlight = service.request({ operation: 'evidence.verify', run_id: 'ev-ok' });
    process.kill(pid, 'SIGKILL');
    const failed = await inFlight;
    expect(failed).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(alive(pid)).toBe(false);
    const after = await persistent({ operation: 'capabilities' }) as { error: unknown };
    expect(after.error).toBeNull();
    expect(service.pid).not.toBe(pid);
    expect(service.stats.deaths).toBeGreaterThanOrEqual(1);
    // and an idle death is replaced just the same
    const second = service.pid!;
    process.kill(second, 'SIGKILL');
    await new Promise((r) => setTimeout(r, 300));
    expect(((await persistent({ operation: 'snapshot.list' })) as { error: unknown }).error).toBeNull();
    expect(service.pid).not.toBe(second);
  }, 120_000);

  it('Kriya-owned authority is evaluated per request: a durable approval revoked while the child runs takes effect on the next request', async () => {
    const onConfig = join(base, 'config-on'); mkdirSync(onConfig);
    writeFileSync(join(onConfig, 'kriya.yaml'), 'evidence:\n  attempt_recorder:\n    content_visibility: "on"\n');
    const pyEnv = { PATH: process.env.PATH, ...env };
    execFileSync(KRIYA, ['authority', 'approve', '--confirm'], { env: pyEnv, cwd: onConfig });
    const warm = new KriyaService({ executable: KRIYA, cwd: onConfig, env });
    try {
      const featureOf = async () => ((await warm.request({ operation: 'capabilities' })) as { json: { error: { code: string } | null; data: { features: { evidence_content: boolean } } | null } }).json;
      const on = await featureOf();
      expect(on.error).toBeNull();
      expect(on.data?.features.evidence_content).toBe(true);
      const pid = warm.pid;
      execFileSync(KRIYA, ['authority', 'revoke'], { env: pyEnv, cwd: onConfig });
      const off = await featureOf();
      expect(warm.pid, 'the same warm child answered').toBe(pid);
      // without the approval the repository-sourced security field is denied: typed CONFIG_AUTHORITY_REFUSED, and content is NOT served
      expect(off.error?.code).toBe('CONFIG_AUTHORITY_REFUSED');
      expect(off.data).toBeNull();
      const denied = (await warm.request({ operation: 'evidence.change_content', run_id: 'ev-ok', seq: 5, part: 'diff' })) as { json: { error: { code: string }; data: unknown } };
      expect(denied.json.data).toBeNull();
      expect(denied.json.error.code).not.toBeUndefined();
    } finally {
      await warm.stop();
    }
  }, 180_000);

  it('stopping the service ends the real child cleanly', async () => {
    const s = new KriyaService({ executable: KRIYA, cwd: config, env });
    await s.request({ operation: 'capabilities' });
    const pid = s.pid!;
    await s.stop();
    expect(alive(pid)).toBe(false);
  }, 60_000);
});
