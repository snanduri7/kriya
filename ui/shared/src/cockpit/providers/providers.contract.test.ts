/** One contract, two providers: KriyaProvider and FixtureProvider must pass exactly the same suite (they differ only in the host behind them). */
import { describe, expect, it } from 'vitest';
import type { HostAdapter } from '../../host/HostAdapter';
import type { KupEnvelope, KupRequest } from '../../model/kup';
import type { UIProvider } from '../core/provider';
import { createKriyaProvider } from './KriyaProvider';
import { FIXTURE_SNAPSHOT_ID, FixtureHost, SCENARIOS, createFixtureProvider } from './fixture';

const makers: Array<[string, 'kriya' | 'fixture', () => UIProvider]> = [
  ['KriyaProvider (over the fixture wire host)', 'kriya', () => createKriyaProvider(new FixtureHost())],
  ['FixtureProvider', 'fixture', () => createFixtureProvider()],
];

describe.each(makers)('UIProvider contract: %s', (_name, kind, make) => {
  it('declares its kind', () => { expect(make().kind).toBe(kind); });

  it('capabilities: capability set 1.1, nothing advertised that does not exist', async () => {
    const r = await make().capabilities();
    expect(r.ok).toBe(true);
    if (!r.ok) return;
    const f = r.data.features as Record<string, unknown>;
    expect(f.capability_set).toBe('1.1');
    expect(f.evidence).toBe(true);
    expect(f.evidence_content).toBe(false); // the default is the production default: content is not authorized
    for (const k of ['qualification', 'authority', 'knowledge', 'tools', 'experiments', 'live', 'actions']) expect(f[k]).toBe(false);
    expect(r.provenance).toMatchObject({ operation: 'capabilities', schemaVersion: 1 });
  });

  it('snapshots: listed, verified at pin; an unpublished id is a typed error, never a substitute', async () => {
    const p = make();
    const list = await p.listSnapshots();
    expect(list.ok && list.data.snapshots.map((s) => s.snapshot_id)).toEqual([FIXTURE_SNAPSHOT_ID]);
    const ok = await p.verifySnapshot(FIXTURE_SNAPSHOT_ID);
    expect(ok.ok && ok.data.snapshot_id).toBe(FIXTURE_SNAPSHOT_ID);
    const bad = await p.verifySnapshot('20200101T000000000000Z-00000000');
    expect(!bad.ok && bad.error.code).toBe('SNAPSHOT_UNAVAILABLE');
    expect(!bad.ok && bad.error.extra.snapshot_id).toBe('20200101T000000000000Z-00000000');
  });

  it('history: every scenario is listed with its provenance; an unknown run is a typed error', async () => {
    const p = make();
    const list = await p.listRuns(FIXTURE_SNAPSHOT_ID);
    expect(list.ok).toBe(true);
    if (!list.ok) return;
    expect(list.data.runs.map((r) => r.run_id).sort()).toEqual(SCENARIOS.map((s) => s.id).sort());
    expect(list.provenance.consistency?.kind).toBe('snapshot_copy');
    const detail = await p.runDetail(FIXTURE_SNAPSHOT_ID, 'fx-brownfield-modify');
    expect(detail.ok && detail.data.run.goal).toMatch(/whitespace/);
    const missing = await p.runDetail(FIXTURE_SNAPSHOT_ID, 'nope');
    expect(!missing.ok && missing.error.code).toBe('INVALID_REQUEST');
  });

  it('evidence manifest: present, absent with a derived reason, never a generic "no data"', async () => {
    const p = make();
    const present = await p.evidenceManifest('fx-greenfield-success', { snapshotId: FIXTURE_SNAPSHOT_ID, fromTrace: true });
    expect(present.ok && present.data.store).toBe('present');
    expect(present.ok && present.data.run_id).toBe('ev-fx-greenfield-success'); // the evidence id, resolved from the trace row's recorded event
    expect(present.ok && present.data.link).toMatchObject({ trace_run_id: 'fx-greenfield-success', evidence_run_id: 'ev-fx-greenfield-success' });
    const direct = await p.evidenceManifest('ev-fx-greenfield-success');
    expect(direct.ok && direct.data.store).toBe('present');
    expect(present.ok && present.data.manifest?.capture).toBe('full');
    const pruned = await p.evidenceManifest('fx-pruned', { snapshotId: FIXTURE_SNAPSHOT_ID, fromTrace: true });
    expect(pruned.ok && pruned.data.absence?.reason).toBe('pruned');
    expect(pruned.ok && pruned.data.run_id).toBe('ev-fx-pruned');
    for (const [id, reason] of [['fx-pre-recorder', 'pre_recorder'], ['fx-capture-off', 'capture_off']] as const) {
      const m = await p.evidenceManifest(id, { snapshotId: FIXTURE_SNAPSHOT_ID, fromTrace: true });
      expect(m.ok && [m.data.absence?.reason, m.data.run_id]).toEqual([reason, null]);
    }
    const noSnapshot = await p.evidenceManifest('fx-pruned', { fromTrace: true });
    expect(!noSnapshot.ok && noSnapshot.error.code).toBe('INVALID_REQUEST');
    expect(pruned.ok && pruned.data.absence?.derived_from).toContain('evidence.attempt_store');
  });

  it('evidence verify: sealed_store consistency only for VERIFIED', async () => {
    const p = make();
    const sealed = await p.evidenceVerify('ev-fx-greenfield-success');
    expect(sealed.ok && sealed.data.status).toBe('VERIFIED');
    expect(sealed.ok && sealed.provenance.consistency?.kind).toBe('sealed_store');
    const open = await p.evidenceVerify('ev-fx-unsealed');
    expect(open.ok && open.data.status).toBe('UNSEALED');
    expect(open.ok && open.provenance.consistency?.kind).toBe('live_observation');
    const none = await p.evidenceVerify('ev-fx-pruned');
    expect(none.ok && none.data.status).toBe('NOT_FOUND');
  });

  it('evidence records: filters, paging by cursor without loss, typed error for a missing store', async () => {
    const p = make();
    const all = await p.evidenceRecords('ev-fx-greenfield-compile-retry', { limit: 1000 });
    expect(all.ok).toBe(true);
    if (!all.ok) return;
    const total = all.data.records.length;
    const seen: number[] = [];
    let cursor: string | undefined;
    for (let i = 0; i < 50; i++) {
      const page = await p.evidenceRecords('ev-fx-greenfield-compile-retry', { limit: 4, cursor });
      expect(page.ok).toBe(true);
      if (!page.ok) return;
      seen.push(...page.data.records.map((r) => r.seq));
      if (!page.data.next_cursor) break;
      cursor = page.data.next_cursor;
    }
    expect(seen).toEqual(Array.from({ length: total }, (_, i) => i + 1));
    const gates = await p.evidenceRecords('ev-fx-greenfield-compile-retry', { kind: ['gate.result'], attempt: 1 });
    expect(gates.ok && gates.data.records.every((r) => r.kind === 'gate.result' && r.attempt_number === 1)).toBe(true);
    expect(gates.ok && gates.data.filters_applied).toEqual({ attempt: 1, kind: ['gate.result'] });
    const missing = await p.evidenceRecords('ev-fx-pruned', {});
    expect(!missing.ok && missing.error.code).toBe('EVIDENCE_STORE_NOT_FOUND');
  });

  it('settings round-trip through the host', async () => {
    const p = make();
    await p.setSetting('editor', 'intellij');
    expect(await p.getSetting('editor')).toBe('intellij');
  });
});

