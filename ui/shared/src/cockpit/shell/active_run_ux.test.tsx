/** Daily-use active-run UX: native notifications from Kriya's durable state, the compact run strip on every other page, Return to Run, and ACTION REQUIRED. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { notificationFor, type RunView } from '../core';
import { LAUNCH_ID } from '../providers/fixture/fixtureExecution';
import { renderWorkspaceShell } from '../testing/harness';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });

async function start(execution: Record<string, unknown>, fixture: Record<string, unknown> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution, ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Add clamp with tests.' } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  return { ...mounted, bar: await banner() };
}
const reachReview = async () => {
  const s = await start({ outcome: 'review' });
  for (let i = 0; i < 3; i += 1) act(() => s.host.exec!.advance());
  await within(s.bar).findByRole('region', { name: 'Change review' });
  return s;
};

describe('notificationFor (pure): only durable states notify', () => {
  const base = { launchId: LAUNCH_ID, workspace: '/w' } as unknown as RunView;
  const outcome = (kind: string) => ({ ...base, stage: 'finished', review: null, outcome: { kind } }) as unknown as RunView;
  it.each([
    ['success', 'run_succeeded'], ['failure', 'run_unsuccessful'], ['needs_review', 'run_unsuccessful'], ['refused', 'run_unsuccessful'], ['interrupted', 'run_unsuccessful'],
    ['not_recorded', 'run_unsuccessful'], ['approval_required', 'run_unsuccessful'], ['review_expired', 'run_unsuccessful'],
  ])('a recorded %s end asks for %s', (kind, expected) => expect(notificationFor(outcome(kind))?.kind).toBe(expected));
  it('the person\'s own rejection never notifies, and neither does a run that is merely running or starting', () => {
    expect(notificationFor(outcome('rejected'))).toBeNull();
    expect(notificationFor({ ...base, stage: 'running', review: null, outcome: null } as unknown as RunView)).toBeNull();
    expect(notificationFor({ ...base, stage: 'starting', review: null, outcome: null } as unknown as RunView)).toBeNull();
  });
  it('a pending review in a live run asks for approval_required, keyed by the review (a new review is a new event)', () => {
    const live = (id: string) => ({ ...base, stage: 'running', outcome: null, review: { review_id: id } }) as unknown as RunView;
    expect(notificationFor(live('ra-1'))).toEqual({ kind: 'approval_required', key: `approval_required:${LAUNCH_ID}:ra-1` });
    expect(notificationFor(live('ra-2'))!.key).not.toBe(notificationFor(live('ra-1'))!.key);
  });
  it('a finished run with a stale review object is the outcome, not an approval', () => {
    expect(notificationFor({ ...base, stage: 'finished', review: { review_id: 'ra-1' }, outcome: { kind: 'success' } } as unknown as RunView)?.kind).toBe('run_succeeded');
  });
});

describe('notifications over a whole run', () => {
  it('a review asks for exactly ONE approval notification (polling and re-rendering do not repeat it), then a success asks for one run_succeeded', async () => {
    const { host, bar } = await reachReview();
    await waitFor(() => expect(host.exec!.notifications).toEqual([{ kind: 'approval_required', launch_id: LAUNCH_ID }]));
    await new Promise((r) => setTimeout(r, 150));
    expect(host.exec!.notifications).toHaveLength(1);
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve & Continue' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    await waitFor(() => expect(host.exec!.notifications.map((n) => n.kind)).toEqual(['approval_required', 'run_succeeded']));
  });
  it('a failed run asks for run_unsuccessful; the person\'s own rejection asks for nothing further', async () => {
    const failed = await start({ outcome: 'failure' });
    act(() => failed.host.exec!.finish());
    await waitFor(() => expect(failed.bar).toHaveAttribute('data-outcome', 'failure'));
    await waitFor(() => expect(failed.host.exec!.notifications.map((n) => n.kind)).toEqual(['run_unsuccessful']));
  });
  it('rejection: the approval notification was asked, the rejection itself is silent', async () => {
    const { host, bar } = await reachReview();
    await waitFor(() => expect(host.exec!.notifications).toHaveLength(1));
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
    await new Promise((r) => setTimeout(r, 150));
    expect(host.exec!.notifications.map((n) => n.kind)).toEqual(['approval_required']);
  });
  it('a run that died while waiting for review asks for run_unsuccessful, never for success', async () => {
    const { host, bar } = await reachReview();
    act(() => host.exec!.killProcess());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'interrupted'), { timeout: 4000 });
    await waitFor(() => expect(host.exec!.notifications.map((n) => n.kind)).toEqual(['approval_required', 'run_unsuccessful']));
  });
  it('a host without a notification port starts and finishes a run normally', async () => {
    const { host, bar } = await start({ outcome: 'success' });
    (host.execution as { notify?: unknown }).notify = undefined;
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
  });
});

const strip = () => screen.findByRole('status', { name: 'Active run' });
const onPage = () => document.querySelector('.ck-shell')?.getAttribute('data-page');

describe('the compact run strip: findable from every page, Return to Run, and the ACTION REQUIRED state', () => {
  it('the strip is on Runs, Skills, Tools and Settings while a run is active, and absent on the Project page (where the run is the page)', async () => {
    const { bar } = await start({ outcome: 'success' });
    expect(onPage()).toBe('project');
    expect(screen.queryByRole('status', { name: 'Active run' })).toBeNull();
    expect(bar).toHaveAttribute('data-state');
    for (const page of ['Runs', 'Skills', 'Tools', 'Settings']) {
      goTo(page);
      const s = await strip();
      expect(within(s).getByText('Run active')).toBeInTheDocument();
      expect(within(s).getByRole('button', { name: 'Return to Run' })).toBeInTheDocument();
    }
    goTo('Project');
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Active run' })).toBeNull());
  });
  it('while a run is active, "Return to Run" selects the live run and returns to the Project page from any page or historical selection', async () => {
    const { host, store } = await start({ outcome: 'success' });
    goTo('Runs');
    const history = await screen.findAllByRole('button', { name: /Succeeded|Failed/ });
    fireEvent.click(history[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    expect(store.getState().liveLaunchId).toBeNull();
    goTo('Settings');
    fireEvent.click(within(await strip()).getByRole('button', { name: 'Return to Run' }));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    expect(store.getState().runId).toBe(LAUNCH_ID);
    expect(host.exec!.active()).toBe(true);
    await waitFor(() => expect(onPage()).toBe('project'));
    expect(await banner()).toBeInTheDocument();
  });
  it('a pending review turns the strip into ACTION REQUIRED, whose Review button returns to that exact review and focuses it', async () => {
    await reachReview();
    goTo('Runs');
    const s = await strip();
    expect(within(s).getByText('ACTION REQUIRED')).toBeInTheDocument();
    expect(within(s).getByText('Change approval waiting')).toBeInTheDocument();
    expect(s).toHaveClass('ck-attention');
    expect(within(s).queryByRole('button', { name: 'Return to Run' })).toBeNull();
    expect(within(s).queryByRole('button', { name: /Approve|Reject/ })).toBeNull(); // the decision is taken in one place only: the Project page
    fireEvent.click(within(s).getByRole('button', { name: 'Review' }));
    await waitFor(() => expect(onPage()).toBe('project'));
    await waitFor(() => expect(within(screen.getByRole('region', { name: 'Current run' })).getByRole('region', { name: 'Change review' })).toHaveFocus());
  });
  it('no ACTION REQUIRED without a pending review', async () => {
    await start({ outcome: 'success' });
    goTo('Skills');
    const s = await strip();
    expect(within(s).queryByText('ACTION REQUIRED')).toBeNull();
    expect(within(s).queryByRole('button', { name: 'Review' })).toBeNull();
    expect(within(s).getByRole('button', { name: 'Return to Run' })).toBeInTheDocument();
  });
  it('a clicked notification (the fixed showRun command) returns to the current run', async () => {
    const { host, store } = await reachReview();
    goTo('Settings');
    act(() => host.menu('showRun'));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Change review' })).toHaveFocus());
    expect(onPage()).toBe('project');
  });
  it('showRun after the run finished opens its recorded run; with no run it does nothing', async () => {
    const { host, bar, store } = await start({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    await waitFor(() => expect(store.getState().liveLaunchId).toBeNull());
    goTo('Settings');
    act(() => host.menu('showRun'));
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    await waitFor(() => expect(onPage()).toBe('project'));
    fireEvent.click(within(await banner()).getByRole('button', { name: 'New Goal' }));
    const before = store.getState();
    act(() => host.menu('showRun'));
    expect(store.getState()).toEqual(before);
  });
  it('the Runs list pins an "Active run" row while a run is active (with the approval hint), selecting the live run; it disappears with the run', async () => {
    const { host, store } = await reachReview();
    goTo('Runs');
    const row = await screen.findByRole('button', { name: 'Active run - return to it' });
    expect(row).toHaveTextContent('approval waiting');
    fireEvent.click(row);
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Active run - return to it' })).toHaveAttribute('aria-selected', 'true'));
    fireEvent.click(within(await strip()).getByRole('button', { name: 'Review' }));
    const bar = await banner();
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
    goTo('Runs');
    expect(screen.queryByRole('button', { name: 'Active run - return to it' })).toBeNull();
  });
  it('once the run is over the strip offers View Result and Dismiss instead of Return to Run, and the Project page offers New Goal', async () => {
    const { host, bar } = await start({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    expect(within(bar).queryByRole('button', { name: 'Return to Run' })).toBeNull();
    expect(within(bar).getByRole('button', { name: 'New Goal' })).toBeInTheDocument();
    goTo('Skills');
    const s = await strip();
    expect(within(s).getByText('Run completed')).toBeInTheDocument();
    expect(within(s).queryByRole('button', { name: 'Return to Run' })).toBeNull();
    fireEvent.click(within(s).getByRole('button', { name: 'Dismiss' }));
    await waitFor(() => expect(screen.queryByRole('status', { name: 'Active run' })).toBeNull());
  });
});
