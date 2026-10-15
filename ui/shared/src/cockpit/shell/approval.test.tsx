/** Human review of a change through the real shell over the scripted fixture host (GUI final). The host forwards ONE typed decision; every word about the run
 * comes from Kriya's records (the pending review in the launch record, then the sealed outcome). Nothing is auto-approved and nothing is inferred. */
import { act, cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { activeReview, createDecider, deriveOutcome, type RunView } from '../core';
import type { LaunchStatus } from '../../model/kup';
import { renderWorkspaceShell } from '../testing/harness';
import approvalSource from '../core/ApprovalReview.tsx?raw';
import { LAUNCH_ID, REVIEW_DIGEST, REVIEW_ID } from '../providers/fixture/fixtureExecution';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });
const GOAL = 'Add a clamp(value, low, high) function with tests.';

/** Open the Workspace page, start the run and move the scripted launch to the frame where Kriya waits on the review. */
async function reachReview(fixture: Record<string, unknown> = {}, opts: { holdReview?: boolean } = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'review' }, ...fixture } });
  if (opts.holdReview) {
    const real = mounted.host.query.bind(mounted.host);
    vi.spyOn(mounted.host, 'query').mockImplementation(async (request) => (request.operation === 'change_approval.review' ? new Promise(() => undefined) : real(request)));
  }
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
  await within(bar).findByRole('region', { name: 'Change review' });
  return { ...mounted, bar };
}