describe('KriyaProvider failure semantics: typed errors, never fixture data', () => {
  const hostWith = (query: (r: KupRequest) => Promise<unknown>): HostAdapter => ({ ...new FixtureHost(), query: query as HostAdapter['query'], getSetting: async () => undefined, setSetting: async () => undefined, hostInfo: () => new FixtureHost().hostInfo(), openInIde: async () => ({ ok: false, editor: 'vscode', verified: false, message: '' }), copyToClipboard: async () => undefined });

  it('a transport failure is HOST_ERROR and carries no data', async () => {
    const r = await createKriyaProvider(hostWith(async () => { throw new Error('spawn failed'); })).evidenceVerify('x');
    expect(r).toEqual({ ok: false, error: { code: 'HOST_ERROR', message: 'spawn failed', extra: {} } });
  });

  it('a response that is not a KUP envelope is INVALID_RESPONSE', async () => {
    for (const garbage of ['not json', null, {}, { schema_version: 2 }]) {
      const r = await createKriyaProvider(hostWith(async () => garbage)).capabilities();
      expect(!r.ok && r.error.code).toBe('INVALID_RESPONSE');
    }
  });

  it('an answer for another operation, or a payload that violates its schema, is INVALID_RESPONSE', async () => {
    const real = new FixtureHost();
    const wrongOp = await createKriyaProvider(hostWith(async (req) => ({ ...(await real.query({ operation: 'capabilities' })), operation: req.operation === 'capabilities' ? 'snapshot.list' : req.operation }))).capabilities();
    expect(!wrongOp.ok && wrongOp.error.code).toBe('INVALID_RESPONSE');
    const badData = await createKriyaProvider(hostWith(async (req) => ({ ...(await real.query(req)) as KupEnvelope, data: { run_id: 'x' } }))).evidenceVerify('ev-fx-greenfield-success');
    expect(!badData.ok && badData.error.code).toBe('INVALID_RESPONSE');
  });

  it('Kriya typed errors keep their extras verbatim', async () => {
    const real = new FixtureHost();
    const r = await createKriyaProvider(hostWith(async () => ({ ...(await real.query({ operation: 'capabilities' })), operation: 'evidence.records', data: null, source: null, consistency: null, error: { code: 'EVIDENCE_VERIFY_FAILED', message: 'x', database_state: null, status: 'CHAIN_BROKEN', broken_seq: 5 } }))).evidenceRecords('r');
    expect(!r.ok && r.error).toMatchObject({ code: 'EVIDENCE_VERIFY_FAILED', extra: { status: 'CHAIN_BROKEN', broken_seq: 5 } });
  });

  it('unavailable evidence is an error result: the provider never substitutes fixture records', async () => {
    const real = new FixtureHost();
    const down = hostWith(async (req) => { if (req.operation === 'evidence.records') throw new Error('store unreadable'); return real.query(req); });
    const r = await createKriyaProvider(down).evidenceRecords('fx-greenfield-success');
    expect(r.ok).toBe(false);
  });
});
