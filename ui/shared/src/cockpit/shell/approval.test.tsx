/** Human review of a change through the real shell over the scripted fixture host (GUI final). The host forwards ONE typed decision; every word about the run
 * comes from Kriya's records (the pending review in the launch record, then the sealed outcome). Nothing is auto-approved and nothing is inferred. */
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { activeReview, createDecider, deriveOutcome, type RunView } from '../core';
import type { LaunchStatus } from '../../model/kup';
import { renderWorkspaceShell } from '../testing/harness';
import { LAUNCH_ID, REVIEW_DIGEST, REVIEW_ID, REVIEW_REASON } from '../providers/fixture/fixtureExecution';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('status', { name: 'Active run' });
const GOAL = 'Add a clamp(value, low, high) function with tests.';

/** Open the Workspace page, start the run and move the scripted launch to the frame where Kriya waits on the review. */
async function reachReview(fixture: Record<string, unknown> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'review' }, ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Workspace');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findByText('fixture-model:7b');
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
  await within(bar).findByRole('region', { name: 'Change review' });
  return { ...mounted, bar };
}

describe('the review of a change', () => {
  it('shows "Approval required" with Kriya\'s reviewer text VERBATIM, the files and their sizes, and sends nothing until a button is pressed', async () => {
    const { host, bar } = await reachReview();
    expect(within(bar).getAllByText('Approval required').length).toBeGreaterThan(0);
    const review = within(bar).getByRole('region', { name: 'Change review' });
    expect(within(review).getByText('src/clamp.py')).toBeInTheDocument();
    expect(within(within(review).getByRole('list', { name: 'Files in this change' })).getByText(/\+3 −0/)).toBeInTheDocument();
    expect(review.querySelector('pre')?.textContent).toBe(REVIEW_REASON);
    expect(within(review).getByRole('button', { name: 'Approve changes' })).toBeEnabled();
    expect(within(review).getByRole('button', { name: 'Reject changes' })).toBeEnabled();
    expect(host.exec!.decisions).toEqual([]);
  });

  it('Approve asks for confirmation, then sends ONE typed decision carrying exactly the identities Kriya gave the review; the run then continues and ends from Kriya\'s records', async () => {
    const { host, bar } = await reachReview();
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve changes' }));
    expect(host.exec!.decisions).toEqual([]); // not yet: confirmation first
    const dialog = within(bar).getByRole('alertdialog', { name: 'Confirm approval' });
    fireEvent.click(within(dialog).getByRole('button', { name: 'Confirm approval' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]).toEqual({ workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: REVIEW_ID, candidate_digest: REVIEW_DIGEST, decision: 'approve' });
    expect(Object.keys(host.exec!.decisions[0]!).sort()).toEqual(['candidate_digest', 'decision', 'launch_id', 'review_id', 'workspace']);
    // Kriya's launch record now says APPROVED: the banner says so, offers no further decision, and the run is still running
    await within(bar).findByText(/Your approval is recorded by Kriya/);
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
    expect(bar).toHaveAttribute('data-outcome', ''); // no outcome yet
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    expect(within(bar).getByText('Run succeeded')).toBeInTheDocument();
    expect(within(bar).getByText(/the change was approved by you/)).toBeInTheDocument();
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
  });

  it('Reject sends one decision and the durable outcome is the recorded human rejection - no retry is started', async () => {
    const { host, bar } = await reachReview();
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject changes' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]!.decision).toBe('reject');
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
    expect(within(bar).getByText('Changes rejected')).toBeInTheDocument();
    expect(within(bar).getByText(/Kriya applied nothing/)).toBeInTheDocument();
    expect(host.exec!.starts).toHaveLength(1);
  });

  it('a double click sends exactly one decision; the buttons are gone while it is in flight', async () => {
    const { host, bar } = await reachReview();
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve changes' }));
    const confirm = within(bar).getByRole('button', { name: 'Confirm approval' });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(host.exec!.decisions.length).toBeGreaterThan(0));
    expect(host.exec!.decisions).toHaveLength(1);
    await within(bar).findByText(/Your approval is recorded by Kriya/);
    expect(within(bar).queryByRole('button', { name: 'Approve changes' })).toBeNull();
    expect(within(bar).queryByRole('button', { name: 'Reject changes' })).toBeNull();
  });

  it('a decision for a review that was superseded fails closed: Kriya\'s refusal is shown verbatim and the run keeps waiting', async () => {
    const { host, bar } = await reachReview();
    act(() => host.exec!.supersedeReview());
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject changes' }));
    const alert = await within(bar).findByRole('alert');
    expect(alert).toHaveTextContent('Kriya did not accept the decision');
    expect(alert).toHaveTextContent('STALE_REVIEW');
    expect(alert).toHaveTextContent('Review it again before deciding');
    expect(bar).not.toHaveAttribute('data-outcome', 'rejected');
    expect(within(bar).getByRole('button', { name: 'Approve changes' })).toBeEnabled(); // nothing was recorded; the person can decide again
  });

  it('Review changes opens the existing Changes module on the run\'s LIVE evidence (no second diff implementation) and Review validation opens Validation', async () => {
    const { bar, store } = await reachReview({ evidenceContent: true });
    fireEvent.click(within(bar).getByRole('button', { name: 'Review changes' }));
    await waitFor(() => expect(store.getState().liveLaunchId).toBe(LAUNCH_ID));
    expect(store.getState().runId).toBe(LAUNCH_ID);
    expect(store.getState().centralTab).toBe('changes');
    const doc = await screen.findByRole('region', { name: 'Changes' });
    expect((await within(doc).findAllByText(/new file: no baseline exists/)).length).toBeGreaterThan(0);
    fireEvent.click(within(bar).getByRole('button', { name: 'Review validation' }));
    await waitFor(() => expect(store.getState().centralTab).toBe('validation'));
    expect(await screen.findByRole('region', { name: 'Validation' })).toBeInTheDocument();
  });

  it('with content visibility off the review says the diff text is not shown and still presents Kriya\'s metadata (nothing is manufactured)', async () => {
    const { bar } = await reachReview({ evidenceContent: false });
    const review = within(bar).getByRole('region', { name: 'Change review' });
    expect(within(review).getByText(/content visibility is off, so the diff text is not shown here/)).toBeInTheDocument();
    expect(within(review).getByText('src/clamp.py')).toBeInTheDocument();
  });

  it('a window that reloads while a review is pending finds it again from the host and Kriya\'s records, not from its own memory', async () => {
    const first = await reachReview();
    cleanup();
    // a fresh renderer over the SAME host (the execution process and the pending review live on)
    const { render } = await import('@testing-library/react');
    const { HostProvider } = await import('../providers/hostProvider');
    const { createSelectionStore } = await import('../core');
    const { initialSelection } = await import('../../state/selection');
    const { AppShell } = await import('./AppShell');
    const { createCockpitRegistry } = await import('../composition');
    const { FAST_POLL } = await import('../testing/harness');
    render(<AppShell provider={new HostProvider(first.host, 'fixture')} registry={createCockpitRegistry()} selection={createSelectionStore({ ...initialSelection })} viewportWidth={1440} pollIntervals={FAST_POLL} />);
    const bar = await banner();
    expect(await within(bar).findByRole('region', { name: 'Change review' })).toBeInTheDocument();
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject changes' }));
    await waitFor(() => expect(first.host.exec!.decisions).toHaveLength(1));
  });
});

