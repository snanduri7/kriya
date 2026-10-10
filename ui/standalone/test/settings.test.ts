import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { writeFileSync } from 'node:fs';
import { SettingsStore, sanitizeRecent } from '../src/main/settings';

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

describe('remembered workspaces are UI data, never trust', () => {
  it('round-trip, most-recent-first, bounded; a hand-edited file keeps only well-formed absolute paths', () => {
    const dir = mkdtempSync(join(tmpdir(), 'kriya-ui-recent-'));
    const f = join(dir, 'settings.json');
    const s = new SettingsStore(f);
    expect(s.get('recentWorkspaces')).toEqual([]);
    s.set('recentWorkspaces', ['/a', '/b']);
    expect(new SettingsStore(f).get('recentWorkspaces')).toEqual(['/a', '/b']);
    writeFileSync(f, JSON.stringify({ recentWorkspaces: ['/ok', 'relative', '', '/has\u0001control', 7, null, '/ok', ...Array.from({ length: 30 }, (_, i) => `/p${i}`)] }));
    const cleaned = new SettingsStore(f).get('recentWorkspaces');
    expect(cleaned[0]).toBe('/ok');
    expect(cleaned).not.toContain('relative');
    expect(cleaned.filter((p) => p === '/ok')).toHaveLength(1);
    expect(cleaned.length).toBe(10);
    expect(sanitizeRecent('nope')).toEqual([]);
    expect(sanitizeRecent(undefined)).toEqual([]);
  });
});
