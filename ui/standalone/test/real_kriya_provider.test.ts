/**
 * The production read path against REAL Kriya: KriyaProvider -> host validators -> argv builder -> the real process runner ->
 * the real `kriya` CLI -> real attempt-evidence stores written by the recorder and a real trace database. Nothing is stubbed
 * between the provider and Kriya. Needs the repository's .venv (skipped, loudly, when it is absent); never calls a model.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { HostAdapter } from '@kriya-ui/shared';
import { loadRunEvidence } from '@kriya-ui/shared/src/cockpit/core/evidenceClient';
import type { UIProvider } from '@kriya-ui/shared/src/cockpit/core/provider';
import { createKriyaProvider } from '@kriya-ui/shared/src/cockpit/providers/KriyaProvider';
import { buildKriyaChildEnv } from '../src/main/child_env';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { validateKupRequest } from '../src/main/ipc_contract';
import { runKriya } from '../src/main/kriya_process';

const ROOT = join(__dirname, '..', '..', '..');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const available = existsSync(KRIYA) && existsSync(PYTHON);

const SEED = `
import json, os
from kriya.core.attempt_evidence.writer import AttemptEvidenceWriter
from kriya.core.trace import TraceLogger
state = os.environ['KRIYA_STATE_DIR']
ident = {'unit_id': 'direct', 'unit_kind': 'direct', 'invocation_seq': 1, 'phase': 'attempt', 'attempt_number': 1}
def store(rid, capture='full', seal=True, tamper=False):
    w = AttemptEvidenceWriter(state, rid, capture=capture, manifest={'workspace_identity': 'ws', 'base_revision': 'b2b5fc411394e307', 'base_tree_hash': None})
    w.append('run.opened', {'base_revision': 'b2b5fc411394e307'})
    w.append('attempt.opened', {'mode': 'direct'}, identity=ident)
    w.append('model.result', {'finish_reason': 'stop'}, identity={**ident, 'role': 'developer', 'call_seq': 1})
    w.append('mirror.gate_outcome', {'attempt': 1, 'index': 0, 'success': True, 'type': 'compile'}, identity=ident, content={'outcome': 'mirrored'})
    w.append('attempt.concluded', {'succeeded': True}, identity=ident)
    w.append('run.closed', {'terminal_status': 'SUCCESS'})
    if seal: w.seal('run.closed')
    if tamper:
        p = os.path.join(state, 'attempt-evidence', rid, 'records.jsonl')
        lines = open(p, 'rb').read().split(b'\\n')
        rec = json.loads(lines[2]); rec['payload'] = {'tampered': True}
        lines[2] = json.dumps(rec, sort_keys=True, separators=(',', ':')).encode()
        open(p, 'wb').write(b'\\n'.join(lines))
def event(rid, status, **extra):
    d = {'status': status, 'capture': 'full', **extra}
    if rid: d['run_id'] = rid; d['path'] = os.path.join(state, 'attempt-evidence', rid)
    return [{'kind': 'evidence.attempt_store', 'attempt': 0, 'source': 'workflow', 'authority': 'auxiliary', 'message': 'attempt evidence store', 'failure_type': None, 'operation': None, 'details': d, 'created_at': 1.0}]
store('ev-ok'); store('ev-open', seal=False); store('ev-digest', capture='digest_only'); store('ev-broken', tamper=True)
log = TraceLogger(os.path.join(state, 'traces.db'))
rows = [('t-ok', 'success', event('ev-ok', 'OPEN')), ('t-open', 'in_progress', event('ev-open', 'OPEN')), ('t-digest', 'success', event('ev-digest', 'OPEN')),
        ('t-broken', 'success', event('ev-broken', 'OPEN')), ('t-pruned', 'success', event('ev-gone', 'CLOSED')), ('t-old', 'success', []),
        ('t-off', 'success', event(None, 'DISABLED', reason='capture off'))]
for rid, status, events in rows:
    log.log_run(run_id=rid, goal='real-store goal ' + rid, duration_sec=1.234, attempts=1, status=status, files_modified=[], run_events=events)
`;

describe.skipIf(!available)('KriyaProvider against the real Kriya CLI and real evidence stores', () => {
  let state = '';
  let config = '';
  let provider: UIProvider;
  let snapshotId = '';

  const host = (): HostAdapter => ({
    async query(raw) {
      const v = validateKupRequest(raw);
      const op = String((raw as { operation?: unknown }).operation ?? 'unknown');
      const error = (code: string, message: string) => ({ schema_version: 1, operation: op, request_id: 'host', observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code, message, database_state: null } });
      if (!v.ok) return error('INVALID_REQUEST', v.message) as never;
      const env = buildKriyaChildEnv({ HOME: process.env.HOME, KRIYA_STATE_DIR: state });
      if (!env.ok) return error('HOST_ERROR', env.message) as never;
      const out = await runKriya({ executable: KRIYA, argv: buildKriyaArgv(v.value), env: env.env, cwd: config });
      return (out.kind === 'json' ? out.json : error(out.code ?? 'HOST_ERROR', out.message ?? 'unknown')) as never;
    },
    async openInIde() { return { ok: false, editor: 'vscode', verified: false, message: '' }; }, async copyToClipboard() { /* none */ },
    async getSetting() { return undefined; }, async setSetting() { /* none */ },
    hostInfo: () => ({ kind: 'test', hostVersion: '0', fixtureMode: false, configDirectory: config, configDirectorySource: 'setting', configDirectoryProblem: null }),
  });

  beforeAll(async () => {
    const base = mkdtempSync(join(tmpdir(), 'kriya-gui-real-'));
    state = join(base, 'state'); config = join(base, 'config');
    mkdirSync(state); mkdirSync(config);
    execFileSync(PYTHON, ['-c', SEED], { env: { PATH: process.env.PATH, HOME: process.env.HOME, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1' }, cwd: config });
    provider = createKriyaProvider(host());
    const acquired = await provider.acquireSnapshot();
    if (!acquired.ok) throw new Error(`snapshot not acquired: ${acquired.error.code} ${acquired.error.message}`);
    snapshotId = acquired.data.snapshot_id;
  }, 120_000);
  afterAll(() => { if (state) rmSync(join(state, '..'), { recursive: true, force: true }); });

  const load = (traceId: string) => loadRunEvidence(provider, traceId, snapshotId);

  it('is the production provider: kind kriya, real capabilities for set 1.1', async () => {
    expect(provider.kind).toBe('kriya');
    const caps = await provider.capabilities();
    expect(caps.ok && (caps.data.features as Record<string, unknown>).capability_set).toBe('1.1');
    expect(caps.ok && (caps.data.features as Record<string, unknown>).evidence_content).toBe(false);
  }, 60_000);

  it('Runs: the real history lists the trace rows with their exact stored status literals', async () => {
    const list = await provider.listRuns(snapshotId, { limit: 200 });
    expect(list.ok && list.data.runs.map((r) => [r.run_id, r.status]).sort()).toEqual([['t-broken', 'success'], ['t-digest', 'success'], ['t-ok', 'success'], ['t-off', 'success'], ['t-old', 'success'], ['t-open', 'in_progress'], ['t-pruned', 'success']].sort());
    expect(list.ok && list.provenance.consistency?.kind).toBe('snapshot_copy');
  }, 60_000);

  it('verified sealed store: the trace id resolves to a DIFFERENT evidence id; records, seal, capture and baseline are the recorded ones', async () => {
    const a = await load('t-ok');
    expect(a.state).toBe('ready');
    if (a.state !== 'ready') return;
    expect([a.data.runId, a.data.evidenceRunId]).toEqual(['t-ok', 'ev-ok']);
    expect(a.data.records.map((r) => r.kind)).toEqual(['run.opened', 'attempt.opened', 'model.result', 'mirror.gate_outcome', 'attempt.concluded', 'run.closed']);
    expect(a.data.verify).toMatchObject({ status: 'VERIFIED', sealed: true });
    expect(a.data.provenance.records?.consistency?.kind).toBe('sealed_store');
    expect(a.data.manifest.manifest).toMatchObject({ capture: 'full', base_revision: 'b2b5fc411394e307' });
  }, 120_000);

  it('unsealed store: degraded, says unsealed, reads the complete records, never claims the run is active', async () => {
    const a = await load('t-open');
    expect(a.state).toBe('degraded');
    expect(a.state === 'degraded' && a.reasons.join(' ')).toMatch(/unsealed/);
    expect(a.state === 'degraded' && a.provenance.consistency?.kind).toBe('live_observation');
    expect(a.state === 'degraded' && a.data.records).toHaveLength(6);
  }, 120_000);

  it('digest-only store: readable, capture recorded as digest_only', async () => {
    const a = await load('t-digest');
    expect(a.state === 'ready' && a.data.manifest.manifest?.capture).toBe('digest_only');
  }, 120_000);

  it('pruned (event names a store that is gone), pre-recorder (no event) and capture-off are three different typed states', async () => {
    expect(await load('t-pruned')).toMatchObject({ state: 'unavailable', reason: 'pruned' });
    expect(await load('t-old')).toMatchObject({ state: 'unavailable', reason: 'pre_recorder' });
    expect(await load('t-off')).toMatchObject({ state: 'unavailable', reason: 'capture_off' });
  }, 120_000);

  it('a tampered chain is never read: store does not verify, naming the first bad record', async () => {
    const a = await load('t-broken');
    expect(a).toMatchObject({ state: 'unavailable', reason: 'unverified' });
    expect(a.state === 'unavailable' && a.detail).toMatch(/first bad record 4/);
  }, 120_000);

  it('typed errors cross the real path intact: unpublished snapshot, unknown run, refused request', async () => {
    const snap = await provider.verifySnapshot('20200101T000000000000Z-00000000');
    expect(!snap.ok && snap.error.code).toBe('SNAPSHOT_UNAVAILABLE');
    const detail = await provider.runDetail(snapshotId, 'no-such-run');
    expect(!detail.ok && detail.error.code).toBe('INVALID_REQUEST');
    const hidden = await provider.evidenceVerify('.hidden');
    expect(!hidden.ok && hidden.error.code).toBe('INVALID_REQUEST');
    const noStore = await provider.evidenceRecords('ev-gone');
    expect(!noStore.ok && noStore.error.code).toBe('EVIDENCE_STORE_NOT_FOUND');
    const broken = await provider.evidenceRecords('ev-broken');
    expect(!broken.ok && broken.error).toMatchObject({ code: 'EVIDENCE_VERIFY_FAILED', extra: { status: 'CHAIN_BROKEN' } });
  }, 120_000);

  it('an unusable executable is HOST_ERROR, never fixture data', async () => {
    const dead: HostAdapter = { ...host(), async query(raw) { const out = await runKriya({ executable: '/nonexistent/kriya', argv: buildKriyaArgv(validateKupRequest(raw) as never as { ok: true; value: never }['value']), cwd: config }); return { schema_version: 1, operation: 'capabilities', request_id: 'h', observed_at: '', source: null, consistency: null, data: null, error: { code: out.code ?? 'HOST_ERROR', message: out.message ?? '' } } as never; } };
    const r = await createKriyaProvider(dead).capabilities();
    expect(!r.ok && r.error.code).toBe('HOST_ERROR');
  }, 60_000);
});
