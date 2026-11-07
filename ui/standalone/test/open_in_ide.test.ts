import { mkdirSync, mkdtempSync, realpathSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { EDITOR_FORMS, planOpenInIde } from '../src/main/open_in_ide';

function workspace() {
  const root = mkdtempSync(join(tmpdir(), 'kriya-ui-ws-'));
  const ws = join(root, 'ws'); mkdirSync(join(ws, 'src'), { recursive: true });
  writeFileSync(join(ws, 'src', 'a.py'), 'x = 1\n');
  const outside = join(root, 'outside.txt'); writeFileSync(outside, 'secret');
  symlinkSync(outside, join(ws, 'src', 'link_out.txt'));
  return { ws, outside };
}

describe('open in IDE (P-R5, Phase E rules)', () => {
  it('opens only existing regular files inside the workspace, with the fixed editor form', () => {
    const { ws } = workspace();
    const plan = planOpenInIde('vscode', ws, 'src/a.py', 7);
    expect(plan.ok).toBe(true);
    if (plan.ok) { expect(plan.binary).toBe('code'); expect(plan.argv).toEqual(['-g', `${realpathSync(join(ws, 'src', 'a.py'))}:7`]); }
    const abs = planOpenInIde('vscode', ws, join(ws, 'src', 'a.py'));
    expect(abs.ok && abs.argv).toEqual(['-g', realpathSync(join(ws, 'src', 'a.py'))]);
  });
  it('refuses escapes: .., symlink pointing outside, absolute path outside, directory, missing file, no workspace', () => {
    const { ws, outside } = workspace();
    for (const [p, reason] of [['../outside.txt', 'outside'], ['src/link_out.txt', 'outside'], [outside, 'outside'], ['src', 'not a regular file'], ['src/missing.py', 'does not exist']] as const) {
      const plan = planOpenInIde('vscode', ws, p);
      expect(plan.ok, p).toBe(false); if (!plan.ok) expect(plan.reason).toContain(reason);
    }
    expect(planOpenInIde('vscode', null, 'src/a.py').ok).toBe(false);
    expect(planOpenInIde('vscode', ws, 'src/a.py', 0).ok).toBe(false);
  });
  it('a file whose NAME merely starts with two dots is inside the workspace and is opened (review P3-6); the escape refusals still hold', () => {
    const { ws } = workspace();
    writeFileSync(join(ws, '..hidden.py'), 'y = 2\n');
    writeFileSync(join(ws, 'src', '..data'), 'z\n');
    for (const p of ['..hidden.py', 'src/..data']) {
      const plan = planOpenInIde('vscode', ws, p);
      expect(plan.ok, p).toBe(true);
    }
    expect(planOpenInIde('vscode', ws, '../ws/../outside.txt').ok).toBe(false);
  });
  it('IntelliJ and Eclipse stay in the table, marked unverified; VS Code is the verified one', () => {
    expect(EDITOR_FORMS.vscode.verified).toBe(true);
    expect(EDITOR_FORMS.intellij.verified).toBe(false); expect(EDITOR_FORMS.intellij.verificationNote).toContain('unverified');
    expect(EDITOR_FORMS.eclipse.verified).toBe(false); expect(EDITOR_FORMS.eclipse.verificationNote).toContain('unverified');
    const { ws } = workspace();
    const ij = planOpenInIde('intellij', ws, 'src/a.py', 3);
    expect(ij.ok && ij.argv).toEqual(['--line', '3', realpathSync(join(ws, 'src', 'a.py'))]);
  });
  it('never puts run data into the command: the binary and flags come from the table only', () => {
    const { ws } = workspace();
    writeFileSync(join(ws, 'src', '-rf.py'), '');
    const plan = planOpenInIde('vscode', ws, 'src/-rf.py', 2);
    expect(plan.ok && plan.argv[0]).toBe('-g');
    expect(plan.ok && plan.argv[1]).toMatch(/\/src\/-rf\.py:2$/);
  });
});

describe('Open in Editor for the PROJECT FOLDER (DESKTOP-OPEN-IN-EDITOR-PROJECT-FOLDER-001) - the same planner, one extra mode', () => {
  it('opens exactly the selected workspace folder through the editor\'s own folder form (no -g, no line), by real path', () => {
    const { ws } = workspace();
    const plan = planOpenInIde('vscode', ws, ws);
    expect(plan.ok).toBe(true);
    if (plan.ok) { expect(plan.binary).toBe('code'); expect(plan.argv).toEqual([realpathSync(ws)]); }
    const viaDot = planOpenInIde('vscode', ws, '.');           // a relative path that IS the workspace is the workspace
    expect(viaDot.ok && viaDot.argv).toEqual([realpathSync(ws)]);
  });
  it('refuses every other folder: the parent, a sibling, a sub-folder, a symlink to a folder outside - and a folder with a line', () => {
    const root = mkdtempSync(join(tmpdir(), 'kriya-ui-folder-'));
    const ws = join(root, 'ws'); mkdirSync(join(ws, 'src'), { recursive: true });
    const sibling = join(root, 'sibling'); mkdirSync(sibling);
    symlinkSync(sibling, join(ws, 'link_to_sibling'));
    for (const [p, reason] of [[root, 'outside'], [sibling, 'outside'], ['..', 'outside'], ['src', 'not a regular file'], ['link_to_sibling', 'outside']] as const) {
      const plan = planOpenInIde('vscode', ws, p);
      expect(plan.ok, p).toBe(false); if (!plan.ok) expect(plan.reason, p).toContain(reason);
    }
    const withLine = planOpenInIde('vscode', ws, ws, 3);
    expect(withLine.ok).toBe(false); if (!withLine.ok) expect(withLine.reason).toContain('no line');
  });
  it('a workspace that is itself a symlink opens its real folder; no workspace selected is a refusal', () => {
    const root = mkdtempSync(join(tmpdir(), 'kriya-ui-link-'));
    const real = join(root, 'real'); mkdirSync(real);
    const link = join(root, 'link'); symlinkSync(real, link);
    const plan = planOpenInIde('vscode', link, link);
    expect(plan.ok && plan.argv).toEqual([realpathSync(real)]);
    expect(planOpenInIde('vscode', null, link).ok).toBe(false);
  });
  it('an editor without a verified folder form is refused with a reason - never a guessed command', () => {
    const { ws } = workspace();
    const plan = planOpenInIde('eclipse', ws, ws);
    expect(plan.ok).toBe(false); if (!plan.ok) expect(plan.reason).toMatch(/no verified way to open a project folder/);
    expect(EDITOR_FORMS.eclipse.folderArgv).toBeNull();
  });
  it('the folder argument is data, never a shell fragment: a workspace name with shell metacharacters is passed as ONE argv element', () => {
    const root = mkdtempSync(join(tmpdir(), 'kriya-ui-meta-'));
    const ws = join(root, 'a b; touch pwned $(id)'); mkdirSync(ws);
    const plan = planOpenInIde('vscode', ws, ws);
    expect(plan.ok && plan.argv).toEqual([realpathSync(ws)]);
    expect(plan.ok && plan.argv.length).toBe(1);
  });
});
