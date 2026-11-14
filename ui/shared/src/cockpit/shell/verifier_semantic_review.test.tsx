/** The VERIFIER SEMANTIC REVIEW in the renderer (VERIFIER-SEMANTIC-REVIEW-001, owner ruling 2026-11-14): a FOURTH subject with its own three decisions. Pinned: the card shows three
 * buttons whose labels state the polarity in words and never Approve / Reject; the decider sends only the five identities and one of the three words; a word that carries authority
 * needs the fingerprint that was SHOWN and a review Kriya verified; a native Cancel is "no decision", not `cannot_decide`; the review is never one of the other three subjects. */
import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { LaunchStatus } from '../../model/kup';

const api = vi.hoisted(() => ({ current: {} as Record<string, unknown> }));
vi.mock('../core/execution', async (original) => ({ ...(await original<typeof import('../core/execution')>()), useExecution: () => api.current }));

import {
  NO_PROGRESS, SEMANTIC_BUTTONS, VERIFIER_SEMANTIC_WORDS, VerifierSemanticReviewCard, activeReview, activeRuntimeCommandReview, activeVerifierRepairReview, activeVerifierSemanticReview,
  activityFromLaunch, createVerifierSemanticDecider, deriveOutcome, notificationFor, runPhase, semanticReviewViewable, type PendingVerifierSemanticReview, type RunView,
  type SemanticDecisionState,
} from '../core';

const KIND = 'verifier_semantic_review';
const LAUNCH_ID = '20261010T101010-abcdef01';
const REVIEW_ID = 'ra-' + 'a1'.repeat(12);
const FP = 'sha256:' + 'b2'.repeat(32);
const SOURCE = 'class WidgetTest { void counts() { assertEquals(3, widget.count()); } }';
const CONTRACT = 'count() is the number of items the widget holds.';
const pending = (over: Record<string, unknown> = {}) => ({
  review_id: REVIEW_ID, sequence: 1, kind: KIND, status: 'PENDING', requested_at: '2026-10-10T10:11:00+00:00', expires_at: '2026-10-10T11:11:00+00:00', candidate_digest: FP,
  semantic_fingerprint: FP, verified: true, question: 'Is what this verifier expects correct under the canonical contract?', notice: 'A generated verifier is candidate evidence.',
  decisions: [...VERIFIER_SEMANTIC_WORDS], verifier_unit_id: 's5', review_phase: 'failing_verifier',
  bindings: { run_id: LAUNCH_ID, plan_hash: 'sha256:' + '1b'.repeat(32), goal_digest: 'c3'.repeat(32), verification_contract_digest: null, failure_identity: 'f1'.repeat(32) },
  verifier_files: [{ path: 'src/test/java/WidgetTest.java', sha256: 'a0'.repeat(32), source: SOURCE }], contract_text: CONTRACT, contract_digest: 'c3'.repeat(32),
  acceptance_criteria: [{ id: 'AC1', text: 'count() returns the number of items' }], invariants: [{ id: 'GI1', text: 'no item is counted twice' }],
  outcome: 'WidgetTest.counts expected: <3> but was: <2>', decision: null, ...over,
}) as unknown as PendingVerifierSemanticReview;
const statusWith = (review: unknown, phase = 'RUNNING', liveness = 'live') => ({ liveness, launch: { phase, pending_approval: review, approval_history: [], requested_at: null }, run_record: { found: false } }) as unknown as LaunchStatus;
const idle: SemanticDecisionState = { state: 'idle' };
function mount(review = pending(), over: Record<string, unknown> = {}) {
  const sent: unknown[][] = [];
  api.current = { canDecideVerifierSemantic: true, semanticDecision: idle, decideVerifierSemantic: async (...args: unknown[]) => { sent.push(args); }, ...over };
  render(<VerifierSemanticReviewCard review={review} launchId={LAUNCH_ID} />);
  return { sent, card: screen.getByRole('region', { name: 'Verifier semantic review' }) };
}