describe('the review of a change: the EXACT change first, then why, verification, decision', () => {
  const diff = () => screen.findByTestId('proposed-change');

  it('loads the exact proposed change of THIS review from Kriya (typed hunks, +/- and line numbers) before anything can be approved, and sends nothing until a button is pressed', async () => {
    const { host, bar } = await reachReview();
    const review = within(bar).getByRole('region', { name: 'Change review' });
    const shown = await within(review).findByTestId('proposed-change');
    expect(host.exec!.reviewReads.at(-1)).toEqual({ launch_id: LAUNCH_ID, review_id: REVIEW_ID }); // Kriya was asked for exactly this review
    expect(within(shown).getByText('src/clamp.py')).toBeInTheDocument();
    const added = Array.from(shown.querySelectorAll('[data-diff-row="add"]')).map((r) => r.textContent);
    expect(added.some((t) => t?.includes('def clamp(value, low, high):'))).toBe(true);
    expect(within(shown).getByLabelText('3 lines added')).toHaveTextContent('+3');
    expect(within(review).getByRole('button', { name: 'Approve & Continue' })).toBeEnabled();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(host.exec!.decisions).toEqual([]);
  });

  it('puts the change before the explanation, the explanation before the verification and the decision last; digests and ids are under Technical details', async () => {
    const { bar } = await reachReview();
    const review = within(bar).getByRole('region', { name: 'Change review' });
    await within(review).findByTestId('proposed-change');
    const order = ['proposed-change', 'change-why'].map((id) => review.querySelector(`[data-testid="${id}"]`)!);
    expect(order[0]!.compareDocumentPosition(order[1]!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(review).getByTestId('change-why')).toHaveTextContent('The change implements the goal and keeps the public signature.'); // the reviewer\'s words, not the policy preamble marker
    expect(review.textContent).not.toMatch(/=== Automated Code Review ===/);
    const tech = review.querySelector('details.ck-tech')!;
    expect(tech.hasAttribute('open')).toBe(false);
    expect(tech.textContent).toContain(REVIEW_ID);
    const primary = (review.textContent ?? '').replace(tech.textContent ?? '', '');
    expect(primary).not.toContain(REVIEW_ID);
    expect(primary).not.toMatch(/sha256|candidate/);
    expect(within(review).getByRole('list', { name: 'Verification so far' })).toHaveTextContent(/Build passed/);
  });

  it('says what each decision does BEFORE it is pressed', async () => {
    const { bar } = await reachReview();
    const help = within(within(bar).getByRole('region', { name: 'Change review' })).getByTestId('decision-help');
    expect(help).toHaveTextContent(/Approve & Continue: Kriya finishes its checks and, if they pass, applies this change to your folder/);
    expect(help).toHaveTextContent(/Reject: nothing is applied and this run ends/);
  });

  it('Approve shows the change and ONE click sends ONE typed decision carrying the digest of the change that was SHOWN (no second renderer confirmation); the run then continues and ends from Kriya\'s records', async () => {
    const { host, bar } = await reachReview();
    await diff();
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve & Continue' }));
    expect(within(bar).queryByRole('alertdialog')).toBeNull(); // the exact diff was the review; there is no "Confirm approval" step
    expect(within(bar).queryByRole('button', { name: /Confirm approval/ })).toBeNull();
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]).toEqual({ workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: REVIEW_ID, candidate_digest: REVIEW_DIGEST, decision: 'approve' });
    expect(Object.keys(host.exec!.decisions[0]!).sort()).toEqual(['candidate_digest', 'decision', 'launch_id', 'review_id', 'workspace']);
    await within(bar).findByText(/Your change was approved\. Kriya is running final checks\./);
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
    expect(bar).toHaveAttribute('data-outcome', ''); // no outcome yet
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    expect(within(bar).getByText('Run completed')).toBeInTheDocument();
    expect(within(bar).getByText(/You approved the change/)).toBeInTheDocument();
  });

  it('Reject works with the change shown and sends one decision; the durable outcome is the recorded human rejection - no retry is started', async () => {
    const { host, bar } = await reachReview();
    await diff();
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]!.decision).toBe('reject');
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
    expect(within(bar).getByText(/Kriya applied nothing/)).toBeInTheDocument();
    expect(host.exec!.starts).toHaveLength(1);
  });

  it('a double click sends exactly one decision; the buttons are gone while it is in flight', async () => {
    const { host, bar } = await reachReview();
    await diff();
    const approve = within(bar).getByRole('button', { name: 'Approve & Continue' });
    fireEvent.click(approve);
    fireEvent.click(approve);
    await waitFor(() => expect(host.exec!.decisions.length).toBeGreaterThan(0));
    expect(host.exec!.decisions).toHaveLength(1);
    await within(bar).findByText(/Your change was approved/);
    expect(within(bar).queryByRole('button', { name: 'Approve & Continue' })).toBeNull();
    expect(within(bar).queryByRole('button', { name: 'Reject' })).toBeNull();
  });

  it('while the change is loading, Approve does not exist (only Reject and Open in Editor)', async () => {
    const { host, bar } = await reachReview({}, { holdReview: true });
    const review = within(bar).getByRole('region', { name: 'Change review' });
    expect(within(review).getByText('Loading the proposed change…')).toBeInTheDocument();
    expect(within(review).queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(host.exec!.decisions).toEqual([]);
  });

  it('a candidate that changes while the person is reading is announced; the diff reloads for the NEW review and approving it is a fresh, explicit act with the NEW digest', async () => {
    const { host, bar } = await reachReview();
    await diff();
    act(() => host.exec!.replaceReview()); // Kriya raises a NEW review (another candidate) for the same launch
    const note = await within(bar).findByTestId('change-changed');
    expect(note).toHaveTextContent('The proposed change changed while you were reviewing it. Review the updated change before approving.');
    await waitFor(() => expect(host.exec!.reviewReads.at(-1)!.review_id).not.toBe(REVIEW_ID));
    const shown = await diff();
    expect(shown.textContent).toContain('return min(max(value, low), high)'); // the NEW candidate\'s text, never the earlier one\'s
    expect(shown.textContent).not.toContain('return max(low, min(value, high))');
    expect(host.exec!.decisions).toEqual([]); // nothing was approved on the person\'s behalf
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve & Continue' }));
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]!.review_id).not.toBe(REVIEW_ID);
    expect(host.exec!.decisions[0]!.candidate_digest).not.toBe(REVIEW_DIGEST);
  });

  it('a decision for a review that was superseded fails closed: Kriya\'s refusal is shown, the person is told to review the updated change, and the run keeps waiting', async () => {
    const { host, bar } = await reachReview();
    await diff();
    act(() => host.exec!.supersedeReview());
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
    const alert = await within(bar).findByTestId('change-changed');
    expect(alert).toHaveTextContent('Review the updated change before approving');
    expect(bar).not.toHaveAttribute('data-outcome', 'rejected');
  });

  it.each([
    ['Kriya cannot prove the stored change is the reviewed one', { fault: 'unverified' as const }, /could not confirm that the stored change is the one under review/],
    ['Kriya did not keep the change in a form it can show', { fault: 'unavailable' as const }, /did not keep this change/],
    ['the change is larger than one response can carry', { truncated: true }, /larger than can be shown in full/],
  ])('%s: "Unable to display the proposed change", NO approve control, Reject and Open in Editor remain', async (_name, review, reason) => {
    const { host, bar } = await reachReview({ execution: { outcome: 'review', review } });
    const unable = await within(bar).findByTestId('unable-to-display');
    expect(unable).toHaveTextContent('Unable to display the proposed change');
    expect(unable).toHaveTextContent(reason);
    expect(unable).toHaveTextContent(/approval is not offered/);
    const region = within(bar).getByRole('region', { name: 'Change review' });
    expect(within(region).queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(within(region).queryByRole('alertdialog')).toBeNull();
    expect(within(region).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(within(region).getByRole('button', { name: 'Open in Editor' })).toBeInTheDocument();
    expect(region.textContent).not.toMatch(/without viewing/i);
    fireEvent.click(within(region).getByRole('button', { name: 'Reject' })); // a human may always decline
    await waitFor(() => expect(host.exec!.decisions).toHaveLength(1));
    expect(host.exec!.decisions[0]!.decision).toBe('reject');
  });

  it('a change whose digest is not the pending review\'s is not shown as approvable: Kriya answered about a different candidate', async () => {
    const { host, bar } = await reachReview({ execution: { outcome: 'review', review: { digestMismatch: true } } });
    const unable = await within(bar).findByTestId('unable-to-display');
    expect(unable).toBeInTheDocument();
    expect(within(bar).queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(host.exec!.decisions).toEqual([]);
  });

  it('a review that is no longer pending when its change is loaded (superseded or expired) is not approvable', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'review' } } });
    const real = mounted.host.query.bind(mounted.host);
    vi.spyOn(mounted.host, 'query').mockImplementation(async (request) => { if (request.operation === 'change_approval.review') await gate; return real(request); });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    const bar = await banner();
    for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
    await within(bar).findByRole('region', { name: 'Change review' });
    act(() => mounted.host.exec!.supersedeReview()); // the candidate was replaced before the change finished loading
    release();
    expect(await within(bar).findByTestId('unable-to-display')).toBeInTheDocument();
    expect(within(bar).queryByRole('button', { name: /Approve/ })).toBeNull();
  });

  it('"Approve without viewing diff" exists nowhere: not in the source, not in any state of the review', async () => {
    const { bar } = await reachReview();
    await diff();
    fireEvent.click(within(bar).getByRole('button', { name: 'Approve & Continue' }));
    expect(document.body.textContent).not.toMatch(/without viewing/i);
    expect(approvalSource).not.toMatch(/without viewing|Diff hidden by Kriya content-visibility/i);
  });

  it('Open in Editor opens the first file that already exists in the project; a change that only adds files has nothing to open', async () => {
    const { host, bar } = await reachReview();
    await diff();
    const open = within(bar).getByRole('button', { name: 'Open in Editor' });
    expect(open).toBeDisabled(); // the fixture change adds src/clamp.py (a new file)
    expect(host.ideCalls).toEqual([]);
  });

  it('content visibility off does not hide the proposed change (the review is approval-scoped), and nothing here turns content visibility on', async () => {
    const { host, bar } = await reachReview({ evidenceContent: false });
    expect(await within(bar).findByTestId('proposed-change')).toBeInTheDocument();
    goTo('Settings');
    expect(document.body.textContent).not.toMatch(/content[- ]visibility.*(enable|turn on|allow)|enable content|show content/i);
    for (const key of Object.keys(host)) expect(key).not.toMatch(/visibility/i);
  });

  it('the review reaches Kriya only through the typed provider read: no evidence content operation, no file access, no arbitrary path', () => {
    expect(approvalSource).toMatch(/provider\.changeApprovalReview\(workspace!, launchId, reviewId!, signal\)/);
    expect(approvalSource).not.toMatch(/changeContent|callContent|evidence\.change_content|from 'node:|require\(|fetch\(|XMLHttpRequest/);
  });

  it('a window that reloads while a review is pending finds it again from the host and Kriya\'s records, and shows the change again', async () => {
    const first = await reachReview();
    cleanup();
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
    expect(await within(bar).findByTestId('proposed-change')).toBeInTheDocument();
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject' }));
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
    const first = decide('approve', REVIEW_ID, REVIEW_DIGEST);
    const second = decide('reject', REVIEW_ID, REVIEW_DIGEST);
    await second;
    expect(sent).toHaveLength(1);
    expect(sent[0]).toEqual({ workspace: '/w', launch_id: LAUNCH_ID, review_id: REVIEW_ID, candidate_digest: REVIEW_DIGEST, decision: 'approve' });
    release({ accepted: true, code: 'ACCEPTED', message: 'ok', launch_id: null, review_id: null, decision: null, decided_at: null } as never);
    await first;
    expect(states).toEqual(['submitting', 'accepted']);
    await decide('approve', REVIEW_ID, REVIEW_DIGEST); // free again afterwards (a new review or a refusal)
    expect(sent).toHaveLength(2);
  });

  it('nothing is sent when no review is pending', async () => {
    const sent: unknown[] = [];
    const decide = createDecider({ port: { decideApproval: async (r: unknown) => { sent.push(r); } } as never, run: () => ({ launchId: LAUNCH_ID, workspace: '/w', review: null } as RunView), set: () => undefined });
    await decide('approve', REVIEW_ID, REVIEW_DIGEST);
    expect(sent).toEqual([]);
  });
});

