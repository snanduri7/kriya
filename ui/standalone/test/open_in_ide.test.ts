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
