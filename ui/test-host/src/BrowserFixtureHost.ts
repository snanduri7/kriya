import type { HostAdapter, HostInfo, HostSettings, KupEnvelope, KupRequest, OpenInIdeRequest, OpenInIdeResult } from '@kriya-ui/shared';

/**
 * Fake HostAdapter for the plain-browser test host (P-R3). Answers KUP requests from the generated fixture files;
 * "open in IDE" and settings are simulated (a browser page has no process authority, by design - P-31).
 * `?scenario=<ERROR_CODE>` makes history.list answer with that fixture error envelope; `?scenario=schema2` returns
 * a schema_version 2 envelope; `?scenario=garbage` returns non-JSON.
 */
export class BrowserFixtureHost implements HostAdapter {
  private settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace', kriyaExecutable: null };
  /** In-memory snapshot store: the fixture snapshots plus ids "acquired" in this page (gate C-2 semantics). */
  private snapshots: Record<string, unknown>[] | null = null;
  private acquired = 0;
  constructor(private base: string, private scenario: string | null, private delayMs = 0) {}

  private async snapshotState(): Promise<Record<string, unknown>[]> {
    if (this.snapshots === null) { const list = (await this.load('snapshot.list.json')) as KupEnvelope<{ snapshots: Record<string, unknown>[] }>; this.snapshots = [...(list.data?.snapshots ?? [])]; }
    return this.snapshots;
  }

  private pinned(env: KupEnvelope, snap: Record<string, unknown>): KupEnvelope {
    const c = env.consistency as Record<string, unknown> | null;
    const consistency = c ? { ...c, snapshot_id: snap.snapshot_id, acquisition_started_at: snap.acquisition_started_at, acquisition_completed_at: snap.acquisition_completed_at, source_metadata_at_acquisition: snap.source_metadata_at_acquisition, source_metadata_now: snap.source_metadata_now, source_metadata_changed: snap.source_metadata_changed } : c;
    return { ...env, source: env.source ? { ...env.source, snapshot_id: snap.snapshot_id as string } : env.source, consistency } as unknown as KupEnvelope;
  }

  private async load(name: string): Promise<unknown> {
    const res = await fetch(`${this.base}/generated/${name}`);
    if (this.delayMs) await new Promise((r) => setTimeout(r, this.delayMs));
    if (!res.ok) return { schema_version: 1, operation: 'unknown', request_id: 'fixture', observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code: 'HOST_ERROR', message: `fixture ${name} missing (${res.status})` } };
    return res.json();
  }

  async query(request: KupRequest): Promise<KupEnvelope> {
    if (request.operation === 'history.list' && this.scenario) {
      if (this.scenario === 'schema2') return { ...(await this.load('history.list.page1.json') as KupEnvelope), schema_version: 2 } as unknown as KupEnvelope;
      if (this.scenario === 'garbage') return 'not json at all' as unknown as KupEnvelope;
      return this.load(`errors/${this.scenario}.json`) as Promise<KupEnvelope>;
    }
    const snaps = await this.snapshotState();
    const unavailable = (op: string, id: string): KupEnvelope => ({ schema_version: 1, operation: op, request_id: 'fixture', observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: snaps.length ? { code: 'SNAPSHOT_UNAVAILABLE', message: `snapshot ${id} is not published (pruned or never existed)`, snapshot_id: id } : { code: 'SNAPSHOT_MISSING', message: 'no published snapshot exists; acquire one first' } } as unknown as KupEnvelope);
    const find = (id: string) => snaps.find((s) => s.snapshot_id === id);
    switch (request.operation) {
      case 'capabilities': return this.load('capabilities.json') as Promise<KupEnvelope>;
      case 'snapshot.list': { const env = (await this.load('snapshot.list.json')) as KupEnvelope<Record<string, unknown>>; const data = (env.data ?? {}) as Record<string, unknown>; return { ...env, data: { ...data, snapshots: request.verify ? snaps.map((s) => ({ ...s, digest_verified: true })) : snaps } } as unknown as KupEnvelope; }
      case 'snapshot.acquire': {
        const base = (await this.load('snapshot.acquire.json')) as KupEnvelope<Record<string, unknown>>;
        const baseData = (base.data ?? {}) as Record<string, unknown>;
        this.acquired += 1;
        const d = new Date(); const p = (n: number, w = 2) => String(n).padStart(w, '0');
        const id = `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}T${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}${p(d.getUTCMilliseconds(), 3)}000Z-${(0xb0000000 + this.acquired).toString(16)}`;
        const snap: Record<string, unknown> = { ...(snaps[0] ?? {}), ...baseData, snapshot_id: id, acquisition_started_at: d.toISOString(), acquisition_completed_at: new Date(d.getTime() + 14).toISOString(), source_metadata_changed: false };
        snaps.unshift(snap); const pruned = snaps.splice(3).map((s) => s.snapshot_id as string);
        return this.pinned({ ...base, data: { ...snap, orphans_removed: [], pruned, backup_steps: 1, duration_ms: 14 } } as KupEnvelope, snap);
      }
      case 'snapshot.prune': { const keep = request.keep ?? 3; const removed = snaps.splice(keep).map((s) => s.snapshot_id as string); return { ...((await this.load('snapshot.prune.json')) as KupEnvelope), data: { removed, orphans_removed: [], kept: snaps.map((s) => s.snapshot_id) } } as KupEnvelope; }
      case 'history.list': { const s = find(request.snapshot_id); if (!s) return unavailable('history.list', request.snapshot_id); return this.pinned((await this.load(request.cursor ? `history.list.cursor.${request.cursor}.json` : 'history.list.page1.json')) as KupEnvelope, s); }
      case 'history.detail': { const s = find(request.snapshot_id); if (!s) return unavailable('history.detail', request.snapshot_id); return this.pinned((await this.load(`history.detail.${encodeURIComponent(request.run_id)}.json`)) as KupEnvelope, s); }
      case 'history.prompt': { const s = find(request.snapshot_id); if (!s) return unavailable('history.prompt', request.snapshot_id); return this.pinned((await this.load(`history.prompt.${encodeURIComponent(request.run_id)}.json`)) as KupEnvelope, s); }
      case 'workspace.status': return this.load('workspace.status.json') as Promise<KupEnvelope>;
    }
  }

  async openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> {
    return { ok: false, editor: this.settings.editor ?? 'vscode', verified: false, message: `browser test host cannot launch editors (simulated: ${request.path}:${request.line ?? ''})` };
  }

  async copyToClipboard(text: string): Promise<void> {
    try { await navigator.clipboard?.writeText(text); } catch { /* clipboard may be unavailable in a test page */ }
  }

  async getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined> { return this.settings[key] as HostSettings[K] | undefined; }
  async setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void> { this.settings[key] = value; }
  hostInfo(): HostInfo { return { kind: 'browser-test', hostVersion: '0.0.1', fixtureMode: true }; }
}
