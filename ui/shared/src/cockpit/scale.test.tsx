/** GUI v1 hardening (Phase E): the modules at SCALE and with hostile content - thousands of runs, thousands of records, many attempts and files, Unicode/shell-metacharacter
 * paths, very long goals and paths. Content is only ever text (never markup); lists page; evidence stays lazy; selection stays correct. (jsdom measures robustness and shape, not
 * speed: the real-app timings live in handover/GUI-RUN-COCKPIT-V1_1/evidence/m9/scale-run*.) */
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../state/selection';
import { createCockpitRegistry } from './composition';
import { createSelectionStore, loadRunEvidence } from './core';
import { FIXTURE_SNAPSHOT_ID, FixtureHost } from './providers/fixture';
import { HostProvider } from './providers/hostProvider';
import { AppShell } from './shell/AppShell';
import { FAST_POLL } from './testing/harness';
import { bigScenario, manyRuns } from './testing/bigScenario';

const mount = (host: FixtureHost, selection = {}) => {
  cleanup();
  const store = createSelectionStore({ ...initialSelection, ...selection });
  render(<AppShell provider={new HostProvider(host, 'fixture')} registry={createCockpitRegistry()} selection={store} viewportWidth={1440} pollIntervals={FAST_POLL} initialPage="runs" />);
  return store;
};

describe('a long history', () => {
  it('3000 runs: the list pages (it never renders thousands of rows at once), Load more appends, the filter works, and selection stays on the clicked run', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: manyRuns(3000) });
    // the real backend pages history.list (limit/cursor, MEASURED: 200 rows per page); the fixture host returns everything, so this test applies Kriya's paging to its answer
    const everything = host.query.bind(host);
    host.query = async (request) => {
      const envelope = await everything(request);
      if (request.operation !== 'history.list' || !envelope.data) return envelope;
      const r = request as { limit?: number; cursor?: string };
      const rows = (envelope.data as { runs: unknown[] }).runs;
      const start = r.cursor ? Number(r.cursor) : 0;
      const limit = r.limit ?? 200;
      return { ...envelope, data: { ...(envelope.data as object), runs: rows.slice(start, start + limit), next_cursor: start + limit < rows.length ? String(start + limit) : null } } as typeof envelope;
    };
    const store = mount(host);
    const first = await screen.findAllByRole('button', { name: /success|failure/ }, { timeout: 8000 });
    expect(first.length).toBeLessThanOrEqual(260); // one page, not 3000 rows
    expect(first.length).toBeGreaterThan(100);
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' }));
    await waitFor(() => expect(screen.getAllByRole('button', { name: /success|failure/ }).length).toBeGreaterThan(first.length));
    const rows = screen.getAllByRole('button', { name: /success|failure/ });
    const target = rows[rows.length - 1]!; // a row that only exists because Load more appended it
    const label = (target.textContent ?? '').slice(0, 24);
    fireEvent.click(target);
    await waitFor(() => expect(store.getState().runId).toMatch(/^hist-/));
    expect(screen.getAllByRole('button').find((b) => b.getAttribute('aria-selected') === 'true')?.textContent).toContain(label);
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter runs' }), { target: { value: 'historic goal 2999' } });
    await waitFor(() => expect(screen.getAllByRole('button', { name: /Historic goal 2999/ }).length).toBe(1));
    fireEvent.change(screen.getByRole('searchbox', { name: 'Filter runs' }), { target: { value: 'zz-no-such-run' } });
    await waitFor(() => expect(screen.queryAllByRole('button', { name: /success|failure/ })).toHaveLength(0));
  });

  it('a very long goal and a goal with shell metacharacters are shown as plain text, bounded in layout', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: manyRuns(120) });
    mount(host);
    const long = (await screen.findAllByRole('button', { name: /success|failure/ })).find((b) => (b.textContent ?? '').includes('gggggggggg'))!;
    expect(long).toBeDefined();
    expect(long.querySelectorAll('img, script, iframe, a[href]').length).toBe(0);
    const meta = screen.getAllByRole('button').find((b) => (b.textContent ?? '').includes('$(rm -rf)'))!;
    expect(meta.textContent).toContain('$(rm -rf)'); // inert text
  });
});

