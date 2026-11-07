/**
 * HOW TO RUN (owner ruling 2026-11-07): the successful result shows the backend's verified run recipe - copyable, with "Open Terminal Here" through the host - or says plainly that Kriya did not run the
 * application. The cockpit shows the recipe's strings exactly and derives nothing.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

const banner = () => screen.findByRole('region', { name: 'Current run' });

async function completed(runRecipe?: { steps: Array<{ kind: 'setup' | 'run'; command: string }>; environmentNote?: string | null }) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'success', ...(runRecipe ? { runRecipe } : {}) } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Create main.py that prints hello world' } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 6; i += 1) act(() => mounted.host.exec!.advance());
  await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
  return { bar, mounted };
}

describe('How to run on the successful result', () => {
  it('shows the verified command exactly as the backend recorded it, with Copy and Open Terminal Here, and opens the terminal only through the host', async () => {
    const { bar, mounted } = await completed({ steps: [{ kind: 'run', command: 'python3 main.py' }] });
    const card = await within(bar).findByTestId('how-to-run');
    expect(within(card).getByTestId('run-command')).toHaveTextContent(/^python3 main\.py$/);
    expect(within(card).getByRole('button', { name: 'Copy run command' })).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/\.kriya|venv|\/fixture\/workspace/);                  // nothing of Kriya's scratch environment, no path
    expect(within(card).getByTestId('how-to-run-evidence')).toHaveTextContent('isolated project environment');
    fireEvent.click(within(card).getByTestId('open-terminal'));
    await waitFor(() => expect(mounted.host.workspaceCalls).toContain('terminal'));             // the host opened it; the cockpit sent no path and no command
    expect(within(bar).queryByText(/Could not open a terminal/)).toBeNull();
  });

  it('shows a setup step first when the backend says one is needed, and a note it recorded', async () => {
    const { bar } = await completed({ steps: [{ kind: 'setup', command: 'python3 -m pip install -r requirements.txt' }, { kind: 'run', command: 'python3 main.py' }], environmentNote: 'Install dependencies first.' });
    const card = await within(bar).findByTestId('how-to-run');
    expect(within(card).getByTestId('setup-command')).toHaveTextContent('python3 -m pip install -r requirements.txt');
    expect(within(card).getByTestId('run-command')).toHaveTextContent('python3 main.py');
    expect(within(card).getByTestId('how-to-run-note')).toHaveTextContent('Install dependencies first.');
    const labels = Array.from(card.querySelectorAll('li')).map((li) => li.getAttribute('data-kind'));
    expect(labels).toEqual(['setup', 'run']);
  });

  it('says plainly that there is no verified command when Kriya did not run the application, and invents none', async () => {
    const { bar } = await completed();
    const card = await within(bar).findByTestId('how-to-run');
    expect(within(card).getByTestId('how-to-run-none')).toHaveTextContent('Kriya did not run this application, so it has no verified run command.');
    expect(within(card).queryByTestId('run-command')).toBeNull();
    expect(within(card).queryByTestId('open-terminal')).toBeNull();
  });

  it('the result reads What changed, How to run, Verification, then Why', async () => {
    const { bar } = await completed({ steps: [{ kind: 'run', command: 'python3 main.py' }] });
    const headings = Array.from(bar.querySelectorAll('.ck-outcome h3')).map((h) => h.textContent);
    const at = (name: string) => headings.indexOf(name);
    expect(at('What changed')).toBeGreaterThanOrEqual(0);
    expect(at('What changed')).toBeLessThan(at('How to run'));
    expect(at('How to run')).toBeLessThan(at('Verification'));
    if (at('Why') >= 0) expect(at('Verification')).toBeLessThan(at('Why'));
  });
});