const status = (over: Partial<LaunchStatus['launch']>, run: Partial<LaunchStatus['run_record']> = {}): LaunchStatus => ({
  launch: { launch_id: LAUNCH_ID, workspace: { path: '/w', identity: 'x' }, requested_at: 't', goal: { sha256: 'g', chars: 1 }, options: {}, phase: 'ENDED', run: { run_id: LAUNCH_ID, started_at: 't', base_revision: null },
    ended: { at: 't', exit_code: 1, workflow_status: null, quality_gates_passed: false, failure_category: null, trace_run_id: 'fx-launched', error: null, note: 'n' }, history: [], process: null, approval_required: null, declined_decisions: [], ...over },
  run_record: { found: true, unreadable: null, lifecycle_state: 'FAILURE', terminal_status: 'FAILURE', commit_result: null, revision: 2, ...run },
  evidence: { state: 'sealed', line_count: 9, sealed: true }, workspace_lock: { held: false, held_by_this_launch: false }, liveness: 'ended', liveness_note: 'n',
}) as unknown as LaunchStatus;
const review = (statusWord: string, decision: Record<string, unknown> | null = null) => ({ review_id: REVIEW_ID, sequence: 1, kind: 'change_approval', status: statusWord, requested_at: 't', expires_at: 't', candidate_digest: REVIEW_DIGEST, files: [], reason: 'r', reason_truncated: false, reason_sha256: 's', decision });

