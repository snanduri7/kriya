/**
 * The production read path against REAL Kriya: KriyaProvider -> host validators -> argv builder -> the real process runner ->
 * the real `kriya` CLI -> real attempt-evidence stores written by the recorder and a real trace database. Nothing is stubbed
 * between the provider and Kriya. Needs the repository's .venv (skipped, loudly, when it is absent); never calls a model.
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import type { HostAdapter } from '@kriya-ui/shared';
import { loadRunEvidence } from '@kriya-ui/shared/src/cockpit/core/evidenceClient';
import { changeRows, diagnoses, gateRows } from '@kriya-ui/shared/src/cockpit/core/selectors';
import type { UIProvider } from '@kriya-ui/shared/src/cockpit/core/provider';
import { createKriyaProvider } from '@kriya-ui/shared/src/cockpit/providers/KriyaProvider';
import { buildKriyaChildEnv } from '../src/main/child_env';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { validateKupRequest } from '../src/main/ipc_contract';
import { runKriya } from '../src/main/kriya_process';
import { KriyaService } from '../src/main/kriya_service';
import type { KupRequest } from '../src/main/ipc_contract';
import { SEED } from './fixtures/real_seed';

const ROOT = join(__dirname, '..', '..', '..');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const available = existsSync(KRIYA) && existsSync(PYTHON);

type Transport = 'spawn' | 'service';
type Outcome = { kind: 'json'; json: unknown } | { kind: 'error'; code?: string; message?: string };
const services = new Map<string, KriyaService>();
/** One request through either transport: the one-shot command, or the persistent `kup-stdio` child (one per environment and working directory). */
async function run(transport: Transport, request: KupRequest, env: Record<string, string>, cwd: string): Promise<Outcome> {
  if (transport === 'spawn') return runKriya({ executable: KRIYA, argv: buildKriyaArgv(request), env, cwd }) as Promise<Outcome>;
  const key = JSON.stringify([cwd, Object.entries(env).sort()]);
  if (!services.has(key)) services.set(key, new KriyaService({ executable: KRIYA, cwd, env }));
  return services.get(key)!.request(request);
}


