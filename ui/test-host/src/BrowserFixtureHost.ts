import type { HostAdapter, HostInfo, HostSettings, KupEnvelope, KupRequest, OpenInIdeRequest, OpenInIdeResult } from '@kriya-ui/shared';

/**
 * Fake HostAdapter for the plain-browser test host (P-R3). Answers KUP requests from the generated fixture files;
 * "open in IDE" and settings are simulated (a browser page has no process authority, by design - P-31).
 * `?scenario=<ERROR_CODE>` makes history.list answer with that fixture error envelope; `?scenario=schema2` returns
 * a schema_version 2 envelope; `?scenario=garbage` returns non-JSON.
 */
export class BrowserFixtureHost implements HostAdapter {
  private settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace', kriyaExecutable: null };
  constructor(private base: string, private scenario: string | null, private delayMs = 0) {}

  private async load(name: string): Promise<unknown> {
    const res = await fetch(`${this.base}/generated/${name}`);
    if (this.delayMs) await new Promise((r) => setTimeout(r, this.delayMs));
    if (!res.ok) return { schema_version: 1, operation: 'unknown', request_id: 'fixture', observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code: 'HOST_ERROR', message: `fixture ${name} missing (${res.status})` } };
    return res.json();
  }

  async query(request: KupRequest): Promise<KupEnvelope> {
    if (request.operation === 'history.list' && this.scenario) {
      if (this.scenario === 'schema2') return { ...(await this.load('history.list.page1.json') as KupEnvelope), schema_version: 2 };
      if (this.scenario === 'garbage') return 'not json at all' as unknown as KupEnvelope;
      return this.load(`errors/${this.scenario}.json`) as Promise<KupEnvelope>;
    }
    switch (request.operation) {
      case 'capabilities': return this.load('capabilities.json') as Promise<KupEnvelope>;
      case 'history.list': return this.load(request.cursor ? `history.list.cursor.${request.cursor}.json` : 'history.list.page1.json') as Promise<KupEnvelope>;
      case 'history.detail': return this.load(`history.detail.${encodeURIComponent(request.run_id)}.json`) as Promise<KupEnvelope>;
      case 'history.prompt': return this.load(`history.prompt.${encodeURIComponent(request.run_id)}.json`) as Promise<KupEnvelope>;
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
