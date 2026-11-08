/**
 * REQUIREMENT TRUTH and OBSERVED OUTPUT on the result (owner sign-off 2026-11-08). The cockpit shows the backend's typed requirement truth exactly and never recomputes it: ALL_VERIFIED keeps "Run completed";
 * OPEN_REQUIREMENTS is "Changes applied — verification incomplete" with the unresolved requirements listed (never ordinary green); CONTRADICTED says so. A run whose record carries no truth is "not recorded" - never "verified".
 * The observed output is the backend's redacted, capped evidence; cut or lost output is said, never shown as complete.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { requirementTruthOf, requirementTruthWords, runtimeObservationOf } from '../core';
import { renderWorkspaceShell } from '../testing/harness';

type Exec = Record<string, unknown>;
const banner = () => screen.findByRole('region', { name: 'Current run' });

async function completed(execution: Exec) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'success', ...execution } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Create hello.py. It prints hello world when executed. Include a pytest test for it.' } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 6; i += 1) act(() => mounted.host.exec!.advance());
  await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
  return bar;
}

const OPEN = { status: 'OPEN_REQUIREMENTS', verified: 2, total: 3, unresolved: [{ id: 'REQ-2', text: 'It prints hello world when executed.' }] } as const;

describe('requirement truth on the completed result', () => {
  it('OPEN_REQUIREMENTS is "Changes applied — verification incomplete", lists the unresolved requirement and never shows ordinary green', async () => {
    const bar = await completed({ requirementTruth: OPEN });
    expect(within(bar).getByRole('heading', { level: 2 })).toHaveTextContent('Changes applied — verification incomplete');
    expect(bar.textContent).not.toMatch(/Run completed|Your change was applied successfully|Application created successfully/);
    expect(within(bar).getByTestId('requirement-truth-summary')).toHaveTextContent('2 of 3 requirements verified · 1 unverified');
    expect(within(bar).getByTestId('requirement-truth')).toHaveAttribute('data-status', 'OPEN_REQUIREMENTS');
    expect(within(bar).getAllByTestId('requirement-unresolved').map((li) => li.textContent)).toEqual([expect.stringContaining('It prints hello world when executed.')]);
    expect(within(bar).getByTestId('applied-with-open-requirements')).toHaveTextContent('Your change was applied');
  });

  it('ALL_VERIFIED keeps the ordinary "Run completed" and says every requirement was verified', async () => {
    const bar = await completed({ requirementTruth: { status: 'ALL_VERIFIED', verified: 3, total: 3 } });
    expect(within(bar).getByRole('heading', { level: 2 })).toHaveTextContent('Run completed');
    expect(within(bar).getByTestId('requirement-truth-summary')).toHaveTextContent('3 of 3 requirements verified');
    expect(within(bar).queryByTestId('requirement-unresolved')).toBeNull();
  });

  it('CONTRADICTED is never green and says a requirement is contradicted by evidence', async () => {
    const bar = await completed({ requirementTruth: { status: 'CONTRADICTED', verified: 1, total: 3, unresolved: [{ id: 'REQ-2', text: 'It prints "hello world" when executed.', outcome: 'violated' }] } });
    expect(within(bar).getByRole('heading', { level: 2 })).toHaveTextContent('a requirement is contradicted');
    expect(within(bar).getByTestId('requirement-unresolved')).toHaveTextContent('contradicted by evidence');
  });

  it('a run whose record carries no truth shows no requirement-truth block and claims nothing was verified', async () => {
    const bar = await completed({});
    expect(within(bar).getByRole('heading', { level: 2 })).toHaveTextContent('Run completed');
    expect(within(bar).queryByTestId('requirement-truth')).toBeNull();
  });
});

describe('observed output on the completed result', () => {
  it('shows the command, the output and the exit status exactly as recorded', async () => {
    const bar = await completed({ runtimeObservation: { command: 'python3 hello.py', exitStatus: 0, stdout: 'hello world\n' } });
    const card = within(bar).getByTestId('observed-output');
    expect(within(card).getByTestId('observed-command')).toHaveTextContent(/^python3 hello\.py$/);
    expect(within(card).getByTestId('observed-stdout').textContent).toBe('hello world\n');
    expect(within(card).getByTestId('observed-exit')).toHaveTextContent(/^0$/);
    expect(within(card).queryByTestId('observed-incomplete')).toBeNull();
  });

  it('says so when the output was cut or lost, and when the program printed nothing', async () => {
    const lost = within(await completed({ runtimeObservation: { command: 'python3 hello.py', exitStatus: 0, stdout: 'abc', stdoutLost: true } })).getByTestId('observed-incomplete');
    expect(lost).toHaveTextContent('lost when it was captured');
  });

  it('shows nothing when Kriya did not run the application', async () => {
    const bar = await completed({});
    expect(within(bar).queryByTestId('observed-output')).toBeNull();
  });
});

describe('the projections validate the backend record and derive nothing', () => {
  it('rejects an unknown status or a malformed record', () => {
    expect(requirementTruthOf({ requirement_truth: { status: 'GREEN', verified: 1, total: 1 } })).toBeNull();
    expect(requirementTruthOf({ requirement_truth: { status: 'ALL_VERIFIED', verified: '1', total: 1 } })).toBeNull();
    expect(requirementTruthOf({})).toBeNull();
    expect(requirementTruthOf(null)).toBeNull();
    expect(runtimeObservationOf({ runtime_observation: { command: '', stdout: 'x' } })).toBeNull();
    expect(runtimeObservationOf({ runtime_observation: { command: 'python3 x.py' } })).toBeNull();
  });
  it('words: not recorded keeps today\'s words, open and contradicted never say "Run completed"', () => {
    expect(requirementTruthWords(null)).toMatchObject({ heading: 'Run completed', tone: 'ok', summary: null });
    expect(requirementTruthWords(requirementTruthOf({ requirement_truth: OPEN }))).toMatchObject({ heading: 'Changes applied — verification incomplete', tone: 'warn' });
    expect(requirementTruthWords({ status: 'CONTRADICTED', verified: 0, total: 1, unresolved: [] }).heading).not.toBe('Run completed');
  });
});