describe('the decision is about the review that was displayed', () => {
  it('a decision for a review that is no longer the pending one sends nothing and says so', async () => {
    const sent: unknown[] = [];
    const states: Array<{ state: string; code?: string }> = [];
    const pending = review('PENDING') as unknown as NonNullable<RunView['review']>;
    const decide = createDecider({ port: { decideApproval: async (r: unknown) => { sent.push(r); } } as never, run: () => ({ launchId: LAUNCH_ID, workspace: '/w', review: pending } as RunView), set: (s) => states.push(s as never) });
    await decide('approve', 'ra-' + '0'.repeat(24), REVIEW_DIGEST); // the person was looking at a different review
    expect(sent).toEqual([]);
    expect(states).toEqual([expect.objectContaining({ state: 'refused', code: 'STALE_REVIEW' })]);
  });

  it('an approval whose displayed digest is not the pending one sends nothing and says the change changed (informed consent: only what was shown can be approved)', async () => {
    const sent: unknown[] = [];
    const states: Array<{ state: string; code?: string }> = [];
    const pending = review('PENDING') as unknown as NonNullable<RunView['review']>;
    const decide = createDecider({ port: { decideApproval: async (r: unknown) => { sent.push(r); return { accepted: true, code: 'ACCEPTED', message: 'ok', launch_id: null, review_id: null, decision: null, decided_at: null }; } } as never, run: () => ({ launchId: LAUNCH_ID, workspace: '/w', review: pending } as RunView), set: (s) => states.push(s as never) });
    await decide('approve', REVIEW_ID, 'sha256:' + '0'.repeat(64)); // right review id, but the person saw a different candidate
    await decide('approve', REVIEW_ID, ''); // no displayed digest at all (nothing was shown)
    expect(sent).toEqual([]);
    expect(states).toEqual([expect.objectContaining({ state: 'refused', code: 'STALE_REVIEW' }), expect.objectContaining({ state: 'refused', code: 'STALE_REVIEW' })]);
    await decide('reject', REVIEW_ID, ''); // a rejection never needs the diff
    expect(sent).toHaveLength(1);
  });
});

