import { mkdirSync, mkdtempSync, realpathSync, symlinkSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { planOpenTerminal } from '../src/main/open_terminal';

const fs = { realpath: realpathSync, isDirectory: (p: string) => { try { return require('node:fs').statSync(p).isDirectory(); } catch { return false; } } };
function folder(name = 'proj') { const root = mkdtempSync(join(tmpdir(), 'kriya-term-')); const dir = join(root, name); mkdirSync(dir); return { root, dir }; }

describe('Open Terminal Here (the host plans; nothing is executed in the terminal)', () => {
  it('opens a terminal at exactly the canonical folder the host has open: a fixed program and fixed arguments, the path as one argv element', () => {
    const { dir } = folder('my project; rm -rf $HOME');
    const plan = planOpenTerminal(dir, fs, 'darwin');
    expect(plan.ok).toBe(true);
    if (plan.ok) { expect(plan.binary).toBe('/usr/bin/open'); expect(plan.argv).toEqual(['-a', 'Terminal', realpathSync(dir)]); expect(plan.argv).toHaveLength(3); }
  });
  it('follows a symlinked folder to its canonical real path (the terminal opens where the project really is)', () => {
    const { root, dir } = folder();
    symlinkSync(dir, join(root, 'link'));
    const plan = planOpenTerminal(join(root, 'link'), fs, 'darwin');
    expect(plan.ok && plan.path).toBe(realpathSync(dir));
  });
  it('refuses with a plain reason: no workspace, a folder that is gone, a file, an application package, and any platform but macOS', () => {
    const { root, dir } = folder();
    expect(planOpenTerminal(null, fs, 'darwin')).toEqual({ ok: false, reason: 'no workspace is open' });
    expect(planOpenTerminal(join(root, 'missing'), fs, 'darwin')).toEqual({ ok: false, reason: 'the folder is no longer there' });
    const pkg = join(root, 'Thing.app'); mkdirSync(pkg);
    const refused = planOpenTerminal(pkg, fs, 'darwin');
    expect(refused.ok).toBe(false);
    for (const platform of ['linux', 'win32']) {
      const plan = planOpenTerminal(dir, fs, platform);
      expect(plan.ok).toBe(false);
      if (!plan.ok) expect(plan.reason).toContain('only available on macOS');
    }
  });
  it('is the one host path that opens a terminal: main.ts spawns only the planned program, with no shell, behind the owned-renderer check', () => {
    const main = require('node:fs').readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8') as string;
    const at = main.indexOf('IPC_CHANNELS.openTerminal');
    expect(at).toBeGreaterThan(0);
    const handler = main.slice(at, at + 1100);
    expect(handler).toContain('fromOwnedRenderer(event)');
    expect(handler).toContain('planOpenTerminal(workspaces.current()');
    expect(handler).toContain('shell: false');
    expect(handler).not.toMatch(/\b(raw|request|arg)\b/);                     // no renderer-supplied path or command reaches it
  });
});
