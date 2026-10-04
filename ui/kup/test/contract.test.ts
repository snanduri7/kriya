import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { AVAILABILITY_STATES, KUP_ERROR_CODES, validate, validateData, type KupEnvelope } from '../src/index';

const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');
const read = (f: string) => JSON.parse(readFileSync(join(GEN, f), 'utf8')) as KupEnvelope;
const files = () => readdirSync(GEN).filter((f) => f.endsWith('.json') && f !== 'index.json');
const INJECTIONS = ['; rm -rf /', '$(id)', 'a\nb', 'x\0y', '--help', '-n 5', '../../etc/passwd', '', 'run id', 'run"id'];
const SID = '20261004T093000000000Z-f1c70001';

describe('golden fixtures validate against the schema (06 Phase B item 4)', () => {
  it('fixtures exist', () => { expect(existsSync(join(GEN, 'index.json'))).toBe(true); });
  it('every fixture envelope and its payload validate', () => {
    for (const f of files()) {
      const env = read(f);
      const r = validate.envelope(env); expect(r.ok, `${f}: ${!r.ok ? r.errors.join('; ') : ''}`).toBe(true);
      if (env.data !== null) { const d = validateData(env.operation, env.data); expect(d.ok, `${f} data: ${!d.ok ? d.errors.join('; ') : ''}`).toBe(true); }
    }
    for (const code of KUP_ERROR_CODES) if (code !== 'HOST_ERROR') expect(validate.envelope(read(`errors/${code}.json`)).ok).toBe(true); // HOST_ERROR is minted by a host, never by Kriya
  });
  it('round-trips through TypeScript with unknown fields preserved', () => {
    const env = read('history.detail.run-unknown-fields.json');
    const again = JSON.parse(JSON.stringify(env)) as KupEnvelope<Record<string, unknown>>;
    expect(again).toEqual(env);
    expect((again.data as Record<string, unknown>).novel_top_level_section).toBeDefined();
    const events = ((again.data as Record<string, unknown>).run_events as { data: Record<string, unknown>[] }).data;
    expect(events[0]?.severity_v9).toBe('novel');
    expect(Object.keys(events[0] ?? {}).some((k) => k.startsWith('extra_'))).toBe(true);
    expect(validate.runDetail(again.data).ok).toBe(true); // unknown fields do not invalidate an open record
  });
});

describe('run events are Kriya\'s serializer shape (08 review F-3)', () => {
  const serializer = JSON.parse(readFileSync(join(GEN, '..', 'serializer', 'run_events.json'), 'utf8')) as { run_events: Record<string, unknown>[]; serializer_keys: string[] };
  it('every event produced by RunEvent.to_dict through the adapter validates, with exactly the serializer keys', () => {
    expect(serializer.run_events.length).toBe(4);
    for (const e of serializer.run_events) {
      expect(validate.runEvent(e).ok, JSON.stringify(e).slice(0, 80)).toBe(true);
      expect(Object.keys(e).sort()).toEqual([...serializer.serializer_keys].sort());
      expect(typeof e.created_at).toBe('number');
    }
    const detail = read('history.detail.run-serializer-events.json');
    expect(validate.runDetail(detail.data).ok).toBe(true);
    expect(((detail.data as Record<string, unknown>).run_events as { data: unknown[] }).data).toEqual(serializer.run_events);
  });
  it('the invented event/at/payload shape of the first draft is refused: kind and created_at are required', () => {
    expect(validate.runEvent({ event: 'gate.compile', attempt: 1, at: '2026-09-12 10:00:00', payload: { k: 1 } }).ok).toBe(false);
    expect(validate.runEvent({ kind: 'gate.compile', attempt: 1 }).ok).toBe(false);
    expect(validate.runEvent({ kind: 'gate.compile', created_at: '2026-09-12' }).ok).toBe(false);
    expect(validate.runEvent({ kind: 'gate.compile', created_at: 1759561200.25 }).ok).toBe(true);
  });
});

