import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { evidenceModule } from '.';

describe('Evidence audit', () => {
  it('filters by kind and unit; the count reflects the filter', async () => {
    renderModule(evidenceModule, { runId: 'fx-greenfield-compile-retry' });
    await screen.findByRole('table', { name: 'Evidence records' });
    const before = screen.getByText(/Evidence audit \((\d+) of (\d+) records\)/).textContent!;
    fireEvent.change(screen.getByLabelText('Kind'), { target: { value: 'gate.result' } });
    const after = screen.getByText(/Evidence audit/).textContent!;
    expect(after).not.toBe(before);
    for (const row of within(screen.getByRole('table', { name: 'Evidence records' })).getAllByRole('row').slice(1)) expect(within(row).getByText('gate.result')).toBeInTheDocument();
  });

  it('groups by attempt and shows human-readable detail with the raw payload only on demand', async () => {
    renderModule(evidenceModule, { runId: 'fx-greenfield-compile-retry' });
    await screen.findByRole('table', { name: 'Evidence records' });
    fireEvent.change(screen.getByLabelText('Group by'), { target: { value: 'attempt' } });
    expect(screen.getByText(/^Attempt 1 \(/)).toBeInTheDocument();
    expect(screen.queryByLabelText('Record detail')).toBeNull();
    fireEvent.click(screen.getAllByRole('row')[3]!);
    const detail = screen.getByLabelText('Record detail');
    expect(within(detail).getByText('Raw payload')).toBeInTheDocument();
    expect(detail.querySelector('details')!.open).toBe(false);
  });

  it('an impossible filter combination is an explicit empty state, not a blank table', async () => {
    renderModule(evidenceModule, { runId: 'fx-greenfield-success', selection: { attempt: '99' } });
    await screen.findByRole('table', { name: 'Evidence records' });
    fireEvent.click(screen.getByLabelText(/Only the selected attempt/));
    expect(screen.getByText('No record matches these filters')).toBeInTheDocument();
    expect(screen.queryByRole('table', { name: 'Evidence records' })).toBeNull();
  });
});
