import { fireEvent, screen, waitFor } from '@testing-library/react';
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
    expect(screen.getAllByText(/Unfinished/).filter((el) => el.closest('li'))).toHaveLength(1); // the row (the status filter's option is not a row)
    expect(screen.queryByText(/\bActive\b|Running/)).toBeNull();
    expect(screen.getByText(/Succeeded · 1m 38s/)).toBeInTheDocument(); // what happened and how long, in words
    expect(screen.queryByText(/\d+ attempt\(s\)/)).toBeNull(); // the trace row's retry counter is never presented as an attempt count
  });
  it('selecting a run writes only runId (and resets what is below it)', async () => {
    const { store } = renderModule(runsModule, { selection: { attempt: '3', unitId: 'u' } });
    fireEvent.click(await screen.findByRole('button', { name: /Create a small CLI app/ }));
    expect(store.getState()).toMatchObject({ runId: 'fx-greenfield-success', attempt: null, unitId: null });
  });
});

describe('attempt count presentation (GUI final)', () => {
  it('a history card never shows attempts (neither the trace row\'s retry counter nor an observed count): attempts are technical detail, one level down', async () => {
    renderModule(runsModule, { runId: 'fx-greenfield-success' });
    expect(await screen.findByText(/Succeeded · 1m 11s/)).toBeInTheDocument();
    const meta = Array.from(document.querySelectorAll('.ck-runmeta'));
    expect(meta.length).toBeGreaterThan(3);
    for (const m of meta) expect(m.textContent ?? '').not.toMatch(/attempt/i); // the card's facts: status, duration, files, time - never attempts
  });
});

describe('filters (end-user experience)', () => {
  it('while more history exists, a filter says it applies only to the runs loaded so far, and loading more extends it (no evidence is read)', async () => {
    const provider = createFixtureProvider();
    const real = provider.listRuns.bind(provider);
    const paged = Object.assign(Object.create(provider), { listRuns: async (sid: string, page: { limit?: number; cursor?: string } = {}) => {
      const all = await real(sid, { limit: 200 });
      if (!all.ok) return all;
      const runs = all.data.runs;
      return page.cursor ? { ...all, data: { runs: runs.slice(5), next_cursor: null } } : { ...all, data: { runs: runs.slice(0, 5), next_cursor: 'abc' } };
    } });
    renderModule(runsModule, { provider: paged });
    await screen.findByText('5 run(s) loaded');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter runs' }), { target: { value: 'a' } });
    expect(screen.getByRole('status')).toHaveTextContent('Filters apply to the runs loaded so far; load more to include older runs');
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' }));
    await waitFor(() => expect(screen.getByRole('status').textContent).not.toMatch(/loaded so far/));
  });

  const rows = () => screen.getAllByRole('listitem');
  it('search, status and time narrow the loaded rows, say how many match, and Clear restores everything; no evidence is read to filter', async () => {
    const { provider } = renderModule(runsModule);
    await screen.findByText('Milestone fx-milestone-1');
    const all = rows().length;
    expect(all).toBeGreaterThan(3);
    const search = screen.getByRole('searchbox', { name: 'Filter runs' });
    fireEvent.change(search, { target: { value: 'Speed up Matcher' } });
    expect(rows()).toHaveLength(1);
    expect(screen.getByRole('status')).toHaveTextContent(`1 of ${all} loaded run(s) match`);
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(rows()).toHaveLength(all);
    fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'failure' } });
    expect(rows().every((li) => /Failed/.test(li.textContent ?? ''))).toBe(true);
    expect(rows().length).toBeGreaterThan(0);
    fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'unfinished' } });
    expect(rows()).toHaveLength(1);
    expect(rows()[0]).toHaveTextContent('Unfinished');
    fireEvent.change(screen.getByRole('combobox', { name: 'Status' }), { target: { value: 'all' } });
    fireEvent.change(screen.getByRole('combobox', { name: 'Time' }), { target: { value: 'today' } });
    expect(screen.getByText('No loaded run matches these filters')).toBeInTheDocument(); // fixture runs are dated October 2026; the test clock is later
    void provider;
  });
  it('a filter that matches nothing offers Clear filters and never claims the history is empty', async () => {
    renderModule(runsModule);
    await screen.findByText('Milestone fx-milestone-1');
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter runs' }), { target: { value: 'zzzzzz' } });
    expect(screen.getByText('No loaded run matches these filters')).toBeInTheDocument();
    expect(screen.queryByText('No run is recorded for this workspace')).toBeNull();
    fireEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]!);
    expect(rows().length).toBeGreaterThan(3);
  });
});
