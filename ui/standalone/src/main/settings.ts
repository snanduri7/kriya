/** Host-owned settings (editor, kriya executable, workspace) in Electron's userData: never Kriya state (P-31). */
import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { EDITOR_IDS, type EditorId, type SettingKey } from './ipc_contract';

export interface Settings { editor: EditorId; kriyaExecutable: string | null; workspacePath: string | null }
export const DEFAULT_SETTINGS: Settings = { editor: 'vscode', kriyaExecutable: null, workspacePath: null };

export class SettingsStore {
  private cache: Settings | null = null;
  constructor(private file: string) {}
  read(): Settings {
    if (this.cache) return this.cache;
    try {
      const raw = JSON.parse(readFileSync(this.file, 'utf8')) as Partial<Settings>;
      this.cache = {
        editor: (EDITOR_IDS as readonly string[]).includes(String(raw.editor)) ? (raw.editor as EditorId) : DEFAULT_SETTINGS.editor,
        kriyaExecutable: typeof raw.kriyaExecutable === 'string' ? raw.kriyaExecutable : null,
        workspacePath: typeof raw.workspacePath === 'string' ? raw.workspacePath : null,
      };
    } catch { this.cache = { ...DEFAULT_SETTINGS }; }
    return this.cache;
  }
  get<K extends SettingKey>(key: K): Settings[K] { return this.read()[key]; }
  set<K extends SettingKey>(key: K, value: Settings[K]): void {
    const next = { ...this.read(), [key]: value } as Settings;
    mkdirSync(dirname(this.file), { recursive: true });
    const tmp = join(dirname(this.file), `.settings.${process.pid}.tmp`);
    writeFileSync(tmp, JSON.stringify(next, null, 2));
    renameSync(tmp, this.file);
    this.cache = next;
  }
}
