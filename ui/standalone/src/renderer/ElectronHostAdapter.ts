import type { HostAdapter, HostInfo, HostSettings, KupEnvelope, KupRequest, OpenInIdeRequest, OpenInIdeResult } from '@kriya-ui/shared';

export interface KriyaHostBridge {
  query(request: unknown): Promise<unknown>;
  openInIde(request: unknown): Promise<unknown>;
  copyToClipboard(text: string): Promise<void>;
  getSetting(key: string): Promise<unknown>;
  setSetting(key: string, value: unknown): Promise<void>;
  hostInfo(): Promise<unknown>;
  measureMode: boolean;
}

/** HostAdapter over the preload bridge. The renderer has no other capability (contextIsolation + sandbox). */
export class ElectronHostAdapter implements HostAdapter {
  constructor(private bridge: KriyaHostBridge, private info: HostInfo) {}
  static async create(bridge: KriyaHostBridge): Promise<ElectronHostAdapter> {
    const info = (await bridge.hostInfo()) as HostInfo;
    return new ElectronHostAdapter(bridge, info);
  }
  query(request: KupRequest): Promise<KupEnvelope> { return this.bridge.query(request) as Promise<KupEnvelope>; }
  openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { return this.bridge.openInIde(request) as Promise<OpenInIdeResult>; }
  copyToClipboard(text: string): Promise<void> { return this.bridge.copyToClipboard(text); }
  getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined> { return this.bridge.getSetting(key) as Promise<HostSettings[K] | undefined>; }
  setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void> { return this.bridge.setSetting(key, value); }
  hostInfo(): HostInfo { return this.info; }
}
