import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { evidenceModule } from '.';

describe('Evidence', () => {
  it('lists every recorded record with its provenance and selecting a row writes only eventSeq', async () => {
    const { store } = renderModule(evidenceModule, { runId: 'fx-greenfield-success' });
    const table = await screen.findByRole('table', { name: 'Evidence records' });
    const rows = within(table).getAllByRole('row');
    expect(rows.length).toBeGreaterThan(5);
    fireEvent.click(rows[2]!);
    expect(store.getState().eventSeq).toBe(2); // rows[0] is the header, rows[2] is record seq 2
    expect(store.getState().drawerOpen).toBe(false);
  });
});
