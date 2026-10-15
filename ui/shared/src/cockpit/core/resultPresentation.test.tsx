/** The result screen's pieces after the post-live Demo 00 closure: the captured output (bounded, whole text one click away), the technical verification (no empty rows) and the formal requirement disclosure. */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ApplicationResult, CapturedOutput, FormalRequirementDisclosure, OUTPUT_PREVIEW_LINES, TechnicalVerification } from './ResultParts';
import { formalRequirementStatus } from './presentation';
import { validationFacts, type GateFact } from './runFacts';
import success from '../testing/live-greenfield-success.records.json';
import type { ApplicationOutcome } from '../../model/kup';
import type { EvidenceRecord } from './records';

const gate = (type: string, passed: boolean | null = true): GateFact => ({ type, passed, tests: null, requirementsVerified: null });

describe('TechnicalVerification: no empty rows, no empty section', () => {
  it('omits the whole section when no row has a recorded type', () => {
    const { container } = render(<TechnicalVerification gates={[gate(''), gate('   ')]} />);
    expect(container.innerHTML).toBe('');
    expect(render(<TechnicalVerification gates={[]} />).container.innerHTML).toBe('');
  });
  it('drops an empty row and keeps the valid ones with a readable name and the exact recorded type', () => {
    render(<TechnicalVerification gates={[gate('compile'), gate(''), gate('tests', false), gate('run_verification', null)]} />);
    const items = within(screen.getByRole('list', { name: 'Technical verification' })).getAllByRole('listitem').map((li) => li.textContent);
    expect(items).toEqual(['✓ Compile (compile)', '✕ Tests (tests)', '• Runtime verification (run_verification)']);
    expect(items.every((t) => /[A-Za-z]/.test(t ?? ''))).toBe(true); // a row is never just a mark or a dash
  });
  it('the live Demo 00 run lists its eight recorded checks, each with its type', () => {
    render(<TechnicalVerification gates={validationFacts(success as unknown as EvidenceRecord[])} />);
    const items = within(screen.getByRole('list', { name: 'Technical verification' })).getAllByRole('listitem').map((li) => li.textContent ?? '');
    expect(items).toHaveLength(8);
    for (const type of ['pom_validate', 'compile', 'tests', 'targeted_test', 'runtime_verification', 'run_verification', 'goal_spec_compliance', 'regression_test']) expect(items.some((t) => t.includes(type))).toBe(true);
    expect(items.every((t) => t.trim().length > 2)).toBe(true);
  });
});

describe('CapturedOutput: shown as captured, bounded, with the whole text one click away - never parsed', () => {
  const lines = (n: number) => Array.from({ length: n }, (_, i) => `line ${i + 1}`).join('\n');
  it('a short output is shown whole, with no disclosure', () => {
    render(<CapturedOutput text={lines(OUTPUT_PREVIEW_LINES)} />);
    expect(screen.getByLabelText('Application output').textContent).toBe(lines(OUTPUT_PREVIEW_LINES));
    expect(screen.queryByTestId('full-output')).toBeNull();
  });
  it('a long output shows its first lines and offers the full text; nothing is dropped', () => {
    render(<CapturedOutput text={`${lines(OUTPUT_PREVIEW_LINES + 10)}\n\n`} />);
    expect(screen.getByLabelText('Application output').textContent).toBe(lines(OUTPUT_PREVIEW_LINES));
    const full = screen.getByTestId('full-output') as HTMLDetailsElement;
    expect(full.open).toBe(false);
    expect(within(full).getByText(`Show full output (${OUTPUT_PREVIEW_LINES + 10} lines)`)).toBeInTheDocument();
    expect(screen.getByLabelText('Full application output').textContent).toBe(lines(OUTPUT_PREVIEW_LINES + 10));
  });
  it('no output says so', () => {
    render(<CapturedOutput text={'  \n'} />);
    expect(screen.getByLabelText('Application output').textContent).toBe('(no output)');
  });
  it('the live Maven output is not parsed: the program line is inside the captured text, in recorded order, and the whole text is available', () => {
    const maven = '[INFO] Scanning for projects...\n[INFO] --- exec:3.6.4:java (default-cli) @ hello-kriya ---\nHello from Kriya\n[INFO] BUILD SUCCESS';
    const app = { status: 'EXECUTED', succeeded: true, exit_code: 0, timed_out: false, command: 'mvn -e exec:java', stdout: { text: maven, truncated: false }, stderr: { text: '', truncated: false } } as unknown as ApplicationOutcome;
    render(<ApplicationResult app={app} />);
    expect(screen.getByText('✓ Ran successfully')).toBeInTheDocument();
    expect(screen.getByLabelText('Application output').textContent).toBe(maven);
  });
});

describe('FormalRequirementDisclosure', () => {
  it('is a collapsed disclosure, carries the ledger fact, and never says anything failed', () => {
    render(<FormalRequirementDisclosure status={formalRequirementStatus([gate('compile'), gate('goal_spec_compliance')])} />);
    const d = screen.getByTestId('formal-requirements') as HTMLDetailsElement;
    expect(d.open).toBe(false);
    expect(within(d).getByText('Formal requirement verification')).toBeInTheDocument();
    expect(d.textContent).toMatch(/Requirement ledger: not fully verified/);
    expect(d.textContent).toMatch(/Matches the planned change/);
    expect(d.textContent).not.toMatch(/everything is verified|may not work|has failed|did not pass/i);
  });
  it('renders nothing when there is nothing to qualify', () => {
    expect(render(<FormalRequirementDisclosure status={null} />).container.innerHTML).toBe('');
  });
});