describe('one very large run', () => {
  const big = bigScenario({ id: 'big-1', evidenceId: 'big-ev', attempts: 30, filesPerAttempt: 8, gatesPerAttempt: 6, extraRecords: 5000, goal: 'A long goal. '.repeat(200) });
  const count = big.seed.store === 'present' ? big.seed.records.length : 0;

  it('loads every record through the paged client (one verified pass over thousands of records)', async () => {
    expect(count).toBeGreaterThan(5000);
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: [big] });
    const loaded = await loadRunEvidence(new HostProvider(host, 'fixture'), 'big-1', FIXTURE_SNAPSHOT_ID);
    expect(loaded.state === 'ready' || loaded.state === 'degraded').toBe(true);
    if (loaded.state === 'ready' || loaded.state === 'degraded') expect(loaded.data.records).toHaveLength(count);
  }, 30000);

  it('the Evidence audit never renders every record: it shows a bounded window and says how many exist; content parts stay lazy', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: [big], evidenceContent: true });
    mount(host, { runId: 'big-1', centralTab: 'evidence' });
    const panel = await screen.findByRole('tabpanel', undefined, { timeout: 20000 });
    await waitFor(() => expect(panel.textContent).toMatch(/EVIDENCE AUDIT|Evidence audit/i), { timeout: 20000 });
    expect(panel.textContent).toMatch(new RegExp(`${count} RECORDS|${count} records`, 'i'));
    expect(panel.querySelectorAll('tr').length).toBeLessThan(400); // a window, not thousands of rows
    const contentCalls = host.calls.filter((c) => c.operation === 'evidence.change_content' || c.operation === 'evidence.call_content');
    expect(contentCalls).toHaveLength(0); // no diff or prompt was fetched merely by loading the run
  }, 60000);

  it('Changes lists many files with hostile, Unicode and very long paths as plain text; a diff is read only when asked and is never interpreted as markup', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: [big], evidenceContent: true });
    mount(host, { runId: 'big-1', centralTab: 'changes', attempt: '30' });
    const panel = await screen.findByRole('tabpanel', undefined, { timeout: 20000 });
    await waitFor(() => expect(panel.querySelectorAll('ul button').length).toBeGreaterThanOrEqual(8), { timeout: 20000 });
    const text = panel.textContent ?? '';
    expect(text).toContain('Ünïcode/файл');
    expect(text).toContain('日本語/テスト');
    expect(text).toContain('$(subshell)');
    expect(panel.querySelectorAll('script, iframe, img').length).toBe(0);
    expect(host.calls.filter((c) => c.operation === 'evidence.change_content')).toHaveLength(0);
    fireEvent.click(panel.querySelector('ul button')!);
    fireEvent.click(await within(panel).findByRole('button', { name: 'Show diff' }));
    await within(panel).findByLabelText('Unified diff');
    expect(host.calls.filter((c) => c.operation === 'evidence.change_content')).toHaveLength(1); // exactly the one that was asked for
  }, 60000);

  it('repeated navigation over the large run leaves no stale selection and does not multiply requests: 40 cycles issue a bounded number of evidence reads', async () => {
    const host = new FixtureHost({ workspace: '/fixture/workspace', scenarios: [big, ...manyRuns(8)] });
    const store = mount(host, { runId: 'big-1' });
    await screen.findByRole('tabpanel', undefined, { timeout: 20000 });
    const tabs = ['Validation', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence', 'Overview'];
    const before = host.calls.filter((c) => c.operation.startsWith('evidence.')).length;
    for (let cycle = 0; cycle < 40; cycle += 1) for (const name of tabs) { const tab = screen.queryByRole('tab', { name }); if (tab) fireEvent.click(tab); }
    await new Promise((r) => setTimeout(r, 200));
    const after = host.calls.filter((c) => c.operation.startsWith('evidence.')).length;
    expect(after - before).toBeLessThanOrEqual(2); // tab switches re-read nothing: the run's evidence was loaded once and is shared by every module
    expect(store.getState().runId).toBe('big-1');
  }, 90000);
});
