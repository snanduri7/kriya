/** P-R3 in CI: every generated fixture run renders through the shared App with the browser host's own fixture
 * adapter (no Electron anywhere). Reads the generated files from disk in place of fetch(). */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App, checkEnvelope } from '@kriya-ui/shared';
import { BrowserFixtureHost } from '../src/BrowserFixtureHost';

const GEN = join(__dirname, '..', '..', 'fixtures', 'generated');

beforeAll(() => {
  if (!existsSync(join(GEN, 'index.json'))) throw new Error('run `npm run fixtures` first');
  vi.stubGlobal('fetch', async (url: string) => {
    const p = join(GEN, decodeURIComponent(url.replace(/^\/generated\//, '')));
    if (!existsSync(p)) return { ok: false, status: 404, json: async () => null } as unknown as Response;
    return { ok: true, status: 200, json: async () => JSON.parse(readFileSync(p, 'utf8')) } as unknown as Response;
  });
});

const index = () => JSON.parse(readFileSync(join(GEN, 'index.json'), 'utf8')) as { runs: string[]; specials: string[]; errors: string[] };

describe('generated fixtures are valid KUP v1 envelopes', () => {
  it('every detail/prompt/list/capabilities/status file passes checkEnvelope', () => {
    const { runs } = index();
    for (const id of runs) {
      expect(checkEnvelope(JSON.parse(readFileSync(join(GEN, `history.detail.${id}.json`), 'utf8'))).ok).toBe(true);
      expect(checkEnvelope(JSON.parse(readFileSync(join(GEN, `history.prompt.${id}.json`), 'utf8'))).ok).toBe(true);
    }
    for (const f of ['capabilities.json', 'history.list.page1.json', 'workspace.status.json']) expect(checkEnvelope(JSON.parse(readFileSync(join(GEN, f), 'utf8'))).ok).toBe(true);
  });
  it('the big-events detail is larger than 4 MiB (A2 target) and smaller than the 8 MiB stdout limit', () => {
    const bytes = readFileSync(join(GEN, 'history.detail.run-big-events.json')).byteLength;
    expect(bytes).toBeGreaterThan(4 * 1024 * 1024); expect(bytes).toBeLessThan(8 * 1024 * 1024);
  });
});

describe('every special fixture renders in the browser test host', () => {
  for (const id of index().specials) {
    it(`renders ${id} with all panels`, { timeout: 30000 }, async () => {
      const host = new BrowserFixtureHost('', null);
      render(<App host={host} />);
      fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
      await waitFor(() => expect(screen.getAllByRole('option', { selected: undefined }).filter((o) => o.classList.contains('vrow')).length).toBeGreaterThan(0), { timeout: 10000 });
      // The runs column is virtualized (only ~16 rows exist in the DOM), so reach the wanted run through the filter.
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: summaryGoal(id) } });
      await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(1));
      const target = screen.getAllByRole('option').filter((o) => o.classList.contains('vrow'))[0]!;
      const t0 = performance.now();
      fireEvent.click(target);
      await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(summaryGoal(id)), { timeout: 10000 });
      expect(performance.now() - t0).toBeLessThan(2000);
      for (const tab of ['Context', 'Prompt', 'Output', 'Gates', 'Evidence']) { fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${tab}`) })); expect(screen.getByRole('tabpanel')).toBeInTheDocument(); }
      fireEvent.click(screen.getByRole('tab', { name: /^Why/ })); fireEvent.click(screen.getByRole('tab', { name: /^Diff/ }));
      expect(document.querySelectorAll('[role="tabpanel"]').length).toBeGreaterThan(0);
    });
  }
  it('production-shaped gate records (fixtures/serializer_gates.py, through the KUP adapter) render with type, recorded success and output', { timeout: 30000 }, async () => {
    render(<App host={new BrowserFixtureHost('', null)} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBeGreaterThan(0), { timeout: 10000 });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: summaryGoal('run-serializer-events') } });
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(1));
    fireEvent.click(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow'))[0]!);
    await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(summaryGoal('run-serializer-events')), { timeout: 10000 });
    fireEvent.click(screen.getByRole('tab', { name: /^Gates/ }));
    const panel = screen.getByRole('tabpanel');
    for (const text of ['compile · failure', 'test_selection · failure', 'targeted_test · success', 'run_verification · failure', 'goal_spec_compliance · success', 'status UNAVAILABLE', 'VERIFIER_REQUEST_REFUSED', 'graded_by process_exit · deterministic_result PASS', "src/mod1/a.py:12:5: error: name 'audit' is not defined"]) expect(panel).toHaveTextContent(text);
    for (const absent of ['result not recorded', 'unknown fields preserved', 'conflicting result fields', 'type not recorded']) expect(panel).not.toHaveTextContent(absent);
  });
  it('attribution: the serializer run says not recorded with Kriya\'s reason; the demonstration shows an empty list; the NEGATIVE fixture shows unresolved references, never a link', { timeout: 30000 }, async () => {
    const open = async (goalFragment: string) => {
      // the runs column is the first listbox and search box in DOM order; once a run is open the timeline adds its own virtualized options and filter
      const runRows = () => within(screen.getAllByRole('listbox')[0]!).getAllByRole('option').filter((o) => o.classList.contains('vrow'));
      fireEvent.change(screen.getAllByRole('searchbox')[0]!, { target: { value: goalFragment } });
      await waitFor(() => expect(runRows().length).toBe(1));
      fireEvent.click(runRows()[0]!);
      await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(goalFragment), { timeout: 10000 });
      fireEvent.click(screen.getByRole('tab', { name: /^Why/ }));
      return document.querySelector('.why') as HTMLElement;
    };
    render(<App host={new BrowserFixtureHost('', null)} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBeGreaterThan(0), { timeout: 10000 });
    const serializer = await open('exactly as Kriya serializes');
    expect(serializer).toHaveTextContent('Recorded causal attribution: not recorded - the baseline persists failure categories, not causal attribution');
    expect(serializer).not.toHaveTextContent('unresolved reference');
    const demo = await open('synthetic demonstration');
    expect(demo).toHaveTextContent('none recorded (empty list)');
    expect(demo).not.toHaveTextContent('unresolved reference');
    const negative = await open('NEGATIVE CASE');
    const refs = within(negative).getByRole('list', { name: 'Evidence references' });
    expect(within(refs).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['ev-negative-1 — unresolved reference', 'ev-negative-1 — unresolved reference', 'ev-negative-2 — unresolved reference']);
    expect(negative).toHaveTextContent('Repeated within the list: ev-negative-1');
    expect(negative).not.toHaveTextContent(/\bresolved\b/); // never "resolved" on its own; only "unresolved reference"
    fireEvent.click(screen.getByRole('tab', { name: /^Evidence/ }));
    expect(document.getElementById('insp-panel-evidence')).toHaveTextContent('unknown fields preserved: evidence_id'); // the inspector's Evidence tab panel (inactive panels are rendered hidden): the invented field stays visible, never read as a link
  });
  it('unknown status and unknown fields are shown literally', async () => {
    render(<App host={new BrowserFixtureHost('', null)} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBeGreaterThan(0), { timeout: 10000 });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'FIXTURE: unknown event fields' } });
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(1));
    fireEvent.click(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow'))[0]!);
    await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('unknown event fields'));
    expect(screen.getAllByText('PARTIALLY_SETTLED_v9').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 unknown fields/).length).toBeGreaterThan(0);
  });
  it('the serializer-shaped run shows its event names in the timeline (08 review F-3)', async () => {
    render(<App host={new BrowserFixtureHost('', null)} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBeGreaterThan(0), { timeout: 10000 });
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'exactly as Kriya serializes' } });
    await waitFor(() => expect(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(1));
    fireEvent.click(screen.getAllByRole('option').filter((o) => o.classList.contains('vrow'))[0]!);
    await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('exactly as Kriya serializes'));
    const list = screen.getByRole('listbox', { name: /recorded events/i });
    for (const kind of ['context.known_target_package', 'developer.prompt_composition', 'model.transition', 'model.role_metrics']) expect(list).toHaveTextContent(kind);
    expect(list).not.toHaveTextContent('(unnamed event)');
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('qwen2.5-coder:14b / QUALIFIED');
  });
  it('a snapshot that fails digest verification at pin is never displayed (?scenario=verify_corrupt)', async () => {
    render(<App host={new BrowserFixtureHost('', 'verify_corrupt')} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Snapshot state' })).toHaveTextContent(/digest verification failed: SNAPSHOT_CORRUPT/), { timeout: 10000 });
    expect(screen.queryAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(0);
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('none pinned');
  });
  for (const code of index().errors) {
    it(`error envelope ${code} is shown typed and no run is rendered`, async () => {
      render(<App host={new BrowserFixtureHost('', code)} />);
      fireEvent.click(await screen.findByRole('button', { name: 'Acquire new snapshot' }));
      await waitFor(() => expect(screen.getAllByText(new RegExp(code)).length).toBeGreaterThan(0), { timeout: 10000 });
      expect(screen.queryAllByRole('option').filter((o) => o.classList.contains('vrow')).length).toBe(0);
    });
  }
});

function summaryGoal(id: string): string {
  const d = JSON.parse(readFileSync(join(GEN, `history.detail.${id}.json`), 'utf8'));
  return d.data.run.goal;
}
