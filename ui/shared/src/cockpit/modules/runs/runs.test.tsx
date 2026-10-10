import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { runsModule } from '.';

describe('Runs', () => {
  it('groups by milestone, then by day; an in_progress row is Unfinished, never Active or Running', async () => {
    renderModule(runsModule);
    await screen.findByText('Milestone fx-milestone-1');
    expect(screen.getByText('2026-10-10')).toBeInTheDocument();
    expect(screen.getByText(/Unfinished/)).toBeInTheDocument();
    expect(screen.queryByText(/\bActive\b|Running/)).toBeNull();
    expect(screen.getByText(/success · 1 attempt\(s\) · 98\.1s/)).toBeInTheDocument();
  });
  it('selecting a run writes only runId (and resets what is below it)', async () => {
    const { store } = renderModule(runsModule, { selection: { attempt: '3', unitId: 'u' } });
    fireEvent.click(await screen.findByRole('button', { name: /Create a small CLI app/ }));
    expect(store.getState()).toMatchObject({ runId: 'fx-greenfield-success', attempt: null, unitId: null });
  });
});
