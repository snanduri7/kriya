/** Daily-use active-run UX: native notifications from Kriya's durable state, Return to run, and the approval-waiting indicator. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { notificationFor, type RunView } from '../core';
import { LAUNCH_ID } from '../providers/fixture/fixtureExecution';
import { renderWorkspaceShell } from '../testing/harness';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('status', { name: 'Active run' });

async function start(execution: Record<string, unknown>, fixture: Record<string, unknown> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution, ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Workspace');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Add clamp with tests.' } });
  await screen.findByRole('region', { name: 'Readiness' });
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
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve changes' }));
    fireEvent.click(await within(bar).findByRole('button', { name: /Approve without viewing diff|Confirm approval/ }));
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
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject changes' }));
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

describe('Return to run and the approval indicator', () => {
  it('while a run is active, "Return to run" selects the live run and opens the run documents from any page or historical selection', async () => {
    const { host, bar, store } = await start({ outcome: 'success' });
    goTo('Runs');
    const history = await screen.findAllByRole('button', { name: /success|failure/ });
    fireEvent.click(history[0]!);
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    expect(store.getState().liveLaunchId).toBeNull();
    goTo('Settings');
    fireEvent.click(within(bar).getByRole('button', { name: 'Return to run' }));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    expect(store.getState().runId).toBe(LAUNCH_ID);
    expect(screen.getByRole('navigation', { name: 'Destinations' })).toBeInTheDocument();
    expect(host.exec!.active()).toBe(true);
    await waitFor(() => expect(document.querySelector('.ck-shell')?.getAttribute('data-page')).toBe('runs')); // the run documents page is showing
  });
  it('"1 approval waiting · Go to review" appears only while a review is pending and focuses that exact review', async () => {
    const { bar } = await reachReview();
    goTo('Runs');
    const button = within(bar).getByRole('button', { name: '1 approval waiting · Go to review' });
    fireEvent.click(button);
    await waitFor(() => expect(within(bar).getByRole('region', { name: 'Change review' })).toHaveFocus());
  });
  it('no approval indicator without a pending review', async () => {
    const { bar } = await start({ outcome: 'success' });
    expect(within(bar).queryByRole('button', { name: /approval waiting/ })).toBeNull();
    expect(within(bar).getByRole('button', { name: 'Return to run' })).toBeInTheDocument();
  });
  it('a clicked notification (the fixed showRun command) returns to the current run', async () => {
    const { host, store } = await reachReview();
    goTo('Settings');
    act(() => host.menu('showRun'));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    await waitFor(() => expect(screen.getByRole('region', { name: 'Change review' })).toHaveFocus());
  });
  it('showRun after the run finished opens its recorded run; with no run it does nothing', async () => {
    const { host, bar, store } = await start({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    await waitFor(() => expect(store.getState().liveLaunchId).toBeNull());
    goTo('Settings');
    act(() => host.menu('showRun'));
    await waitFor(() => expect(store.getState().runId).not.toBeNull());
    fireEvent.click(within(bar).getByRole('button', { name: 'Dismiss' }));
    const before = store.getState();
    act(() => host.menu('showRun'));
    expect(store.getState()).toEqual(before);
  });
  it('the Return and indicator buttons are gone once the run is over', async () => {
    const { host, bar } = await start({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    expect(within(bar).queryByRole('button', { name: 'Return to run' })).toBeNull();
    expect(within(bar).queryByRole('button', { name: /approval waiting/ })).toBeNull();
  });
});
