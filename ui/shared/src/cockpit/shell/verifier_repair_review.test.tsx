/** The VERIFIER-DEFECT REPAIR AUTHORIZATION review through the real shell over the scripted fixture host (owner rulings 2026-11-13): a THIRD subject, distinct from a change review and
 * from a runtime-command review. The renderer shows Kriya's recorded evidence and asks once; the commitment is the host's NATIVE confirmation (scripted here by `nativeAnswers`). Every word of
 * evidence comes from Kriya's record; the renderer sends identities, never a file, a text or a conclusion. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { activeReview, activeRuntimeCommandReview, activeVerifierRepairReview, activityFromLaunch, createVerifierRepairDecider, deriveOutcome, runPhase, verifierReviewViewable, type RunView, type RuntimeDecisionState } from '../core';
import type { LaunchStatus } from '../../model/kup';
import { renderWorkspaceShell } from '../testing/harness';
import {
  LAUNCH_ID, REVIEW_ID, VERIFIER_CONTRACT, VERIFIER_FINGERPRINT, VERIFIER_NOTICE, VERIFIER_PATH, VERIFIER_PRE_REPAIR_OUTCOME, VERIFIER_QUESTION, VERIFIER_REPLAY_OUTCOME, VERIFIER_SOURCE,
} from '../providers/fixture/fixtureExecution';

const KIND = 'verifier_defect_repair_authorization';
const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });
const GOAL = 'Create the protocol parser and its test.';
const CONTINUE = 'Continue to Authorization…';

async function reachVerifierReview(fixture: Record<string, unknown> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'verifier_review' }, ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
  await within(bar).findByRole('region', { name: 'Verifier repair authorization' });
  return { ...mounted, bar };
}
const card = (bar: HTMLElement) => within(bar).getByRole('region', { name: 'Verifier repair authorization' });

describe('the verifier-repair authorization review: deterministic evidence first, the question in Kriya\'s exact words', () => {
  it('shows ACTION REQUIRED, the question, the notice, the contract, the verifier source and both gate outcomes - and states no conclusion of its own', async () => {
    const { bar } = await reachVerifierReview();
    const review = card(bar);
    expect(review.getAttribute('data-subject')).toBe(KIND);
    expect(review).toHaveTextContent('ACTION REQUIRED');
    expect(within(review).getByTestId('verifier-question')).toHaveTextContent(VERIFIER_QUESTION);
    expect(within(review).getByTestId('verifier-notice')).toHaveTextContent(VERIFIER_NOTICE);
    expect(within(review).getByTestId('verifier-contract').textContent).toBe(VERIFIER_CONTRACT);
    expect(within(review).getByTestId('verifier-file')).toHaveTextContent(VERIFIER_PATH);
    expect(within(review).getByTestId('verifier-source').textContent).toBe(VERIFIER_SOURCE);
    expect(within(review).getByTestId('verifier-pre-repair-outcome').textContent).toBe(VERIFIER_PRE_REPAIR_OUTCOME);
    expect(within(review).getByTestId('verifier-replay-outcome').textContent).toBe(VERIFIER_REPLAY_OUTCOME);
    // it is neither of the other two subjects
    expect(within(bar).queryByRole('region', { name: 'Runtime command review' })).toBeNull();
    expect(within(bar).queryByRole('region', { name: 'Change review' })).toBeNull();
    // Kriya asserts nothing about who is wrong: no verdict words in the primary content
    const tech = review.querySelector('details.ck-tech')!;
    const primary = (review.textContent ?? '').replace(tech.textContent ?? '', '');
    expect(primary).not.toMatch(/the verifier is (wrong|invalid|defective)|TEST_DEFECT/i);
    expect(tech.hasAttribute('open')).toBe(false);
    expect(tech.textContent).toContain(REVIEW_ID);
    expect(tech.textContent).toContain('verified by Kriya');
  });

  it('names the two actions honestly: Continue leads to a native confirmation, Reject keeps the verifier', async () => {
    const { bar } = await reachVerifierReview();
    const review = card(bar);
    expect(within(review).getByRole('button', { name: CONTINUE })).toBeEnabled();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
    expect(within(review).getByTestId('verifier-decision-help')).toHaveTextContent('nothing is authorized until you confirm there');
    expect(within(review).getByTestId('verifier-decision-help')).toHaveTextContent('at most once per run');
  });

  it('APPROVE: one typed decision with the five identities, after the native confirmation', async () => {
    const { bar, host } = await reachVerifierReview();
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    await waitFor(() => expect(host.exec!.verifierDecisions).toHaveLength(1));
    expect(host.exec!.confirmationsShown).toBe(1);
    expect(host.exec!.verifierDecisions[0]).toEqual({ workspace: '/fixture/workspace', launch_id: LAUNCH_ID, review_id: REVIEW_ID, authorization_fingerprint: VERIFIER_FINGERPRINT, decision: 'approve' });
    expect(Object.keys(host.exec!.verifierDecisions[0]!).sort()).toEqual(['authorization_fingerprint', 'decision', 'launch_id', 'review_id', 'workspace']);
    expect(host.exec!.runtimeDecisions).toEqual([]);
  });

  it('native CANCEL is no decision: nothing is recorded, the review is still pending, the person is told so and can still decide', async () => {
    const { bar, host } = await reachVerifierReview();
    host.exec!.nativeAnswers.push(false);
    fireEvent.click(within(card(bar)).getByRole('button', { name: CONTINUE }));
    const note = await within(bar).findByTestId('verifier-declined');
    expect(note).toHaveTextContent('Nothing was authorized.');
    expect(host.exec!.verifierDecisions).toEqual([]);
    expect(within(card(bar)).getByRole('button', { name: CONTINUE })).toBeEnabled();
    expect(within(card(bar)).getByRole('button', { name: 'Reject' })).toBeEnabled();
  });

  it('REJECT is the explicit no: one decision, no native confirmation', async () => {
    const { bar, host } = await reachVerifierReview();
    fireEvent.click(within(card(bar)).getByRole('button', { name: 'Reject' }));
    await waitFor(() => expect(host.exec!.verifierDecisions).toHaveLength(1));
    expect(host.exec!.verifierDecisions[0]).toMatchObject({ decision: 'reject', authorization_fingerprint: VERIFIER_FINGERPRINT });
    expect(host.exec!.confirmationsShown).toBe(0);
  });

  it('a review Kriya did not verify is shown as undisplayable: no evidence, no Continue - only Reject', async () => {
    const { bar } = await reachVerifierReview({ execution: { outcome: 'verifier_review', verifierReview: { fault: 'unverified' } } });
    const review = card(bar);
    expect(within(review).getByTestId('unable-to-display-verifier')).toHaveTextContent('Unable to display the evidence');
    expect(within(review).queryByTestId('verifier-source')).toBeNull();
    expect(within(review).queryByRole('button', { name: CONTINUE })).toBeNull();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeEnabled();
  });

  it('a host without the dedicated decision path can show the review but cannot decide it', async () => {
    const { bar } = await reachVerifierReview({ execution: { outcome: 'verifier_review', noVerifierDecisionPort: true } });
    const review = card(bar);
    expect(within(review).queryByRole('button', { name: CONTINUE })).toBeNull();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeDisabled();
    expect(within(review).getByTestId('verifier-cannot-decide')).toBeInTheDocument();
  });
});

describe('the pure rules behind it', () => {
  const pending = (over: Record<string, unknown> = {}) => ({
    review_id: REVIEW_ID, sequence: 1, kind: KIND, status: 'PENDING', candidate_digest: VERIFIER_FINGERPRINT, authorization_fingerprint: VERIFIER_FINGERPRINT, verified: true,
    question: VERIFIER_QUESTION, notice: VERIFIER_NOTICE, verifier_unit_id: 's5', owner_unit_id: 's3', verifier_files: [{ path: VERIFIER_PATH, sha256: 'a0'.repeat(32), source: VERIFIER_SOURCE }],
    contract_text: VERIFIER_CONTRACT, contract_digest: 'c0'.repeat(32), requested_at: '2026-10-10T10:11:00+00:00', decision: null, ...over,
  });
  const statusWith = (review: unknown, phase = 'RUNNING', liveness = 'live') => ({ liveness, launch: { phase, pending_approval: review, approval_history: [], requested_at: '2026-10-10T10:10:00+00:00', ended: null }, run_record: { found: false } }) as unknown as LaunchStatus;

  it('the three subjects are disjoint: a pending review of one kind is never returned as another', () => {
    const verifier = statusWith(pending());
    expect(activeVerifierRepairReview(verifier)?.review_id).toBe(REVIEW_ID);
    expect(activeReview(verifier)).toBeNull();
    expect(activeRuntimeCommandReview(verifier)).toBeNull();
    expect(activeVerifierRepairReview(statusWith({ ...pending(), kind: 'runtime_command' }))).toBeNull();
    expect(activeVerifierRepairReview(statusWith({ ...pending(), kind: 'change_approval' }))).toBeNull();
    for (const [status, phase, liveness] of [['APPROVED', 'RUNNING', 'live'], ['EXPIRED', 'RUNNING', 'live'], ['PENDING', 'ENDED', 'live'], ['PENDING', 'RUNNING', 'dead']] as const) {
      expect(activeVerifierRepairReview(statusWith(pending({ status }), phase, liveness))).toBeNull();
    }
    expect(activeVerifierRepairReview(null)).toBeNull();
  });

  it('viewable only when Kriya verified it and the evidence is all there', () => {
    type Review = Parameters<typeof verifierReviewViewable>[0];
    expect(verifierReviewViewable(pending() as unknown as Review)).toBe(true);
    for (const over of [{ verified: false }, { verified: undefined }, { status: 'APPROVED' }, { verifier_files: [] }, { contract_text: '' },
      { verifier_files: [{ path: VERIFIER_PATH, sha256: 'a0'.repeat(32), source: '' }] }, { authorization_fingerprint: 'sha256:' + '0'.repeat(64) }]) {
      expect(verifierReviewViewable(pending(over) as unknown as Review)).toBe(false);
    }
  });

  const runWith = (verifierReview: unknown): RunView => ({ launchId: LAUNCH_ID, workspace: '/w', verifierReview } as unknown as RunView);
  function decider(answer: unknown = { accepted: true, code: 'ACCEPTED', message: 'ok' }, review: unknown = pending()) {
    const sent: unknown[] = [];
    const states: RuntimeDecisionState[] = [];
    const port = { decideVerifierRepair: async (r: unknown) => { sent.push(r); return answer; } } as never;
    return { sent, states, decide: createVerifierRepairDecider({ port, run: () => runWith(review), set: (s) => states.push(s) }) };
  }
  it('an approve needs the fingerprint that was SHOWN and a verified review; a stale or mismatched one sends nothing, a reject never needs it', async () => {
    const { sent, states, decide } = decider();
    await decide('approve', REVIEW_ID, 'sha256:' + '0'.repeat(64));
    await decide('approve', REVIEW_ID, '');
    await decide('approve', 'ra-' + 'ff'.repeat(12), VERIFIER_FINGERPRINT);
    expect(sent).toEqual([]);
    expect(states.map((s) => (s.state === 'refused' ? s.code : s.state))).toEqual(['STALE_REVIEW', 'STALE_REVIEW', 'STALE_REVIEW']);
    const unverified = decider(undefined, pending({ verified: false }));
    await unverified.decide('approve', REVIEW_ID, VERIFIER_FINGERPRINT);
    expect(unverified.sent).toEqual([]);
    await decide('reject', REVIEW_ID, '');
    expect(sent).toHaveLength(1);
  });
  it('sends only the five identities; a native cancel is its own state; a refusal carries Kriya\'s code', async () => {
    const ok = decider();
    await ok.decide('approve', REVIEW_ID, VERIFIER_FINGERPRINT);
    expect(Object.keys(ok.sent[0] as object).sort()).toEqual(['authorization_fingerprint', 'decision', 'launch_id', 'review_id', 'workspace']);
    expect(ok.states.at(-1)).toMatchObject({ state: 'accepted', decision: 'approve' });
    const cancelled = decider({ accepted: false, code: 'CONFIRMATION_DECLINED', message: 'no' });
    await cancelled.decide('approve', REVIEW_ID, VERIFIER_FINGERPRINT);
    expect(cancelled.states.at(-1)).toMatchObject({ state: 'declined' });
    const stale = decider({ accepted: false, code: 'STALE_REVIEW', message: 'changed' });
    await stale.decide('approve', REVIEW_ID, VERIFIER_FINGERPRINT);
    expect(stale.states.at(-1)).toMatchObject({ state: 'refused', code: 'STALE_REVIEW' });
    const noPort = createVerifierRepairDecider({ port: {} as never, run: () => runWith(pending()), set: () => { throw new Error('nothing may be set without the port'); } });
    await noPort('approve', REVIEW_ID, VERIFIER_FINGERPRINT);
  });

  it('a decided verifier review is never the run\'s outcome: a rejection or an expiry does not read as "Changes rejected" or "Review expired"', () => {
    const ended = (kind: string, status: string) => ({ liveness: 'ended', launch: { phase: 'ENDED', ended: { workflow_status: 'needs_review', trace_run_id: 't1' }, approval_required: null, pending_approval: { kind, status, decision: { decision: 'reject', source: 'review-channel' } } }, run_record: { found: true, terminal_status: 'FAILURE' } }) as unknown as LaunchStatus;
    for (const status of ['REJECTED', 'EXPIRED']) {
      const outcome = deriveOutcome(ended(KIND, status), false, null);
      expect(outcome?.kind).not.toBe('rejected');
      expect(outcome?.kind).not.toBe('review_expired');
    }
    expect(deriveOutcome(ended('change_approval', 'REJECTED'), false, null)).toMatchObject({ kind: 'rejected', label: 'Changes rejected' });   // unchanged
    expect(deriveOutcome(ended('runtime_command', 'REJECTED'), false, null)).toMatchObject({ kind: 'rejected', label: 'Commands not allowed' });
  });

  it('the run phase and the activity log name the third subject in its own words', () => {
    const base = { launchId: LAUNCH_ID, workspace: '/w', host: null, statusError: null, progress: { lastSeq: 3, kinds: {}, recent: [], error: null }, stage: 'running', outcome: null, review: null, runtimeReview: null, timing: {} };
    const waiting = runPhase({ ...base, status: statusWith(pending()), verifierReview: pending() } as unknown as RunView);
    expect(waiting).toMatchObject({ state: 'WAITING_FOR_APPROVAL', label: 'Waiting for your decision about a verifier', needsAction: true });
    const approved = runPhase({ ...base, status: statusWith(pending({ status: 'APPROVED' })), verifierReview: null } as unknown as RunView);
    expect(approved.label).toBe('Verifying');
    expect(approved.detail).toMatch(/authorized one repair/);
    expect(approved.detail).not.toMatch(/Your change was approved/);
    const rejected = runPhase({ ...base, status: statusWith(pending({ status: 'REJECTED' })), verifierReview: null } as unknown as RunView);
    expect(rejected.detail).toMatch(/No verifier repair was authorized/);
    const lines = (status: string) => activityFromLaunch(statusWith(pending({ status, decision: status === 'PENDING' ? null : { decided_at: '2026-10-10T10:11:30+00:00' } }))).map((e) => e.text);
    expect(lines('PENDING')).toContain('Waiting for your decision about a verifier');
    expect(lines('APPROVED')).toContain('One verifier repair authorized');
    expect(lines('REJECTED')).toContain('Verifier repair not authorized');
    expect(lines('EXPIRED')).toContain('The verifier decision expired; no repair was authorized');
    for (const status of ['PENDING', 'APPROVED', 'REJECTED', 'EXPIRED']) expect(lines(status).join(' ')).not.toMatch(/Code (approved|rejected)|Commands/);
  });
});
