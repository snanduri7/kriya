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

export const env = <T,>(operation: string, data: T, over: Partial<KupEnvelope<T>> = {}): KupEnvelope<T> => ({
  schema_version: 1, operation, request_id: 'req', observed_at: '2026-10-04T09:00:00Z',
  source: { state_directory: '/fixture/state', trace_database: '/fixture/state/traces.db' },
  consistency: { kind: 'sqlite_transaction_snapshot', live_stream: false }, data, error: null, ...over,
});