describe.skipIf(!available).each(['spawn', 'service'] as const)('KriyaProvider against the real Kriya CLI and real evidence stores (%s transport)', (transport) => {
  afterAll(async () => { await Promise.all([...services.values()].map((x) => x.stop())); services.clear(); });
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
      const out = await run(transport, v.value, env.env, config);
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
    expect(a.data.records.map((r) => r.kind)).toEqual(['run.opened', 'attempt.opened', 'model.result', 'mirror.gate_outcome', 'candidate.change', 'diagnosis', 'attempt.concluded', 'run.closed']);
    expect(a.data.verify).toMatchObject({ status: 'VERIFIED', sealed: true });
    expect(a.data.provenance.records?.consistency?.kind).toBe('sealed_store');
    expect(a.data.manifest.manifest).toMatchObject({ capture: 'full', base_revision: 'b2b5fc411394e307' });
  }, 120_000);

  it('unsealed store: degraded, says unsealed, reads the complete records, never claims the run is active', async () => {
    const a = await load('t-open');
    expect(a.state).toBe('degraded');
    expect(a.state === 'degraded' && a.reasons.join(' ')).toMatch(/unsealed/);
    expect(a.state === 'degraded' && a.provenance.consistency?.kind).toBe('live_observation');
    expect(a.state === 'degraded' && a.data.records).toHaveLength(8);
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

  it('Validation and WHY inputs are the real recorded facts (selectors over the real records)', async () => {
    const a = await load('t-ok');
    if (a.state !== 'ready') throw new Error('not ready');
    expect(gateRows(a.data.records).map((g) => [g.type, g.origin, g.success])).toEqual([['compile', 'mirrored', true]]);
    expect(diagnoses(a.data.records)[0]).toMatchObject({ reasonCode: 'COMPILE_FAILED', evidenceClass: 'DERIVED_DETERMINISTIC', attributionTier: 'locator', provenance: 'OBSERVED', messageKept: true });
    expect(changeRows(a.data.records)[0]).toMatchObject({ kind: 'MODIFIED', path: 'src/A.java', kept: ['after', 'before', 'diff'] });
  }, 120_000);

  it('content is refused by default through the real CLI (Kriya policy off), and unclassified keys never reach a store', async () => {
    const caps = await provider.capabilities();
    expect(caps.ok && (caps.data.features as Record<string, unknown>).evidence_content).toBe(false);
    const a = await load('t-ok');
    const seq = a.state === 'ready' ? changeRows(a.data.records)[0]!.seq : 0;
    const denied = await provider.evidenceChangeContent('ev-ok', { seq, part: 'diff' });
    expect(!denied.ok && denied.error).toMatchObject({ code: 'EVIDENCE_CONTENT_POLICY', extra: { content_class: 'candidate_content', reason: 'content_visibility_off' } });
    const unclassified = await provider.evidenceChangeContent('ev-ok', { seq, part: 'analysis_model_claimed' });
    expect(!unclassified.ok && unclassified.error.extra).toMatchObject({ content_class: 'unclassified' });
  }, 120_000);

  describe('with Kriya content_visibility ON (an approved SECURITY_AUTHORITY configuration in an isolated HOME)', () => {
    let onProvider: UIProvider;
    beforeAll(() => {
      const home = join(state, '..', 'home'); const onConfig = join(state, '..', 'config-on');
      mkdirSync(home); mkdirSync(onConfig);
      writeFileSync(join(onConfig, 'kriya.yaml'), 'evidence:\n  attempt_recorder:\n    content_visibility: "on"\n');
      const env = { PATH: process.env.PATH, HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1' };
      execFileSync(KRIYA, ['authority', 'approve', '--confirm'], { env, cwd: onConfig });
      onProvider = createKriyaProvider({ ...host(), async query(raw) {
        const v = validateKupRequest(raw);
        const op = String((raw as { operation?: unknown }).operation ?? 'unknown');
        if (!v.ok) return { schema_version: 1, operation: op, request_id: 'h', observed_at: '', source: null, consistency: null, data: null, error: { code: 'INVALID_REQUEST', message: v.message, database_state: null } } as never;
        const out = await run(transport, v.value, env as Record<string, string>, onConfig);
        return (out.kind === 'json' ? out.json : { schema_version: 1, operation: op, request_id: 'h', observed_at: '', source: null, consistency: null, data: null, error: { code: out.code ?? 'HOST_ERROR', message: out.message ?? '', database_state: null } }) as never;
      } });
    }, 60_000);

    it('capabilities advertise content only now, and only the two classes Kriya classifies', async () => {
      const caps = await onProvider.capabilities();
      expect(caps.ok && (caps.data.features as Record<string, unknown>)).toMatchObject({ evidence_content: true, evidence_content_classes: ['candidate_content', 'model_input'] });
    }, 60_000);

    it('the authorized diff, before and after are the recorded bytes, digest-checked, with provenance', async () => {
      const a = await loadRunEvidence(onProvider, 't-ok', snapshotId);
      const seq = a.state === 'ready' ? changeRows(a.data.records)[0]!.seq : 0;
      const diff = await onProvider.evidenceChangeContent('ev-ok', { seq, part: 'diff' });
      expect(diff.ok && diff.data.content).toBe('--- a/src/A.java\n+++ b/src/A.java\n@@ -1 +1,2 @@\n class A {}\n+// added\n');
      expect(diff.ok && [diff.data.content_class, diff.data.digest_ok, diff.data.path, diff.data.attempt]).toEqual(['candidate_content', true, 'src/A.java', 1]);
      expect(diff.ok && diff.provenance.consistency?.kind).toBe('sealed_store');
      const after = await onProvider.evidenceChangeContent('ev-ok', { seq, part: 'after' });
      expect(after.ok && after.data.content).toBe('class A {}\n// added\n');
    }, 120_000);

    it('even with content on: an unclassified key, a non-candidate record and a digest-only store are all typed refusals', async () => {
      const a = await loadRunEvidence(onProvider, 't-ok', snapshotId);
      const seq = a.state === 'ready' ? changeRows(a.data.records)[0]!.seq : 0;
      const unclassified = await onProvider.evidenceChangeContent('ev-ok', { seq, part: 'message' });
      expect(!unclassified.ok && unclassified.error.extra).toMatchObject({ content_class: 'unclassified', reason: 'unclassified_key' });
      const notCandidate = await onProvider.evidenceChangeContent('ev-ok', { seq: 1, part: 'diff' });
      expect(!notCandidate.ok && notCandidate.error.code).toBe('INVALID_REQUEST');
      const d = await loadRunEvidence(onProvider, 't-digest', snapshotId);
      const dseq = d.state === 'ready' ? changeRows(d.data.records)[0]!.seq : 0;
      const digestOnly = await onProvider.evidenceChangeContent('ev-digest', { seq: dseq, part: 'diff' });
      expect(!digestOnly.ok && digestOnly.error).toMatchObject({ code: 'EVIDENCE_CONTENT_UNAVAILABLE', extra: { reason: 'digest_only' } });
      const broken = await onProvider.evidenceChangeContent('ev-broken', { seq: 5, part: 'diff' });
      expect(!broken.ok && broken.error).toMatchObject({ code: 'EVIDENCE_VERIFY_FAILED', extra: { status: 'CHAIN_BROKEN' } });
    }, 120_000);
  });

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