describe('the card: three decisions whose labels state the polarity', () => {
  it('shows Kriya\'s evidence and exactly three buttons - never Approve or Reject', () => {
    const { card } = mount();
    expect(card.getAttribute('data-subject')).toBe(KIND);
    expect(card).toHaveTextContent('ACTION REQUIRED');
    expect(within(card).getByTestId('semantic-contract').textContent).toBe(CONTRACT);
    expect(within(card).getByTestId('semantic-source').textContent).toBe(SOURCE);
    expect(within(card).getByTestId('semantic-criteria')).toHaveTextContent('AC1');
    expect(within(card).getByTestId('semantic-invariants')).toHaveTextContent('GI1');
    expect(within(card).getByTestId('semantic-outcome')).toHaveTextContent('expected: <3> but was: <2>');
    const buttons = within(card).getAllByRole('button');
    expect(buttons.map((b) => b.textContent)).toEqual(['Expectation is valid…', 'Expectation is invalid…', 'Cannot decide']);
    expect(buttons.map((b) => b.getAttribute('data-decision'))).toEqual([...VERIFIER_SEMANTIC_WORDS]);
    expect(buttons.map((b) => b.textContent).join(' ')).not.toMatch(/approve|reject|authorize/i);
    expect(SEMANTIC_BUTTONS.map((b) => b.word)).toEqual([...VERIFIER_SEMANTIC_WORDS]);
    // Kriya asserts nothing about who is wrong
    const tech = card.querySelector('details.ck-tech')!;
    expect((card.textContent ?? '').replace(tech.textContent ?? '', '')).not.toMatch(/the verifier is (wrong|invalid|defective)|the code is wrong/i);
  });
  it.each(VERIFIER_SEMANTIC_WORDS.map((word, index) => [word, index] as const))('the %s button sends exactly that word with the review and fingerprint that were shown', (word, index) => {
    const { sent, card } = mount();
    fireEvent.click(within(card).getAllByRole('button')[index]!);
    expect(sent).toEqual([[word, REVIEW_ID, FP]]);
  });
  it('evidence that is not verified offers only Cannot decide', () => {
    const { card } = mount(pending({ verified: false }));
    expect(within(card).getByTestId('unable-to-display-semantic')).toBeTruthy();
    expect(within(card).getAllByRole('button').map((b) => b.getAttribute('data-decision'))).toEqual(['cannot_decide']);
    expect(within(card).queryByTestId('semantic-source')).toBeNull();
  });
  it('a native cancel says nothing was recorded; a recorded word is named; a passing verifier is described as passing', () => {
    const cancelled = mount(pending(), { semanticDecision: { state: 'declined', reviewId: REVIEW_ID } });
    expect(within(cancelled.card).getByTestId('semantic-declined')).toHaveTextContent('Nothing was recorded');
    expect(within(cancelled.card).getAllByRole('button')).toHaveLength(3);        // still waiting: the three decisions are offered again
    document.body.innerHTML = '';
    const recorded = mount(pending({ review_phase: 'passing_verifier' }), { semanticDecision: { state: 'accepted', reviewId: REVIEW_ID, decision: 'cannot_decide' } });
    expect(recorded.card).toHaveTextContent('Recorded: cannot decide');
    expect(recorded.card).toHaveTextContent('A generated verifier passed.');
    expect(within(recorded.card).queryAllByRole('button')).toHaveLength(0);
  });
});

describe('the fourth subject is never one of the other three, and only a live PENDING one is actionable', () => {
  it('is returned by its own selector only', () => {
    const live = statusWith(pending());
    expect(activeVerifierSemanticReview(live)?.review_id).toBe(REVIEW_ID);
    expect(activeReview(live)).toBeNull();
    expect(activeRuntimeCommandReview(live)).toBeNull();
    expect(activeVerifierRepairReview(live)).toBeNull();
    for (const kind of ['change_approval', 'runtime_command', 'verifier_defect_repair_authorization']) expect(activeVerifierSemanticReview(statusWith({ ...pending(), kind }))).toBeNull();
    for (const [status, phase, liveness] of [['DECIDED', 'RUNNING', 'live'], ['EXPIRED', 'RUNNING', 'live'], ['PENDING', 'ENDED', 'ended'], ['PENDING', 'RUNNING', 'dead']] as const) {
      expect(activeVerifierSemanticReview(statusWith(pending({ status }), phase, liveness))).toBeNull();
    }
    expect(activeVerifierSemanticReview(null)).toBeNull();
  });
  it('is viewable only with Kriya\'s proof and the evidence present', () => {
    expect(semanticReviewViewable(pending())).toBe(true);
    for (const over of [{ verified: false }, { status: 'DECIDED' }, { verifier_files: [] }, { contract_text: '' }, { candidate_digest: 'sha256:' + '0'.repeat(64) }]) {
      expect(semanticReviewViewable(pending(over))).toBe(false);
    }
  });
  it('the run phase, the notification and the activity line say what is waited for and what was recorded - an expiry is not a decision', () => {
    const run = { launchId: LAUNCH_ID, workspace: '/w', stage: 'running', semanticReview: pending(), status: statusWith(pending()), progress: NO_PROGRESS } as unknown as RunView;
    expect(runPhase(run)).toMatchObject({ state: 'WAITING_FOR_APPROVAL', needsAction: true });
    expect(notificationFor(run)).toEqual({ kind: 'approval_required', key: `approval_required:${LAUNCH_ID}:${REVIEW_ID}` });
    const lines = (review: unknown) => activityFromLaunch(statusWith(review)).map((entry) => entry.text).join(' | ');
    expect(lines(pending({ status: 'DECIDED', decision: { decision: 'expectation_valid', decided_at: null, source: 'review-channel' } }))).toContain('the verifier expectation is valid');
    expect(lines(pending({ status: 'DECIDED', decision: { decision: 'expectation_invalid', decided_at: null, source: 'review-channel' } }))).toContain('the verifier expectation is invalid');
    expect(lines(pending({ status: 'DECIDED', decision: { decision: 'cannot_decide', decided_at: null, source: 'review-channel' } }))).toContain('cannot decide');
    const expired = lines(pending({ status: 'EXPIRED', decision: { decision: null, decided_at: null, source: 'timeout' } }));
    expect(expired).toContain('ended with no decision');
    expect(expired).not.toContain('cannot decide');
  });
  it('a decided or expired semantic review is never the run\'s outcome', () => {
    for (const status of ['DECIDED', 'EXPIRED']) {
      const ended = { liveness: 'ended', launch: { phase: 'ENDED', ended: { workflow_status: 'needs_review', trace_run_id: 't1' }, approval_required: null, pending_approval: pending({ status }) }, run_record: { found: true, terminal_status: 'FAILURE' } } as unknown as LaunchStatus;
      const outcome = deriveOutcome(ended, false, null);
      expect(outcome?.kind).not.toBe('rejected');
      expect(outcome?.kind).not.toBe('review_expired');
    }
  });
});

