import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { SettingsStore } from '../src/main/settings';

describe('host settings store', () => {
  it('round-trips and falls back to defaults on an invalid editor', () => {
    const f = join(mkdtempSync(join(tmpdir(), 'kriya-ui-settings-')), 'nested', 'settings.json');
    const s = new SettingsStore(f);
    expect(s.get('editor')).toBe('vscode');
    s.set('editor', 'intellij'); s.set('workspacePath', '/w');
    const again = new SettingsStore(f);
    expect(again.get('editor')).toBe('intellij'); expect(again.get('workspacePath')).toBe('/w'); expect(again.get('kriyaExecutable')).toBeNull();
    expect(again.get('configDirectory')).toBeNull(); // default: the operator's HOME, resolved by the main process
    again.set('configDirectory', '/Volumes/work/project');
    expect(new SettingsStore(f).get('configDirectory')).toBe('/Volumes/work/project');
    expect(new SettingsStore(f).get('workspacePath')).toBe('/w'); // independent settings
  });
});
