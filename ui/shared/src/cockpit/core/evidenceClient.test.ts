import { describe, expect, it } from 'vitest';
import { FIXTURE_SNAPSHOT_ID as SNAP, createFixtureProvider } from '../providers/fixture';
import { loadRunEvidence, type ProviderResult, type UIProvider } from '.';

const over = (patch: Partial<UIProvider>): UIProvider => { const base = createFixtureProvider(); return Object.assign(Object.create(base), patch) as UIProvider; };
const err = (code: string, message = 'm', extra: Record<string, unknown> = {}): ProviderResult<never> => ({ ok: false, error: { code, message, extra } });

describe('loadRunEvidence', () => {
  it('needs a pinned snapshot: without one the recorded link cannot be read, and nothing is requested', async () => {
    let asked = false;
    const p = over({ evidenceManifest: async () => { asked = true; return err('x'); } });
    expect(await loadRunEvidence(p, 'fx-greenfield-success', null)).toMatchObject({ state: 'unavailable', reason: 'unknown_run' });
    expect(asked).toBe(false);
  });

  it('every evidence read after the manifest uses the evidence run id Kriya resolved, never the trace id', async () => {
    const ids: string[] = [];
    const base = createFixtureProvider();
    const p = over({ evidenceVerify: (id, s) => { ids.push(id); return base.evidenceVerify(id, s); }, evidenceRecords: (id, q, s) => { ids.push(id); return base.evidenceRecords(id, q, s); } });
    const a = await loadRunEvidence(p, 'fx-greenfield-success', SNAP);
    expect(a.state === 'ready' && [a.data.runId, a.data.evidenceRunId]).toEqual(['fx-greenfield-success', 'ev-fx-greenfield-success']);
    expect(new Set(ids)).toEqual(new Set(['ev-fx-greenfield-success']));
  });

  it('a sealed verified store is ready with every record; the manifest is read first, verify second, records last', async () => {
    const calls: string[] = [];
    const base = createFixtureProvider();
    const p = over({ evidenceManifest: (...a) => { calls.push('manifest'); return base.evidenceManifest(...a); }, evidenceVerify: (...a) => { calls.push('verify'); return base.evidenceVerify(...a); }, evidenceRecords: (...a) => { calls.push('records'); return base.evidenceRecords(...a); } });
    const a = await loadRunEvidence(p, 'fx-greenfield-success', SNAP);
    expect(a.state).toBe('ready');
    expect(calls).toEqual(['manifest', 'verify', 'records']);
  });

  it('a store that fails verification is never read: no records are requested', async () => {
    let asked = false;
    const p = over({ evidenceVerify: async () => err('EVIDENCE_STORE_UNREADABLE', 'denied'), evidenceRecords: async () => { asked = true; return err('x'); } });
    const a = await loadRunEvidence(p, 'fx-greenfield-success', SNAP);
    expect(a).toMatchObject({ state: 'error', code: 'EVIDENCE_STORE_UNREADABLE' });
    expect(asked).toBe(false);
  });

  it('records refused for a broken chain are "store does not verify", naming the first bad record', async () => {
    const p = over({ evidenceRecords: async () => err('EVIDENCE_VERIFY_FAILED', 'did not verify (CHAIN_BROKEN)', { status: 'CHAIN_BROKEN', broken_seq: 13 }) });
    const a = await loadRunEvidence(p, 'fx-greenfield-success', SNAP);
    expect(a).toMatchObject({ state: 'unavailable', reason: 'unverified' });
    expect(a.state === 'unavailable' && a.detail).toMatch(/first bad record 13/);
  });

  it('a missing store is its own state, with the derived reason from the manifest', async () => {
    expect(await loadRunEvidence(createFixtureProvider(), 'fx-pruned', SNAP)).toMatchObject({ state: 'unavailable', reason: 'pruned' });
    expect(await loadRunEvidence(createFixtureProvider(), 'not-a-run', SNAP)).toMatchObject({ state: 'unavailable', reason: 'store_absent' });
    expect(await loadRunEvidence(over({ evidenceRecords: async () => err('EVIDENCE_STORE_NOT_FOUND', 'gone') }), 'fx-greenfield-success', SNAP)).toMatchObject({ state: 'unavailable', reason: 'store_absent' });
  });

  it('an unsealed store is degraded with the reason; a transport error is an error, never fixture data', async () => {
    const a = await loadRunEvidence(createFixtureProvider(), 'fx-unsealed', SNAP);
    expect(a.state).toBe('degraded');
    expect(a.state === 'degraded' && a.reasons[0]).toMatch(/unsealed/);
    expect(await loadRunEvidence(over({ evidenceManifest: async () => err('HOST_ERROR', 'spawn failed') }), 'fx-greenfield-success', SNAP)).toEqual({ state: 'error', code: 'HOST_ERROR', message: 'spawn failed' });
  });

  it('follows the cursor across pages and stops with an error rather than loading a run half-way', async () => {
    const base = createFixtureProvider();
    const seen: Array<string | undefined> = [];
    const paged = over({ evidenceRecords: (id, q, s) => { seen.push(q?.cursor); return base.evidenceRecords(id, { ...q, limit: 5 }, s); } });
    const a = await loadRunEvidence(paged, 'fx-greenfield-compile-retry', SNAP);
    const seqs = a.state === 'ready' ? a.data.records.map((r) => r.seq) : [];
    expect(seqs).toEqual(Array.from({ length: seqs.length }, (_, i) => i + 1));
    expect(seqs.length).toBeGreaterThan(5);
    expect(seen.length).toBeGreaterThan(1);
    const endless = over({ evidenceRecords: async (id, q, s) => { const r = await base.evidenceRecords(id, { ...q, limit: 1 }, s); return r.ok ? { ...r, data: { ...r.data, next_cursor: 'eyJsIjowfQ' } } : r; } });
    expect(await loadRunEvidence(endless, 'fx-greenfield-success', SNAP)).toMatchObject({ state: 'error', code: 'PAGE_LIMIT' });
  });
});
