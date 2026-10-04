/** P-R3 in CI: every generated fixture run renders through the shared App with the browser host's own fixture
 * adapter (no Electron anywhere). Reads the generated files from disk in place of fetch(). */
import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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
      await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(0));
      // The runs column is virtualized (only ~16 rows exist in the DOM), so reach the wanted run through the filter.
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: summaryGoal(id) } });
      await waitFor(() => expect(screen.getAllByRole('option').length).toBe(1));
      const target = screen.getAllByRole('option')[0]!;
      const t0 = performance.now();
      fireEvent.click(target);
      await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(summaryGoal(id)), { timeout: 10000 });
      expect(performance.now() - t0).toBeLessThan(2000);
      for (const tab of ['Context', 'Prompt', 'Output', 'Gates', 'Evidence']) { fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${tab}`) })); expect(screen.getByRole('tabpanel')).toBeInTheDocument(); }
      fireEvent.click(screen.getByRole('tab', { name: /^Why/ })); fireEvent.click(screen.getByRole('tab', { name: /^Diff/ }));
      expect(document.querySelectorAll('[role="tabpanel"]').length).toBeGreaterThan(0);
    });
  }
  it('unknown status and unknown fields are shown literally', async () => {
    render(<App host={new BrowserFixtureHost('', null)} />);
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(0));
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'FIXTURE: unknown event fields' } });
    await waitFor(() => expect(screen.getAllByRole('option').length).toBe(1));
    fireEvent.click(screen.getAllByRole('option')[0]!);
    await waitFor(() => expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent('unknown event fields'));
    expect(screen.getAllByText('PARTIALLY_SETTLED_v9').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/2 unknown fields/).length).toBeGreaterThan(0);
  });
  for (const code of index().errors) {
    it(`error envelope ${code} is shown as a typed, non-current list`, async () => {
      render(<App host={new BrowserFixtureHost('', code)} />);
      await waitFor(() => expect(screen.getAllByText(new RegExp(code)).length).toBeGreaterThan(0));
      expect(screen.queryAllByRole('option').length).toBe(0);
    });
  }
});

function summaryGoal(id: string): string {
  const d = JSON.parse(readFileSync(join(GEN, `history.detail.${id}.json`), 'utf8'));
  return d.data.run.goal;
}
