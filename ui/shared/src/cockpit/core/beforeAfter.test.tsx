/** The before/after summary states only what the run recorded: a row with no result says "not recorded", the delivered line counts files and recorded line changes, recovery is the recorded attempts. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BeforeAfter, deliveredLabel, recoveryText, securityText, summaryRows, writeScopeOf } from './BeforeAfter';
import type { ChangedFile, VerificationGroup } from './presentation';

const group = (id: VerificationGroup['id'], status: 'passed' | 'failed' | 'unknown', text: string): VerificationGroup =>
  ({ id, title: id, status, lines: [{ text, status, gates: [], line: id }] as unknown as VerificationGroup['lines'] });
const file = (path: string, changeType: ChangedFile['changeType'], additions: number | null = 3, deletions: number | null = 0): ChangedFile => ({ path, additions, deletions, changeType });

describe('summaryRows', () => {
  it('reports the four rows from the recorded groups and says "not recorded" for a group with no lines', () => {
    const rows = summaryRows([group('build', 'passed', 'Build passed'), group('tests', 'failed', 'Tests failed')]);
    expect(rows.map((r) => [r.label, r.status, r.text])).toEqual([['Build', 'passed', 'Build passed'], ['Tests', 'failed', 'Tests failed'], ['Runtime', 'unknown', 'not recorded'], ['Safety checks', 'unknown', 'not recorded']]);
  });
  it('is unknown, not passed, when a group has an unknown line', () => {
    expect(summaryRows([group('runtime', 'unknown', 'Runtime verification: no result recorded')])[2]?.status).toBe('unknown');
  });
});

describe('recoveryText', () => {
  it.each([[[], 0, null], [[true], 1, '1 attempt (succeeded)'], [[false], 1, '1 attempt (failed)'], [[false, true], 2, '2 attempts (#1 failed, #2 succeeded)'], [[], 2, '2 attempts (outcomes not recorded)']] as const)(
    '%j opened %j -> %j', (concluded, opened, expected) => { expect(recoveryText(concluded, opened)).toBe(expected); });
});

describe('BeforeAfter', () => {
  it('shows the request, the delivered change set with recorded line counts, the rows and the attempts', () => {
    render(<BeforeAfter request="Create hello.py" files={[file('hello.py', 'added', 2), file('test_hello.py', 'added', 5)]}
      groups={[group('build', 'passed', 'Build passed'), group('tests', 'passed', '1 / 1 tests passed'), group('runtime', 'passed', 'Runtime verification passed')]} concluded={[false, true]} opened={2} />);
    expect(screen.getByText('Create hello.py')).toBeTruthy();
    expect(document.querySelector('[data-row="Delivered"]')!.textContent).toBe('2 files (+7 −0 lines)');
    expect(screen.getByText('Created')).toBeTruthy();
    expect(document.querySelector('[data-row="Tests"]')!.textContent).toContain('PASS');
    expect(document.querySelector('[data-row="Safety checks"]')!.textContent).toContain('not recorded');
    expect(document.querySelector('[data-row="Recovery"]')!.textContent).toBe('2 attempts (#1 failed, #2 succeeded)');
  });
  it('does not invent line counts the recorder did not persist, and says when the change set is unreadable', () => {
    const a = render(<BeforeAfter request="r" files={[file('a.py', 'modified', null, null)]} groups={[]} concluded={[]} opened={0} />);
    expect(document.querySelector('[data-row="Delivered"]')!.textContent).toBe('1 file');
    expect(screen.getByText('Changed')).toBeTruthy();
    a.unmount();
    render(<BeforeAfter request="r" files={null} groups={null} concluded={[]} opened={0} />);
    expect(screen.getByText(/change set is not readable/)).toBeTruthy();
    expect(document.querySelector('[data-row="Recovery"]')!.textContent).toBe('not recorded');
  });
});

describe('the Created / Changed label comes from the recorded change classification', () => {
  it.each([
    [null, 'Delivered'], [[], 'Delivered'],
    [[file('a.py', 'added'), file('b.py', 'added')], 'Created'],
    [[file('a.py', 'added'), file('b.py', 'modified')], 'Changed'],
    [[file('a.py', 'modified')], 'Changed'], [[file('a.py', null)], 'Changed'],
  ] as const)('%j -> %s', (files, label) => { expect(deliveredLabel(files as never)).toBe(label); });
});

describe('the Security row never invents a per-operation decision', () => {
  it('writeScopeOf counts distinct recorded file targets and is null with no snapshot', () => {
    expect(writeScopeOf([])).toBeNull();
    expect(writeScopeOf([{ targets: [{ path: 'a.py' }, { path: 'b.py' }] }, { targets: [{ path: 'a.py' }] }])).toEqual({ targets: 2 });
    expect(writeScopeOf([{ targets: 'garbage' }, { targets: [null, 3, { path: 7 }] }])).toEqual({ targets: 0 });
  });
  it.each([
    ['a run with a recorded write scope', { targets: 2 }, /write scope recorded for 2 file targets; no per-operation decision recorded/],
    ['one target', { targets: 1 }, /for 1 file target;/],
    ['a create-only run, no snapshot', null, /no authority snapshot recorded/],
  ] as const)('%s', (_name, scope, text) => {
    expect(securityText(scope)).toMatch(text);
    render(<BeforeAfter request="r" files={[file('a.py', 'added')]} groups={[]} concluded={[true]} opened={1} securityScope={scope} />);
    const row = document.querySelector('[data-row="Security"]')!;
    expect(row.textContent).toContain('Not recorded');
    expect(row.textContent).not.toMatch(/ALLOWED|BLOCKED|REDACTED|PASS|FAIL/);
  });
  it('is present, and still "Not recorded", on a failed run, an unreadable run and a run that has not finished', () => {
    for (const files of [[file('a.py', 'modified')], null, []] as const) {
      const r = render(<BeforeAfter request="r" files={files as never} groups={files === null ? null : [group('tests', 'failed', 'Tests failed')]} concluded={[false]} opened={1} />);
      expect(document.querySelector('[data-row="Security"]')!.textContent).toContain('Not recorded');
      r.unmount();
    }
  });
});