describe('keyboard use of the approval (hardening, Phase J)', () => {
  it('there is no second renderer confirmation: Escape and a stray Enter on the review send nothing; only the explicit Approve & Continue click does', async () => {
    const { host, bar } = await reachReview();
    await screen.findByTestId('proposed-change');
    const review = within(bar).getByRole('region', { name: 'Change review' });
    fireEvent.keyDown(review, { key: 'Escape' });
    fireEvent.keyDown(review, { key: 'Enter' });
    expect(within(bar).queryByRole('alertdialog')).toBeNull();
    expect(host.exec!.decisions).toEqual([]);
  });

  it('every control of the review is a native button with an accessible name, in document order, with no positive tabindex and no keyboard trap', async () => {
    const { bar } = await reachReview();
    await screen.findByTestId('proposed-change');
    const review = within(bar).getByRole('region', { name: 'Change review' });
    const names = within(review).getAllByRole('button').map((b) => b.textContent?.trim());
    expect(names).toEqual(['Open in Editor', 'Reject', 'Approve & Continue']);
    for (const el of Array.from(review.querySelectorAll('[tabindex]'))) expect(Number(el.getAttribute('tabindex'))).toBeLessThanOrEqual(0);
    for (const b of within(review).getAllByRole('button')) expect(b).toHaveAttribute('type', 'button');
  });
});