describe('envelope refusals (P-27)', () => {
  const base = read('capabilities.json');
  it('refuses schema_version 2, a missing field, a non-object error and a bad operation', () => {
    expect(validate.envelope({ ...base, schema_version: 2 }).ok).toBe(false);
    const { observed_at: _o, ...missing } = base; expect(validate.envelope(missing).ok).toBe(false);
    expect(validate.envelope({ ...base, error: 'oops' }).ok).toBe(false);
    expect(validate.envelope({ ...base, operation: 'history.delete' }).ok).toBe(false);
    expect(validate.envelope(null).ok).toBe(false);
  });
  it('consistency and error vocabularies are closed', () => {
    const snap = read('snapshot.acquire.json');
    expect(validate.consistency(snap.consistency).ok).toBe(true);
    expect(validate.consistency({ kind: 'sqlite_transaction_snapshot', live_stream: false }).ok).toBe(false);
    expect(validate.consistency({ kind: 'snapshot_copy', live_stream: true }).ok).toBe(false);
    expect(validate.envelope({ ...snap, error: { code: 'MADE_UP', message: 'x' }, data: null }).ok).toBe(false);
    expect(validate.envelope({ ...snap, error: { code: 'STORE_BUSY', message: 'x', database_state: 'wal_busy' }, data: null }).ok).toBe(false);
    expect(validate.envelope({ ...snap, error: { code: 'STORE_BUSY', message: 'x', database_state: 'hot_journal' }, data: null }).ok).toBe(true);
  });
  it('a section needs a known availability; data may be anything including null', () => {
    for (const s of AVAILABILITY_STATES) expect(validate.section({ availability: s, data: null }).ok).toBe(true);
    expect(validate.section({ availability: 'definitely_fine', data: 1 }).ok).toBe(false);
    expect(validate.section({ data: [] }).ok).toBe(false);
  });
  it('a run detail must carry every section', () => {
    const d = read('history.detail.run-diff-2000.json').data as Record<string, unknown>;
    const { comparisons: _c, ...without } = d; expect(validate.runDetail(without).ok).toBe(false);
  });
});

describe('host contract (gate A-2 P-R2) matches the Electron host validators', () => {
  it('accepts the five operations and refuses injection shapes, extra keys and leading dashes', () => {
    expect(validate.kupRequest({ operation: 'capabilities' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'history.list', snapshot_id: SID, limit: 200, cursor: 'WyIyMDI2IiwicnVuIl0=' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: 'run-0001.a:b_c' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'workspace.status', workspace: '/w' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'snapshot.acquire' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'snapshot.acquire', workspace: '/w' }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'snapshot.list', verify: true }).ok).toBe(true);
    expect(validate.kupRequest({ operation: 'snapshot.prune', keep: 0 }).ok).toBe(true);
    // history reads are PINNED: no snapshot_id, or a malformed one, is refused (gate C-2)
    expect(validate.kupRequest({ operation: 'history.list', limit: 5 }).ok).toBe(false);
    expect(validate.kupRequest({ operation: 'history.detail', run_id: 'r1' }).ok).toBe(false);
    expect(validate.kupRequest({ operation: 'history.list', snapshot_id: 'latest', limit: 5 }).ok).toBe(false);
    for (const bad of INJECTIONS) {
      expect(validate.kupRequest({ operation: 'history.detail', snapshot_id: SID, run_id: bad }).ok, `run_id ${JSON.stringify(bad)}`).toBe(false);
      expect(validate.kupRequest({ operation: 'history.list', snapshot_id: SID, cursor: bad }).ok, `cursor ${JSON.stringify(bad)}`).toBe(false);
      expect(validate.kupRequest({ operation: 'history.list', snapshot_id: bad }).ok, `snapshot_id ${JSON.stringify(bad)}`).toBe(false);
    }
    expect(validate.kupRequest({ operation: 'capabilities', extra: 1 }).ok).toBe(false);
    expect(validate.kupRequest({ operation: 'history.list', snapshot_id: SID, limit: 201 }).ok).toBe(false);
    expect(validate.kupRequest({ operation: 'workspace.status', workspace: 'rel' }).ok).toBe(false);
    expect(validate.openInIdeRequest({ path: '/w/a.py', line: 7 }).ok).toBe(true);
    expect(validate.openInIdeRequest({ path: '/w/a.py', line: 0 }).ok).toBe(false);
    expect(validate.openInIdeRequest({ path: '/w/a.py', cmd: 'x' }).ok).toBe(false);
    expect(validate.hostInfo({ kind: 'electron', hostVersion: '0.0.1', fixtureMode: true }).ok).toBe(true);
    expect(validate.hostInfo({ kind: 'electron', hostVersion: '0.0.1', fixtureMode: true, extra: 1 }).ok).toBe(false);
  });
});
