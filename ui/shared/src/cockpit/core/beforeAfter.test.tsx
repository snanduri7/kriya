/** The before/after summary states only what the run recorded: a row with no result says "not recorded", the delivered line counts files and recorded line changes, recovery is the recorded attempts. */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BeforeAfter, recoveryText, summaryRows } from './BeforeAfter';
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
    expect(screen.getByText('2 files created (+7 −0 lines)')).toBeTruthy();
    expect(document.querySelector('[data-row="Tests"]')!.textContent).toContain('PASS');
    expect(document.querySelector('[data-row="Safety checks"]')!.textContent).toContain('not recorded');
    expect(document.querySelector('[data-row="Recovery"]')!.textContent).toBe('2 attempts (#1 failed, #2 succeeded)');
  });
  it('does not invent line counts the recorder did not persist, and says when the change set is unreadable', () => {
    const a = render(<BeforeAfter request="r" files={[file('a.py', 'modified', null, null)]} groups={[]} concluded={[]} opened={0} />);
    expect(screen.getByText('1 file changed')).toBeTruthy();
    a.unmount();
    render(<BeforeAfter request="r" files={null} groups={null} concluded={[]} opened={0} />);
    expect(screen.getByText(/change set is not readable/)).toBeTruthy();
    expect(document.querySelector('[data-row="Recovery"]')!.textContent).toBe('not recorded');
  });
});
