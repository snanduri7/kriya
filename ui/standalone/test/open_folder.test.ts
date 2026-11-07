import { mkdirSync, mkdtempSync, symlinkSync, writeFileSync, realpathSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PACKAGE_EXTENSIONS, openFolderResult, planOpenFolder, type OpenFolderFs } from '../src/main/open_folder';

const fsOf = (): OpenFolderFs => ({ realpath: (p) => realpathSync(p), isDirectory: (p) => { try { return statSync(p).isDirectory(); } catch { return false; } } });
const tmp = () => realpathSync(mkdtempSync(join(tmpdir(), 'kriya-open-folder-')));

describe('planOpenFolder: what the host may hand to the file manager', () => {
  it('plans the canonical real path of a plain project folder', () => {
    const root = tmp(); const project = join(root, 'greeter-demo'); mkdirSync(project);
    expect(planOpenFolder(project, fsOf())).toEqual({ ok: true, path: project });
  });
  it('resolves a symlinked project folder to where it really is (the canonical path, not the alias)', () => {
    const root = tmp(); const real = join(root, 'real'); mkdirSync(real); const alias = join(root, 'alias'); symlinkSync(real, alias);
    expect(planOpenFolder(alias, fsOf())).toEqual({ ok: true, path: real });
  });
  it('refuses when no workspace is open, the folder is gone, or the path is a file', () => {
    const root = tmp(); const file = join(root, 'a.txt'); writeFileSync(file, 'x');
    expect(planOpenFolder(null, fsOf())).toEqual({ ok: false, reason: 'no workspace is open' });
    expect(planOpenFolder(join(root, 'gone'), fsOf())).toEqual({ ok: false, reason: 'the folder is no longer there' });
    expect(planOpenFolder(file, fsOf())).toEqual({ ok: false, reason: 'the folder is no longer there' });
  });
  it.each(['Calc.app', 'Tool.WORKFLOW', 'x.saver', 'Pane.prefPane', 'k.kext', 'a.pkg', 'f.framework'])('refuses an application/document package (%s): openPath would launch it', (name) => {
    const root = tmp(); const pkg = join(root, name); mkdirSync(pkg);
    const plan = planOpenFolder(pkg, fsOf());
    expect(plan.ok).toBe(false);
    expect(plan.ok === false && plan.reason).toContain('application package');
  });
  it('judges the REAL path: an innocently named alias of a package is refused, and a package-looking alias of a plain folder is allowed', () => {
    const root = tmp(); const pkg = join(root, 'Real.app'); mkdirSync(pkg); const alias = join(root, 'notes'); symlinkSync(pkg, alias);
    expect(planOpenFolder(alias, fsOf()).ok).toBe(false);
    const plain = join(root, 'plain'); mkdirSync(plain); const appAlias = join(root, 'Alias.app'); symlinkSync(plain, appAlias);
    expect(planOpenFolder(appAlias, fsOf())).toEqual({ ok: true, path: plain });
  });
  it('a folder merely CONTAINING a package name higher up is a plain folder (only the folder itself decides)', () => {
    const root = tmp(); const inner = join(root, 'Outer.app', 'Contents', 'work'); mkdirSync(inner, { recursive: true });
    expect(planOpenFolder(inner, fsOf())).toEqual({ ok: true, path: inner });
  });
  it('keeps a closed, lower-case extension list that includes .app', () => {
    expect(PACKAGE_EXTENSIONS.has('.app')).toBe(true);
    for (const e of PACKAGE_EXTENSIONS) expect(e).toBe(e.toLowerCase());
  });
});

describe('openFolderResult: shell.openPath answers an error string, it does not throw', () => {
  it('an empty answer is success', () => expect(openFolderResult('')).toEqual({ revealed: true, reason: null }));
  it('a whitespace-only answer is success too', () => expect(openFolderResult('  \n')).toEqual({ revealed: true, reason: null }));
  it('any text is a failure carrying a short, bounded reason', () => {
    const r = openFolderResult('Failed to open path'); expect(r.revealed).toBe(false); expect(r.reason).toContain('Failed to open path');
    expect(openFolderResult('x'.repeat(5000)).reason!.length).toBeLessThan(300);
  });
});