describe('the decider: identities and one word, nothing else', () => {
  const runWith = (semanticReview: unknown): RunView => ({ launchId: LAUNCH_ID, workspace: '/w', semanticReview } as unknown as RunView);
  function decider(answer: unknown = { accepted: true, code: 'ACCEPTED', message: 'ok' }, review: unknown = pending()) {
    const sent: unknown[] = [];
    const states: SemanticDecisionState[] = [];
    const port = { decideVerifierSemantic: async (r: unknown) => { sent.push(r); return answer; } } as never;
    return { sent, states, decide: createVerifierSemanticDecider({ port, run: () => runWith(review), set: (s) => states.push(s) }) };
  }
  it.each(VERIFIER_SEMANTIC_WORDS)('%s is sent as itself with only the five identities', async (word) => {
    const { sent, states, decide } = decider();
    await decide(word, REVIEW_ID, FP);
    expect(sent).toEqual([{ workspace: '/w', launch_id: LAUNCH_ID, review_id: REVIEW_ID, semantic_fingerprint: FP, decision: word }]);
    expect(states.at(-1)).toEqual({ state: 'accepted', reviewId: REVIEW_ID, decision: word });
  });
  it('a word that carries authority needs the fingerprint that was SHOWN and a verified review; cannot_decide does not', async () => {
    for (const word of ['expectation_valid', 'expectation_invalid'] as const) {
      const { sent, states, decide } = decider();
      await decide(word, REVIEW_ID, 'sha256:' + '0'.repeat(64));
      await decide(word, 'ra-' + 'ff'.repeat(12), FP);
      expect(sent).toEqual([]);
      expect(states.map((s) => (s.state === 'refused' ? s.code : s.state))).toEqual(['STALE_REVIEW', 'STALE_REVIEW']);
      const unverified = decider(undefined, pending({ verified: false }));
      await unverified.decide(word, REVIEW_ID, FP);
      expect(unverified.sent).toEqual([]);
    }
    const unverified = decider(undefined, pending({ verified: false }));
    await unverified.decide('cannot_decide', REVIEW_ID, '');
    expect(unverified.sent).toHaveLength(1);
  });
  it('a binary word, or any other word, is never sent', async () => {
    const { sent, states, decide } = decider();
    for (const word of ['approve', 'reject', 'valid', '']) await decide(word as never, REVIEW_ID, FP);
    expect(sent).toEqual([]);
    expect(states).toEqual([]);
  });
  it('a native cancel is its own state (no decision); a refusal carries Kriya\'s code; nothing happens without the port or the review', async () => {
    const cancelled = decider({ accepted: false, code: 'CONFIRMATION_DECLINED', message: 'no' });
    await cancelled.decide('expectation_invalid', REVIEW_ID, FP);
    expect(cancelled.states.at(-1)).toEqual({ state: 'declined', reviewId: REVIEW_ID });
    const refused = decider({ accepted: false, code: 'WRONG_DECISION_FOR_REVIEW_KIND', message: 'no' });
    await refused.decide('expectation_valid', REVIEW_ID, FP);
    expect(refused.states.at(-1)).toMatchObject({ state: 'refused', code: 'WRONG_DECISION_FOR_REVIEW_KIND', decision: 'expectation_valid' });
    const fail = () => { throw new Error('nothing may be set'); };
    await createVerifierSemanticDecider({ port: {} as never, run: () => runWith(pending()), set: fail })('cannot_decide', REVIEW_ID, FP);
    const none = decider(undefined, null);
    await none.decide('cannot_decide', REVIEW_ID, FP);
    expect(none.sent).toEqual([]);
  });
});
