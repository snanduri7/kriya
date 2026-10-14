/** GUI v1 hardening (Phases B, F, G): the cockpit as a state machine - transitions, races and stale responses - over the scripted fixture host. No transition may yield a false
 * success, no stale answer may overwrite a newer selection, and a run's ownership never moves because the person looked at another run. */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../../state/selection';
import { createCockpitRegistry } from '../composition';
import { createSelectionStore } from '../core';
import { FixtureHost, type FixtureHostOptions } from '../providers/fixture';
import { manyRuns } from '../testing/bigScenario';
import { LAUNCH_ID, REVIEW_DIGEST } from '../providers/fixture/fixtureExecution';
import { HostProvider } from '../providers/hostProvider';
import { FAST_POLL, renderWorkspaceShell } from '../testing/harness';
import { AppShell } from './AppShell';
import type { KupEnvelope, KupRequest } from '../../model/kup';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });
/** From another page the compact strip's Review button returns to the Project page's current run, where the one review lives. */
const backToRun = async () => { fireEvent.click(within(await screen.findByRole('status', { name: 'Active run' })).getByRole('button', { name: 'Review' })); return banner(); };
const GOAL = 'Add a clamp(value, low, high) function with tests.';

async function startRun(fixture: FixtureHostOptions = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  return { ...mounted, bar: await banner() };
}
const toReview = async (fixture: FixtureHostOptions = {}) => {
  const started = await startRun({ execution: { outcome: 'review' }, ...fixture });
  for (let i = 0; i < 3; i += 1) act(() => started.host.exec!.advance());
  await within(started.bar).findByRole('region', { name: 'Change review' });
  return started;
};

describe('a run that dies while its review is pending', () => {
  it('is "did not finish": no decision control remains, nothing was approved, and it is never a success', async () => {
    const { host, bar } = await toReview();
    act(() => host.exec!.killProcess());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'interrupted'), { timeout: 4000 });
    expect(within(bar).getByText('The run did not finish')).toBeInTheDocument();
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
    expect(within(bar).queryByRole('button', { name: /Approve|Reject/ })).toBeNull();
    expect(host.exec!.decisions).toEqual([]);
    expect(within(bar).queryByText(/Run succeeded/)).toBeNull();
  });

  it('a decision attempted after the process died is refused by Kriya and never turns into success', async () => {
    const { host, bar } = await toReview();
    const review = within(bar).getByRole('region', { name: 'Change review' });
    const shown = { workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: review.getAttribute('data-review-id')!, candidate_digest: REVIEW_DIGEST, decision: 'approve' as const };
    act(() => host.exec!.finish()); // a no-op while a review waits; the run is still waiting
    expect(bar).toHaveAttribute('data-outcome', '');
    act(() => host.exec!.killProcess());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'interrupted'), { timeout: 4000 });
    expect(review.isConnected).toBe(false);
    // the late approval a stale window could still send: Kriya's closed refusal, recorded as the only decision attempt, and the run stays "did not finish"
    const late = await host.exec!.port.decideApproval(shown);
    expect(late).toMatchObject({ accepted: false, code: 'LAUNCH_NOT_ACTIVE' });
    expect(host.exec!.decisions).toEqual([shown]);
    expect(bar).toHaveAttribute('data-outcome', 'interrupted');
    expect(within(bar).queryByText(/Run succeeded/)).toBeNull();
  });

  it('a launch record that stays unreadable after the process is gone does not lock the workspace forever (review P2-4): the run is concluded as "could not be followed" and can be dismissed', async () => {
    const { host, bar } = await toReview();
    act(() => { host.exec!.breakStatus('EVIDENCE_STORE_UNREADABLE'); host.exec!.killProcess(); });
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'not_recorded'), { timeout: 6000 });
    expect(within(bar).getByText('The run could not be followed')).toBeInTheDocument();
    expect(within(bar).getAllByText(/EVIDENCE_STORE_UNREADABLE/).length).toBeGreaterThan(0);
    expect(within(bar).getByRole('button', { name: 'New Goal' })).toBeInTheDocument();
    expect(within(bar).queryByText(/Run succeeded/)).toBeNull();
  });
});