describe('outcomes of a reviewed run come from Kriya\'s durable records only', () => {
  it('a recorded rejection is "Changes rejected" unless the run record says SUCCESS (the run record always wins)', () => {
    expect(deriveOutcome(status({ pending_approval: review('REJECTED', { decision: 'reject' }) } as never), false, null)?.kind).toBe('rejected');
    expect(deriveOutcome(status({ pending_approval: review('REJECTED') } as never, { terminal_status: 'SUCCESS', commit_result: 'COMMITTED' }), false, null)?.kind).toBe('success');
  });
  it('an expired review is its own outcome and says nothing was applied', () => {
    const outcome = deriveOutcome(status({ pending_approval: review('EXPIRED', { decision: null, source: 'host-gone' }) } as never), false, null);
    expect(outcome?.kind).toBe('review_expired');
    expect(outcome?.detail).toMatch(/nothing was applied/);
  });
  it('an approved run that then FAILED is still a failure (approval is not success)', () => {
    const outcome = deriveOutcome(status({ pending_approval: review('APPROVED', { decision: 'approve' }) } as never), false, null);
    expect(outcome?.kind).toBe('failure');
  });
  it('a review still PENDING at the end of the launch (the process went away) is not shown as decided', () => {
    const outcome = deriveOutcome(status({ pending_approval: review('PENDING') } as never), false, null);
    expect(outcome?.kind).not.toBe('rejected');
    expect(outcome?.kind).not.toBe('success');
  });
});

describe('which review can be decided, and the one-decision guard', () => {
  const live = (reviewStatus: string, over: { phase?: string; liveness?: string } = {}) => status({ phase: over.phase ?? 'RUNNING', pending_approval: review(reviewStatus) as never, ended: null } as never, {}) as LaunchStatus & { liveness: string };
  it('only a PENDING review in a RUNNING, live launch is actionable', () => {
    const cases: Array<[string, string, string, boolean]> = [
      ['PENDING', 'RUNNING', 'live', true], ['APPROVED', 'RUNNING', 'live', false], ['REJECTED', 'RUNNING', 'live', false], ['EXPIRED', 'RUNNING', 'live', false],
      ['PENDING', 'ENDED', 'ended', false], ['PENDING', 'STARTING', 'starting', false], ['PENDING', 'STARTING', 'live', false], ['PENDING', 'ENDED', 'live', false], ['PENDING', 'RUNNING', 'interrupted', false],
    ];
    for (const [reviewStatus, phase, liveness, actionable] of cases) {
      const s = live(reviewStatus, { phase });
      (s as { liveness: string }).liveness = liveness;
      expect(activeReview(s) !== null, `${reviewStatus}/${phase}/${liveness}`).toBe(actionable);
    }
    expect(activeReview(null)).toBeNull();
  });

  it('two decisions in the same tick reach the host once, with the identities of the review that was pending', async () => {
    const sent: unknown[] = [];
    let release: (v: never) => void = () => undefined;
    const port = { startRun: async () => { throw new Error('no'); }, state: async () => { throw new Error('no'); }, decideApproval: (request: unknown) => { sent.push(request); return sent.length > 1 ? Promise.resolve({ accepted: false, code: 'X', message: 'x' }) : new Promise((resolve) => { release = resolve as never; }); } };
    const states: string[] = [];
    const pending = review('PENDING') as unknown as NonNullable<RunView['review']>;
    const decide = createDecider({ port: port as never, run: () => ({ launchId: LAUNCH_ID, workspace: '/w', review: pending } as RunView), set: (s) => states.push(s.state) });
    const first = decide('approve');
    const second = decide('reject');
    await second;
    expect(sent).toHaveLength(1);
    expect(sent[0]).toEqual({ workspace: '/w', launch_id: LAUNCH_ID, review_id: REVIEW_ID, candidate_digest: REVIEW_DIGEST, decision: 'approve' });
    release({ accepted: true, code: 'ACCEPTED', message: 'ok', launch_id: null, review_id: null, decision: null, decided_at: null } as never);
    await first;
    expect(states).toEqual(['submitting', 'accepted']);
    await decide('approve'); // free again afterwards (a new review or a refusal)
    expect(sent).toHaveLength(2);
  });

  it('nothing is sent when no review is pending', async () => {
    const sent: unknown[] = [];
    const decide = createDecider({ port: { decideApproval: async (r: unknown) => { sent.push(r); } } as never, run: () => ({ launchId: LAUNCH_ID, workspace: '/w', review: null } as RunView), set: () => undefined });
    await decide('approve');
    expect(sent).toEqual([]);
  });
});
