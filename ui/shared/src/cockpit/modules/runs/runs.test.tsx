import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFixtureProvider } from '../../providers/fixture';
import { renderModule } from '../../testing/harness';
import { runsModule } from '.';

describe('Runs', () => {
  it('pages through history: a Load more button appends the next page, and the count says whether all runs are loaded', async () => {
    const provider = createFixtureProvider();
    const real = provider.listRuns.bind(provider);
    const calls: Array<string | undefined> = [];
    const paged = Object.assign(Object.create(provider), { listRuns: async (sid: string, page: { limit?: number; cursor?: string } = {}) => {
      calls.push(page.cursor);
      const all = await real(sid, { limit: 200 });
      if (!all.ok) return all;
      const runs = all.data.runs;
      return page.cursor ? { ...all, data: { runs: runs.slice(5), next_cursor: null } } : { ...all, data: { runs: runs.slice(0, 5), next_cursor: 'abc' } };
    } });
    renderModule(runsModule, { provider: paged });
    expect(await screen.findByText('5 run(s) loaded')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' }));
    expect(await screen.findByText(/\d+ run\(s\) loaded \(all\)/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Load more runs' })).toBeNull();
    expect(calls).toEqual([undefined, 'abc']);
  });

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
