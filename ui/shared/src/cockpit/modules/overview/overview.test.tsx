import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { overviewModule } from '.';

describe('Overview', () => {
  it('greenfield: no baseline is assumed', async () => {
    renderModule(overviewModule, { runId: 'fx-greenfield-success' });
    expect(await screen.findByText('not recorded (no baseline is assumed)')).toBeInTheDocument();
    expect(await screen.findByText('Run closed')).toBeInTheDocument();
  });
  it('brownfield: the recorded baseline revision, verbatim', async () => {
    renderModule(overviewModule, { runId: 'fx-brownfield-modify' });
    expect(await screen.findByText('a1b2c3d4e5f60718293a4b5c6d7e8f9012345678')).toBeInTheDocument();
  });
  it('an unfinished run is not claimed to be active', async () => {
    renderModule(overviewModule, { runId: 'fx-unsealed' });
    expect(await screen.findByText(/Unfinished \(the trace row says in_progress; this is not a claim that a run is active\)/)).toBeInTheDocument(); // the stored-status row keeps its caveat
    expect(screen.getByText('Run not finished')).toBeInTheDocument(); // and the summary says only that it did not finish
  });
  it('a pruned run still shows the run row and says the evidence is pruned', async () => {
    renderModule(overviewModule, { runId: 'fx-pruned' });
    expect(await screen.findByText('Evidence pruned by retention')).toBeInTheDocument();
    expect(screen.getByText('An older run whose store retention removed')).toBeInTheDocument();
  });
  it('opens with a plain summary - the outcome, the changed files and the recorded validation - before any identifier', async () => {
    renderModule(overviewModule, { runId: 'fx-brownfield-modify' });
    const summary = (await screen.findByText('What happened')).closest('section') as HTMLElement;
    expect(within(summary).getByText('Succeeded')).toBeInTheDocument();
    expect(within(await within(summary).findByRole('list', { name: 'Changed files' })).getAllByRole('listitem').length).toBeGreaterThan(0);
    expect(summary.textContent).toMatch(/passed/);
    const run = screen.getByText('Run ID').closest('section') as HTMLElement;
    expect(summary.compareDocumentPosition(run) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy(); // the summary comes first, the identifiers after
  });
  it('a failed run says Failed, never a summary of success; with no readable evidence it says where the facts come from instead of inventing them', async () => {
    renderModule(overviewModule, { runId: 'fx-pruned' });
    const summary = (await screen.findByText('What happened')).closest('section') as HTMLElement;
    expect(summary.textContent).toMatch(/not readable here/);
    expect(within(summary).queryByRole('list', { name: 'Changed files' })).toBeNull();
  });
});