describe('looking at another run never moves the active run or its decision', () => {
  it('selecting a historical run while a review waits leaves the banner, the review and the decision target untouched; the live run can be returned to', async () => {
    const { host, store } = await toReview();
    goTo('Runs');
    const history = await screen.findAllByRole('button', { name: /Succeeded|Failed|Unfinished/ });
    fireEvent.click(history[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    expect(store.getState().liveLaunchId).toBeNull();
    const bar = await backToRun();
    expect(within(bar).getByRole('region', { name: 'Change review' })).toBeInTheDocument(); // the review is unaffected
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]).toMatchObject({ launch_id: LAUNCH_ID, decision: 'reject' }); // the ACTIVE launch, not the selected historical run
    expect(host.exec!.decisions[0]!.review_id).not.toBe(store.getState().runId);
  });

  it('"View Details" returns to the live run from any selection, and finishing the run selects the finished (trace) run, ending the live selection', async () => {
    const { host, store } = await toReview();
    goTo('Runs');
    fireEvent.click((await screen.findAllByRole('button', { name: /Succeeded|Failed|Unfinished/ }))[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBe(LAUNCH_ID));
    const bar = await backToRun();
    fireEvent.click(within(bar).getByRole('button', { name: 'View Details' }));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    fireEvent.click(within(await backToRun()).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    act(() => host.exec!.finish());
    await waitFor(() => expect(screen.getByRole('region', { name: 'Current run' })).toHaveAttribute('data-outcome', 'rejected'), { timeout: 4000 }); // a fresh lookup: the page was left and re-entered
    await waitFor(() => expect(store.getState().liveLaunchId).toBeNull());
  });
});

describe('after a terminal outcome', () => {
  it('switching workspace clears the finished run, the goal and the previous workspace\'s selection - nothing is carried over', async () => {
    const { host, bar, store } = await startRun({ execution: { outcome: 'success' }, choices: ['/fixture/empty'] });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'), { timeout: 4000 });
    host.menu('openFolder');
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Current run' })).toBeNull(), { timeout: 4000 });
    expect(store.getState().runId).toBeNull();
    goTo('Project');
    expect(await screen.findByRole('textbox', { name: 'Engineering goal' })).toHaveValue('');
  });
});

/** A fixture host whose chosen reads wait until the test releases them, to land a late answer after the person has moved on. */
class GatedHost extends FixtureHost {
  gates: Array<{ match: (r: KupRequest) => boolean; release: () => void; open: boolean; wait: Promise<void> }> = [];
  hold(match: (r: KupRequest) => boolean) {
    let release: () => void = () => undefined;
    const wait = new Promise<void>((resolve) => { release = resolve; });
    const gate = { match, release, open: false, wait };
    gate.release = () => { gate.open = true; release(); };
    this.gates.push(gate);
    return gate;
  }
  override async query(request: KupRequest): Promise<KupEnvelope> {
    const gate = this.gates.find((g) => !g.open && g.match(request));
    if (gate) await gate.wait;
    return super.query(request);
  }
}
const mountOver = (host: GatedHost) => {
  cleanup();
  const store = createSelectionStore({ ...initialSelection });
  render(<AppShell provider={new HostProvider(host, 'fixture')} registry={createCockpitRegistry()} selection={store} viewportWidth={1440} pollIntervals={FAST_POLL} initialPage="runs" />);
  return store;
};

describe('stale responses never overwrite a newer selection', () => {
  it('a late run list for workspace A that lands after switching to workspace B is dropped', async () => {
    const host = new GatedHost({ workspace: '/fixture/workspace', choices: ['/fixture/empty'] });
    const gate = host.hold((r) => r.operation === 'history.list' && (r as { workspace?: string }).workspace === '/fixture/workspace');
    mountOver(host);
    await screen.findByRole('navigation', { name: 'Destinations' });
    host.menu('openFolder'); // switch to the empty folder while A's list is still outstanding
    await screen.findByText(/No runs yet\./, undefined, { timeout: 4000 }); // B is open while A's list is still held
    act(() => gate.release());
    await new Promise((r) => setTimeout(r, 120));
    expect(screen.queryAllByRole('button', { name: /Succeeded|Failed/ })).toHaveLength(0); // A's runs never appear under B
    expect(screen.getByText(/No runs yet\./)).toBeInTheDocument();
  });

  it('a read child that fails is a typed, visible error and Refresh recovers it - the cockpit never shows stale rows as current', async () => {
    const host = new GatedHost({ workspace: '/fixture/workspace' });
    let failOnce = true;
    const original = host.query.bind(host);
    host.query = async (request: KupRequest) => {
      if (failOnce && request.operation === 'history.list') { failOnce = false; return { schema_version: 1, operation: 'history.list', request_id: 'x', observed_at: 't', source: null, consistency: null, data: null, error: { code: 'HOST_ERROR', message: 'kriya service died (exit 1)' } } as unknown as KupEnvelope; }
      return original(request);
    };
    mountOver(host);
    expect(await screen.findByText(/Runs could not be read/)).toBeInTheDocument();
    expect(screen.getByText(/HOST_ERROR: kriya service died/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Refresh runs' }));
    expect((await screen.findAllByRole('button', { name: /Succeeded|Failed/ })).length).toBeGreaterThan(2);
    expect(screen.queryByText(/Runs could not be read/)).toBeNull();
  });
});

describe('rapid input', () => {
  it('rapid tab switching and repeated refresh leave one consistent selection and mutate no evidence', async () => {
    const host = new GatedHost({ workspace: '/fixture/workspace' });
    const store = mountOver(host);
    fireEvent.click((await screen.findAllByRole('button', { name: /Succeeded/ }))[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    const run = store.getState().runId;
    await waitFor(() => expect(host.calls.some((c) => c.operation === 'evidence.verify')).toBe(true));
    await new Promise((r) => setTimeout(r, 200)); // the run's evidence has been read once; from here a tab switch must read NOTHING more
    const evidenceBefore = host.calls.filter((c) => c.operation.startsWith('evidence.')).length;
    const tabs = ['Verification', 'Changes', 'Context', 'Plan', 'Attempts', 'Evidence', 'Overview'];
    for (let round = 0; round < 3; round += 1) for (const name of tabs) { const tab = screen.queryByRole('tab', { name }); if (tab) fireEvent.click(tab); }
    for (let i = 0; i < 4; i += 1) fireEvent.click(screen.getByRole('button', { name: 'Refresh runs' }));
    await new Promise((r) => setTimeout(r, 150));
    expect(store.getState().centralTab).toBe('overview');
    expect(store.getState().runId === run || store.getState().runId === null).toBe(true); // a refresh may reset the selection; it never selects another run
    // Tab switching re-reads no evidence (the run's evidence is one shared load) and rapid Refresh never re-reads the SELECTED run's store more than once per snapshot.
    expect(host.calls.filter((c) => c.operation.startsWith('evidence.')).length - evidenceBefore).toBe(0);
  });
});

describe('the other order of arrival: a LATE OLD answer never overwrites a NEWER one (hardening, mutation findings)', () => {
  it('run X is selected, then run Y; Y answers first, X answers late: Y stays displayed (never stuck on Loading, never replaced by X)', async () => {
    const host = new GatedHost({ workspace: '/fixture/workspace' });
    const store = mountOver(host);
    await screen.findByRole('navigation', { name: 'Destinations' });
    const runs = await screen.findAllByRole('button', { name: /Succeeded|Failed/ });
    let firstRun = '';
    const holdX = host.hold((r) => { const id = (r as { run_id?: string }).run_id; if (r.operation === 'history.detail' && id && (!firstRun || id === firstRun)) { firstRun = id; return true; } return false; });
    fireEvent.click(runs[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    fireEvent.click(runs[1]!);
    await waitFor(() => expect(store.getState().runId).not.toBe(firstRun));
    const second = store.getState().runId!;
    await waitFor(() => expect(document.body.textContent).not.toMatch(/Loading…/), { timeout: 6000 }); // Y's own answer arrived while X's is still held
    const before = document.querySelector('main')!.textContent;
    act(() => holdX.release()); // X's answer lands LATE
    await new Promise((r) => setTimeout(r, 250));
    expect(store.getState().runId).toBe(second);
    expect(document.body.textContent).not.toMatch(/Loading…/);
    expect(document.querySelector('main')!.textContent).toBe(before);
  });

  it('a late "Load more runs" page for workspace A is dropped after the person switched to workspace B (nothing from A is merged into B)', async () => {
    const host = new GatedHost({ workspace: '/fixture/workspace', scenarios: manyRuns(500), choices: ['/fixture/empty'] });
    const everything = host.query.bind(host);
    host.query = async (request) => {
      const envelope = await everything(request);
      if (request.operation !== 'history.list' || !envelope.data) return envelope;
      const r = request as { limit?: number; cursor?: string; workspace?: string };
      if (r.workspace !== '/fixture/workspace') return envelope;
      const rows = (envelope.data as { runs: unknown[] }).runs;
      const start = r.cursor ? Number(r.cursor) : 0;
      const limit = r.limit ?? 200;
      return { ...envelope, data: { ...(envelope.data as object), runs: rows.slice(start, start + limit), next_cursor: start + limit < rows.length ? String(start + limit) : null } } as typeof envelope;
    };
    mountOver(host);
    await screen.findAllByRole('button', { name: /Succeeded|Failed/ }, { timeout: 8000 });
    const page2 = host.hold((r) => r.operation === 'history.list' && Boolean((r as { cursor?: string }).cursor));
    fireEvent.click(screen.getByRole('button', { name: 'Load more runs' }));
    host.menu('openFolder'); // switch to the empty folder while A's second page is outstanding
    await screen.findByText(/No runs yet\./, undefined, { timeout: 6000 });
    act(() => page2.release());
    await new Promise((r) => setTimeout(r, 250));
    expect(screen.queryAllByRole('button', { name: /Historic|success|failure/ })).toHaveLength(0);
    expect(screen.getByText(/No runs yet\./)).toBeInTheDocument();
  });

  it('a launch.status answer that lands after the execution provider is gone triggers no refresh and does not re-select a run (both for a run that wrote evidence and for one that ended with none)', async () => {
    for (const outcome of ['success', 'refused'] as const) await lateStatusAfterUnmount(outcome);
  });

  async function lateStatusAfterUnmount(outcome: 'success' | 'refused') {
    const host = new GatedHost({ workspace: '/fixture/workspace', execution: { outcome } });
    const store = mountOver(host);
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    await banner();
    const held = host.hold((r) => r.operation === 'launch.status'); // the NEXT poll waits on this read
    await new Promise((r) => setTimeout(r, 150));
    act(() => host.exec!.finish()); // the run ends durably while that poll is still outstanding: when it lands it will read the sealed outcome
    const acquiresBefore = host.calls.filter((c) => c.operation === 'snapshot.acquire').length;
    cleanup(); // the window goes away while that read is outstanding
    store.dispatch({ type: 'selectRun', runId: 'someone-elses-run' });
    act(() => held.release());
    await new Promise((r) => setTimeout(r, 400));
    expect(host.calls.filter((c) => c.operation === 'snapshot.acquire').length).toBe(acquiresBefore); // no refresh after the provider ended
    expect(store.getState().runId).toBe('someone-elses-run'); // and no stale re-selection of the finished run
  }
});
