import type { HostAdapter, HostSettings, OpenInIdeRequest, OpenInIdeResult } from '../src/host/HostAdapter';
import type { KupEnvelope, KupRequest } from '../src/model/kup';

/** In-memory HostAdapter for tests: answers from a map of canned envelopes. */
export class FakeHost implements HostAdapter {
  calls: KupRequest[] = [];
  opened: OpenInIdeRequest[] = [];
  clipboard: string[] = [];
  settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace' };
  constructor(private responses: (req: KupRequest) => KupEnvelope | Promise<KupEnvelope> | unknown) {}
  async query(request: KupRequest): Promise<KupEnvelope> { this.calls.push(request); return (await this.responses(request)) as KupEnvelope; }
  async openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { this.opened.push(request); return { ok: true, editor: 'vscode', verified: true, message: `fake open ${request.path}` }; }
  async copyToClipboard(text: string) { this.clipboard.push(text); }
  async getSetting<K extends keyof HostSettings>(key: K) { return this.settings[key] as HostSettings[K] | undefined; }
  async setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]) { this.settings[key] = value; }
  hostInfo() { return { kind: 'fake-test', hostVersion: '0', fixtureMode: true }; }
}

export const SNAP_ID = '20261004T093000000000Z-f1c70001';
export const SNAP_ID_OLD = '20261004T083000000000Z-f1c70000';
const META = { size: 2969600, mtime_ns: 1759561200000000000, inode: 7781234, wal_size: null, shm_size: null, journal_size: null };
export const snapshotSummary = (id = SNAP_ID, changed: boolean | null = false) => ({ snapshot_id: id, acquisition_started_at: '2026-10-04T09:30:00.000000Z', acquisition_completed_at: '2026-10-04T09:30:00.014000Z', source: '/fixture/state/traces.db', source_metadata_at_acquisition: META, source_metadata_now: META, source_metadata_changed: changed, rows: 2, size: 2969600, sqlite_version: '3.53.4' });
export const snapshotConsistency = (id = SNAP_ID, changed: boolean | null = false) => ({ kind: 'snapshot_copy', live_stream: false, snapshot_id: id, acquisition_started_at: '2026-10-04T09:30:00.000000Z', acquisition_completed_at: '2026-10-04T09:30:00.014000Z', source_metadata_at_acquisition: META, source_metadata_now: META, source_metadata_changed: changed });

export const env = <T,>(operation: string, data: T, over: Partial<KupEnvelope<T>> = {}): KupEnvelope<T> => ({
  schema_version: 1, operation, request_id: 'req', observed_at: '2026-10-04T09:00:00Z',
  source: { state_directory: '/fixture/state', trace_database: '/fixture/state/traces.db' },
  consistency: operation.startsWith('history.') || operation === 'snapshot.acquire' ? snapshotConsistency() : operation === 'capabilities' ? { kind: 'not_applicable', live_stream: false } : { kind: 'live_observation', live_stream: false }, data, error: null, ...over,
} as unknown as KupEnvelope<T>);

/** The usual fixture host behaviour for snapshot operations; history ops are answered by `history`. */
export function withSnapshots(history: (req: KupRequest) => unknown, snapshots = [snapshotSummary()]) {
  const state = { snapshots: [...snapshots], acquired: 0 };
  return (req: KupRequest) => {
    switch (req.operation) {
      case 'snapshot.list': return env('snapshot.list', { snapshot_directory: '/fixture/state/kup-snapshots', snapshots: state.snapshots, retain: 3 });
      case 'snapshot.acquire': { state.acquired += 1; const s = snapshotSummary(`20261004T1000000000${String(state.acquired).padStart(2, '0')}Z-acc0000${state.acquired}`); state.snapshots = [s, ...state.snapshots].slice(0, 3); return env('snapshot.acquire', { ...s, orphans_removed: [], pruned: [], backup_steps: 1, duration_ms: 14 }, { consistency: snapshotConsistency(s.snapshot_id) } as never); }
      case 'snapshot.prune': return env('snapshot.prune', { removed: [], orphans_removed: [], kept: state.snapshots.map((s) => s.snapshot_id) });
      case 'history.list': case 'history.detail': case 'history.prompt':
        if (!state.snapshots.some((s) => s.snapshot_id === req.snapshot_id)) return env(req.operation, null, { error: { code: 'SNAPSHOT_UNAVAILABLE', message: `snapshot ${req.snapshot_id} is not published` }, source: null, consistency: null } as never);
        return history(req);
      default: return history(req);
    }
  };
}
