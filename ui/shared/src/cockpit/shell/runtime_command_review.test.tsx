/** The review of RUNTIME COMMANDS through the real shell over the scripted fixture host (Demo V2): a different subject from a change review. The renderer explains and asks once;
 * the commitment is the host's NATIVE confirmation (scripted here by `nativeAnswers`). Every word about the commands comes from Kriya's record; the renderer sends identities, never a command. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { activeReview, activeRuntimeCommandReview, createRuntimeCommandDecider, deriveOutcome, type RunView, type RuntimeDecisionState } from '../core';
import type { LaunchStatus } from '../../model/kup';
import { renderWorkspaceShell } from '../testing/harness';
import cardSource from '../core/RuntimeCommandReview.tsx?raw';
import { LAUNCH_ID, REVIEW_ID, RUNTIME_COMMANDS, RUNTIME_CRITERION, RUNTIME_FINGERPRINT, RUNTIME_WORKTREE } from '../providers/fixture/fixtureExecution';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });
const GOAL = 'Create a small Java application that prints Hello from Kriya and run it.';

async function reachRuntimeReview(fixture: Record<string, unknown> = {}, opts: { holdReview?: boolean } = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'runtime_review' }, ...fixture } });
  if (opts.holdReview) {
    const real = mounted.host.query.bind(mounted.host);
    vi.spyOn(mounted.host, 'query').mockImplementation(async (request) => (request.operation === 'runtime_command.review' ? new Promise(() => undefined) : real(request)));
  }
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
  await within(bar).findByRole('region', { name: 'Runtime command review' });
  return { ...mounted, bar };
}
const card = (bar: HTMLElement) => within(bar).getByRole('region', { name: 'Runtime command review' });
const commands = () => screen.findByTestId('runtime-commands');
const CONTINUE = 'Continue to Run Approval…';

describe('the runtime-command review: the exact commands first, in words a person can decide on', () => {
  it('shows ACTION REQUIRED, the subject, why, where it runs, the exact commands in order, and the input - and no id, digest or absolute path as primary content', async () => {
    const { bar } = await reachRuntimeReview();
    const review = card(bar);
    expect(review).toHaveTextContent('ACTION REQUIRED');
    expect(review).toHaveTextContent('Kriya wants to run commands to verify your application');
    expect(within(review).getByTestId('approval-subject')).toHaveTextContent('Allow Kriya to run verification commands');
    expect(within(review).getByTestId('runtime-why')).toHaveTextContent(RUNTIME_CRITERION);
    expect(within(review).getByTestId('runtime-runs-in')).toHaveTextContent('Kriya isolated worktree');
    expect(review).toHaveTextContent('Kriya runs verification in an isolated worktree. Your project folder is updated only through the normal approved apply path.');
    const list = await commands();
    const shown = Array.from(list.querySelectorAll('li')).map((li) => li.textContent);
    expect(shown).toEqual(RUNTIME_COMMANDS.map((c) => c.join(' ')));
    expect(within(review).getByTestId('runtime-input')).toHaveTextContent('No interactive input');
    const tech = review.querySelector('details.ck-tech')!;
    expect(tech.hasAttribute('open')).toBe(false);
    expect(tech.textContent).toContain(REVIEW_ID);
    expect(tech.textContent).toContain(RUNTIME_WORKTREE);
    const primary = (review.textContent ?? '').replace(tech.textContent ?? '', '');
    expect(primary).not.toContain(REVIEW_ID);
    expect(primary).not.toContain(RUNTIME_WORKTREE);
    expect(primary).not.toMatch(/sha256|fingerprint/i);
  });

  it('names the two actions honestly: NOT "Approve & Continue" (a native confirmation follows), and Reject', async () => {
    const { bar } = await reachRuntimeReview();
    await commands();
    const review = card(bar);
    expect(within(review).getByRole('button', { name: CONTINUE })).toBeEnabled();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(within(review).queryByRole('button', { name: /Approve/ })).toBeNull();
    expect(within(review).getByTestId('runtime-decision-help')).toHaveTextContent(/opens a confirmation from the application; nothing runs until you confirm there/);
    expect(cardSource).not.toMatch(/Approve &amp; Continue|Approve & Continue/);
  });

  it('the three approval subjects are visibly different, and a runtime request is never rendered as a change review', async () => {
    const { bar } = await reachRuntimeReview();
    await commands();
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
    expect(within(bar).getByTestId('approval-subject')).toHaveTextContent('Allow Kriya to run verification commands');
  });
});

describe('the decision: ONE renderer click, then the native confirmation; nothing but identities travels', () => {
  it('Continue sends ONE typed decision (review id + command fingerprint of what was SHOWN, no command text), the host confirmation was shown once, and the run goes on from Kriya\'s records', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    await waitFor(() => expect(host.exec!.runtimeDecisions).toHaveLength(1));
    expect(host.exec!.runtimeDecisions[0]).toEqual({ workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: REVIEW_ID, command_fingerprint: RUNTIME_FINGERPRINT, decision: 'approve' });
    expect(Object.keys(host.exec!.runtimeDecisions[0]!).sort()).toEqual(['command_fingerprint', 'decision', 'launch_id', 'review_id', 'workspace']);
    expect(JSON.stringify(host.exec!.runtimeDecisions[0])).not.toMatch(/mvn|java|target\/classes|worktree/);
    expect(host.exec!.confirmationsShown).toBe(1);
    expect(host.exec!.decisions).toEqual([]); // the CHANGE-review channel was never used
    await within(bar).findByText(/Verifying your application/);
    expect(within(bar).queryByRole('region', { name: 'Runtime command review' })).toBeNull();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
  });

  it('native CANCEL is no decision: nothing is recorded, the review is still pending, the person is told so, and can still reject or continue', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    host.exec!.nativeAnswers.push(false);
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    const note = await within(bar).findByTestId('runtime-declined');
    expect(note).toHaveTextContent(/Nothing was run/);
    expect(note).toHaveTextContent(/still waiting for you/);
    expect(host.exec!.confirmationsShown).toBe(1);
    expect(host.exec!.runtimeDecisions).toEqual([]); // not an approval and not a rejection
    expect(host.exec!.reviewPending()).toBe(true);
    expect(within(card(bar)).getByRole('button', { name: CONTINUE })).toBeEnabled();
    expect(within(card(bar)).getByRole('button', { name: 'Reject' })).toBeEnabled();
    // a second try, confirmed this time, is a fresh explicit act
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    await waitFor(() => expect(host.exec!.runtimeDecisions).toHaveLength(1));
    expect(host.exec!.confirmationsShown).toBe(2);
  });

  it('Reject is the explicit refusal: one decision, no native confirmation, nothing is run, the run ends', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    fireEvent.click(within(card(bar)).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.runtimeDecisions).toHaveLength(1));
    expect(host.exec!.runtimeDecisions[0]!.decision).toBe('reject');
    expect(host.exec!.confirmationsShown).toBe(0);
    await within(bar).findByText(/nothing will be run/);   // the interim note names the RIGHT subject (not "nothing will be applied")
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'rejected'));
    expect(bar).toHaveTextContent('Commands not allowed');
    expect(bar).toHaveTextContent(/You did not allow Kriya to run the verification commands, so nothing was run/);
    expect(bar).not.toHaveTextContent(/Changes rejected|proposed changes/);
  });

  it('a double click sends exactly one decision; the buttons are gone while it is in flight', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    const go = within(card(bar)).getByRole('button', { name: CONTINUE });
    fireEvent.click(go);
    fireEvent.click(go);
    await waitFor(() => expect(host.exec!.runtimeDecisions.length).toBeGreaterThan(0));
    expect(host.exec!.runtimeDecisions).toHaveLength(1);
    expect(host.exec!.confirmationsShown).toBe(1);
  });
});

describe('informed consent: no way to continue without the commands on screen', () => {
  it('while the commands load, Continue does not exist (Reject does)', async () => {
    const { host, bar } = await reachRuntimeReview({}, { holdReview: true });
    const review = card(bar);
    expect(within(review).getByText('Loading the commands…')).toBeInTheDocument();
    expect(within(review).queryByRole('button', { name: CONTINUE })).toBeNull();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(host.exec!.runtimeDecisions).toEqual([]);
  });

  it('commands Kriya cannot prove against their fingerprint are not shown, Continue does not exist, Reject remains', async () => {
    const { bar } = await reachRuntimeReview({ execution: { outcome: 'runtime_review', runtimeReview: { fault: 'unverified' } } });
    const unable = await within(bar).findByTestId('unable-to-display-commands');
    expect(unable).toHaveTextContent('Unable to display the commands');
    expect(unable).toHaveTextContent(/continuing is not offered/);
    const review = card(bar);
    expect(review.querySelector('[data-testid="runtime-commands"]')).toBeNull();
    expect(within(review).queryByRole('button', { name: CONTINUE })).toBeNull();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
  });

  it('a request that is replaced while the person reads is announced, the NEW commands load, and nothing is sent on their behalf', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    act(() => host.exec!.replaceReview());
    const note = await within(bar).findByTestId('runtime-changed');
    expect(note).toHaveTextContent(/The commands changed while you were reviewing them/);
    expect(host.exec!.runtimeDecisions).toEqual([]);
  });

  it('a host with no runtime-decision path shows the request but cannot continue from here', async () => {
    const { bar } = await reachRuntimeReview({ execution: { outcome: 'runtime_review', noRuntimeDecisionPort: true } });
    await commands();
    const review = card(bar);
    expect(within(review).queryByRole('button', { name: CONTINUE })).toBeNull();
    expect(within(review).getByTestId('runtime-cannot-decide')).toBeInTheDocument();
  });
});

describe('Kriya\'s refusals reach the person; a change review and a runtime review are never confused', () => {
  it('a decision for a superseded request fails closed with Kriya\'s refusal and the run keeps waiting', async () => {
    const { host, bar } = await reachRuntimeReview();
    await commands();
    act(() => host.exec!.supersedeReview());
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    await within(bar).findByText(/STALE_REVIEW|no longer the ones you were shown|changed while you were reviewing/i);
    expect(host.exec!.reviewPending()).toBe(true);
  });

  it('the change-review channel refuses a runtime review (WRONG_REVIEW_KIND) and the runtime channel refuses a change review', async () => {
    const runtime = await reachRuntimeReview();
    const wrong = await runtime.host.exec!.port.decideApproval({ workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: REVIEW_ID, candidate_digest: RUNTIME_FINGERPRINT, decision: 'approve' });
    expect(wrong).toMatchObject({ accepted: false, code: 'WRONG_REVIEW_KIND' });
    expect(runtime.host.exec!.reviewPending()).toBe(true);
  });
});

describe('the pure rules', () => {
  const pending = (kind: 'change_approval' | 'runtime_command', status = 'PENDING') => ({ launch: { phase: 'RUNNING', pending_approval: { kind, status } }, liveness: 'live' }) as unknown as LaunchStatus;
  it('activeRuntimeCommandReview and activeReview never return each other\'s kind, nor a decided/expired/ended review', () => {
    expect(activeRuntimeCommandReview(pending('runtime_command'))).not.toBeNull();
    expect(activeReview(pending('runtime_command'))).toBeNull();
    expect(activeReview(pending('change_approval'))).not.toBeNull();
    expect(activeRuntimeCommandReview(pending('change_approval'))).toBeNull();
    for (const status of ['APPROVED', 'REJECTED', 'EXPIRED']) expect(activeRuntimeCommandReview(pending('runtime_command', status))).toBeNull();
    expect(activeRuntimeCommandReview({ launch: { phase: 'ENDED', pending_approval: { kind: 'runtime_command', status: 'PENDING' } }, liveness: 'ended' } as unknown as LaunchStatus)).toBeNull();
    expect(activeRuntimeCommandReview(null)).toBeNull();
  });

  it('an undecided runtime request that expired is its own outcome with its own words, and a change review keeps its own', () => {
    const ended = (kind: string, status: string) => ({ launch: { phase: 'ENDED', approval_required: null, run: { run_id: 'r' }, ended: { exit_code: 1, workflow_status: null, trace_run_id: null, error: null, failure_category: null }, declined_decisions: [], pending_approval: { kind, status, decision: null } }, run_record: { found: true, terminal_status: 'FAILURE', commit_result: null } }) as unknown as LaunchStatus;
    const runtime = deriveOutcome(ended('runtime_command', 'EXPIRED'), false, null);
    expect(runtime).toMatchObject({ kind: 'review_expired', label: 'Request expired' });
    expect(runtime!.detail).toMatch(/nothing was run/);
    expect(deriveOutcome(ended('change_approval', 'EXPIRED'), false, null)).toMatchObject({ kind: 'review_expired', label: 'Review expired' });
    expect(deriveOutcome(ended('runtime_command', 'REJECTED'), false, null)).toMatchObject({ kind: 'rejected', label: 'Commands not allowed' });
    expect(deriveOutcome(ended('change_approval', 'REJECTED'), false, null)).toMatchObject({ kind: 'rejected', label: 'Changes rejected' });
  });

  const reviewed = { review_id: REVIEW_ID, command_fingerprint: RUNTIME_FINGERPRINT };
  const runWith = (runtimeReview: unknown): RunView => ({ launchId: LAUNCH_ID, workspace: '/w', runtimeReview } as unknown as RunView);
  function decider(answer: unknown = { accepted: true, code: 'ACCEPTED', message: 'ok' }, review: unknown = reviewed) {
    const sent: unknown[] = [];
    const states: RuntimeDecisionState[] = [];
    const port = { decideRuntimeCommand: async (r: unknown) => { sent.push(r); return answer; } } as never;
    return { sent, states, decide: createRuntimeCommandDecider({ port, run: () => runWith(review), set: (s) => states.push(s) }) };
  }
  it('an approve needs the fingerprint of the command set that was SHOWN; a different or missing one sends nothing, a reject never needs it', async () => {
    const { sent, states, decide } = decider();
    await decide('approve', REVIEW_ID, 'sha256:' + '0'.repeat(64));
    await decide('approve', REVIEW_ID, '');
    await decide('approve', 'ra-' + 'ff'.repeat(12), RUNTIME_FINGERPRINT);
    expect(sent).toEqual([]);
    expect(states.map((s) => (s.state === 'refused' ? s.code : s.state))).toEqual(['STALE_REVIEW', 'STALE_REVIEW', 'STALE_REVIEW']);
    await decide('reject', REVIEW_ID, '');
    expect(sent).toHaveLength(1);
  });
  it('sends only the five identities - there is no field a renderer could put a command, directory, run or unit in', async () => {
    const { sent, decide } = decider();
    await decide('approve', REVIEW_ID, RUNTIME_FINGERPRINT);
    expect(Object.keys(sent[0] as object).sort()).toEqual(['command_fingerprint', 'decision', 'launch_id', 'review_id', 'workspace']);
  });
  it('maps the host\'s CONFIRMATION_DECLINED to its own state, a refusal to refused, and a thrown error to a host error', async () => {
    const declined = decider({ accepted: false, code: 'CONFIRMATION_DECLINED', message: 'x' });
    await declined.decide('approve', REVIEW_ID, RUNTIME_FINGERPRINT);
    expect(declined.states.at(-1)).toEqual({ state: 'declined', reviewId: REVIEW_ID });
    const refused = decider({ accepted: false, code: 'WRONG_REVIEW_KIND', message: 'm' });
    await refused.decide('approve', REVIEW_ID, RUNTIME_FINGERPRINT);
    expect(refused.states.at(-1)).toMatchObject({ state: 'refused', code: 'WRONG_REVIEW_KIND' });
    const seen: RuntimeDecisionState[] = [];
    const thrown = createRuntimeCommandDecider({ port: { decideRuntimeCommand: async () => { throw new Error('boom'); } } as never, run: () => runWith(reviewed), set: (s) => seen.push(s) });
    await thrown('approve', REVIEW_ID, RUNTIME_FINGERPRINT);
    expect(seen.at(-1)).toMatchObject({ state: 'refused', code: 'HOST_ERROR', message: 'boom' });
  });
  it('no review pending, or no runtime-decision path on the host: nothing is sent', async () => {
    const none = decider(undefined, null);
    await none.decide('reject', REVIEW_ID, '');
    expect(none.sent).toEqual([]);
    const sent: unknown[] = [];
    const noPort = createRuntimeCommandDecider({ port: {} as never, run: () => runWith(reviewed), set: (s) => sent.push(s) });
    await noPort('reject', REVIEW_ID, '');
    expect(sent).toEqual([]);
  });
});
