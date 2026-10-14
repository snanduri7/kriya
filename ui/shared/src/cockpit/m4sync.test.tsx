/** Timeline selection drives Context / Attempts / Evidence through the shared SelectionStore; no module calls another. */
import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderShell } from './testing/harness';

const GF = 'fx-greenfield-compile-retry';

describe('M4 modules follow the shared selection', () => {
  it('Timeline → Context → Attempts → Evidence, and Attempts writes back into the shared selection', async () => {
    const { store } = renderShell({ runId: GF });
    const timeline = await screen.findByRole('tree', { name: 'Execution timeline' });
    fireEvent.click(await within(timeline).findByRole('button', { name: 'Attempt 2' }));
    expect(store.getState().attempt).toBe('2');
    fireEvent.click(screen.getByRole('tab', { name: 'Details' })); // Context, Plan, Attempts and Evidence are one level down
    fireEvent.click(screen.getByRole('tab', { name: 'Context' }));
    expect(await screen.findByText(/Model invocations · attempt 2/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Attempts' }));
    const table = await screen.findByRole('table', { name: 'Attempts' });
    expect(within(table).getByRole('button', { name: 'Select attempt 2' })).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(within(table).getByRole('button', { name: 'Select attempt 1' }));
    expect(store.getState().attempt).toBe('1');
    fireEvent.click(screen.getByRole('tab', { name: 'Context' }));
    expect(await screen.findByText(/Model invocations · attempt 1/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Forensic Evidence' }));
    fireEvent.click(await screen.findByLabelText(/Only the selected attempt \(1\)/));
    const rows = within(await screen.findByRole('table', { name: 'Evidence records' })).getAllByRole('row').slice(1);
    expect(rows.length).toBeGreaterThan(0);
  });
});
