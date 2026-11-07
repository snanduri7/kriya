/**
 * T2: after the person REJECTS a review the run ends as THEIR decision ("Stopped by you"), never as a failure and never as something to resume; the card offers what can really be done next -
 * every new attempt is a NEW run. Open in Editor opens the PROJECT FOLDER (the workspace path) and always reports a failure.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

const GOAL = 'Add a clamp(value, low, high) function with tests.';
const banner = () => screen.findByRole('region', { name: 'Current run' });

async function rejected() {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'review' } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
  await within(bar).findByRole('region', { name: 'Change review' });
  await screen.findByTestId('proposed-change');
  fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
  await waitFor(() => expect(mounted.host.exec!.decisions).toHaveLength(1));
  act(() => mounted.host.exec!.finish());
  await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
  return { ...mounted, bar };
}

describe('Stopped by you', () => {
  it('says the run was stopped by the person, offers the real next steps, and never calls anything Resume', async () => {
    const { bar } = await rejected();
    expect(bar.querySelector('h2')!.textContent).toBe('Stopped by you');
    expect(within(bar).getByTestId('stopped-by-you')).toHaveTextContent('there is nothing to resume');
    for (const name of ['Run again', 'Edit goal and retry', 'Open in Editor', 'Open in Finder', 'View Details', 'New Goal']) expect(within(bar).getByRole('button', { name }), name).toBeInTheDocument();
    expect(bar.textContent).not.toMatch(/\bResume\b(?! —)/);
    expect(within(bar).queryByRole('button', { name: /resume/i })).toBeNull();
    expect(within(bar).queryByRole('button', { name: 'Recover Project' })).toBeNull();      // Kriya reports the folder CLEAN: recovery is not offered
    expect(within(bar).getByTestId('what-you-can-do')).toBeInTheDocument();
  });

  it('Run again after a rejection is a NEW run with the same goal; Edit goal and retry keeps the goal in the box', async () => {
    const { host, bar } = await rejected();
    fireEvent.click(within(bar).getByRole('button', { name: 'Run again' }));
    await waitFor(() => expect(host.exec!.starts).toHaveLength(2));
    expect(host.exec!.starts[1]!.goal).toBe(GOAL);
  });

  it('Edit goal and retry returns the same goal to a focused box without starting anything', async () => {
    const { host, bar } = await rejected();
    fireEvent.click(within(bar).getByRole('button', { name: 'Edit goal and retry' }));
    const box = await screen.findByRole('textbox', { name: 'Engineering goal' });
    expect(box).toHaveValue(GOAL);
    expect(box).toHaveFocus();
    expect(host.exec!.starts).toHaveLength(1);
  });
});

describe('Open in Editor opens the project folder', () => {
  it('asks the host to open exactly the workspace folder, and shows the host\'s reason when it cannot', async () => {
    const { host, bar } = await rejected();
    fireEvent.click(within(bar).getByRole('button', { name: 'Open in Editor' }));
    await waitFor(() => expect(host.ideCalls).toHaveLength(1));
    expect(host.ideCalls[0]).toEqual({ path: '/fixture/workspace' });            // the folder itself: no file, no line
    host.openInIde = async () => ({ ok: false, editor: 'vscode', verified: true, message: 'Visual Studio Code: spawn code ENOENT' });
    fireEvent.click(within(bar).getByRole('button', { name: 'Open in Editor' }));
    expect(await within(bar).findByText('Could not open editor: Visual Studio Code: spawn code ENOENT')).toBeInTheDocument();
  });
});
