import type { KupEnvelope, KupRequest } from '../model/kup';

/**
 * The ONE host boundary (gate A-2, P-R1). Every capability a host supplies to
 * the shared panels goes through this interface: a KUP query, open in IDE,
 * clipboard, settings. ui/shared never imports Electron or Node; a host
 * (Electron preload, the browser test host, a future IDE plugin) implements
 * this and hands it to <App/>. The wire-level message schema for a real host
 * lives in ui/kup (P-R2, Phase B); this is its TypeScript face.
 */
export type EditorId = 'vscode' | 'intellij' | 'eclipse';

export interface HostSettings {
  editor: EditorId;
  kriyaExecutable: string | null;
  workspacePath: string | null;
}

export interface OpenInIdeRequest {
  path: string;
  line?: number;
}

export interface OpenInIdeResult {
  ok: boolean;
  editor: EditorId;
  verified: boolean; // false = the host knows the command form is unverified on this machine
  message: string;
}

export interface HostInfo {
  kind: string; // 'electron' | 'browser-test' | ...
  hostVersion: string;
  fixtureMode: boolean;
}

export interface HostAdapter {
  query(request: KupRequest, signal?: AbortSignal): Promise<KupEnvelope>;
  openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult>;
  copyToClipboard(text: string): Promise<void>;
  getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined>;
  setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void>;
  hostInfo(): HostInfo;
}
