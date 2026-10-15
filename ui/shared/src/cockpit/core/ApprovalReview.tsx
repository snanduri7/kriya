import { useEffect, useRef, useState } from 'react';
import { sanitizeText } from '../../render/sanitize';
import type { ChangeApprovalReview } from '../../model/kup';
import { Badge } from '../design';
import { FileDiff } from './ChangeDiff';
import { unavailable, type Availability } from './availability';
import { toAvailability, useAsync } from './async';
import { useProvider } from './context';
import { useExecution, type PendingReview } from './execution';
import { shortDigest } from './records';
import { verificationSummary } from './presentation';
import { useLiveRunFacts } from './runFactsHooks';
import { useWorkspacePath } from './workspace';

export const CHANGED_MESSAGE = 'The proposed change changed while you were reviewing it. Review the updated change before approving.';
const REFUSAL_HELP: Record<string, string> = {
  STALE_REVIEW: CHANGED_MESSAGE,
  ALREADY_DECIDED: 'This review already has a decision.',
  REVIEW_EXPIRED: 'This review expired; nothing was applied.',
  LAUNCH_ENDED: 'The run has already ended; there is nothing left to decide.',
  LAUNCH_NOT_ACTIVE: 'The run that was waiting for this decision is no longer running.',
  NOT_PENDING: 'No change is waiting for review.',
  WRONG_WORKSPACE: 'This run belongs to a different folder.',
};
const STATUS_MARK = { passed: '✓', failed: '✕', unknown: '•' } as const;

/** The EXACT proposed change of one review, proved by Kriya against the review's digests. Re-read whenever the review identity changes (a new candidate is a new review). */
export function useChangeReview(launchId: string, reviewId: string | null, nonce = 0): Availability<ChangeApprovalReview> {
  const provider = useProvider();
  const workspace = useWorkspacePath();
  return useAsync<ChangeApprovalReview>(workspace === null || reviewId === null ? null : `change-review:${workspace}:${launchId}:${reviewId}:${nonce}`,
    async (signal) => toAvailability(await provider.changeApprovalReview(workspace!, launchId, reviewId!, signal)),
    unavailable<ChangeApprovalReview>('unknown_run', 'No workspace is open.'));
}

/** Why the person cannot be shown the change, in words; the approve control does not exist in any of these states. */
function unableReason(state: Availability<ChangeApprovalReview>, truncated: boolean): string {
  if (truncated) return 'This change is larger than can be shown in full here.';
  if (state.state === 'error') return state.code === 'REVIEW_DIFF_UNVERIFIED' ? 'Kriya could not confirm that the stored change is the one under review.' : state.code === 'REVIEW_DIFF_UNAVAILABLE' ? 'Kriya did not keep this change in a form it can show.' : `${state.message}`;
  if (state.state === 'unavailable') return state.detail;
  return 'Kriya could not provide the change.';
}

/**
 * ACTION REQUIRED: the human review of a change Kriya paused a run for. It holds NO authority and shows nothing it invented. Order of importance: the EXACT change (a diff Kriya
 * re-proved against the review's digests), why, the recorded verification, then the decision. INFORMED CONSENT: there is no way to approve a change that is not on screen -
 * while the diff loads, if it cannot be proved or shown in full, or if the review changed underneath, Approve does not exist (Reject and Open in Editor do). The decision carries
 * the digest of the change that was SHOWN; Kriya refuses a candidate that is no longer the pending one, and the person is told to review the updated change.
 */
export function ApprovalReview({ review, launchId }: { review: PendingReview; launchId: string }) {
  const ex = useExecution();
  const provider = useProvider();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const [changed, setChanged] = useState(false);
  const seenReview = useRef(review.review_id);
  const shown = useChangeReview(launchId, review.review_id, nonce);
  // A new review identity means the candidate changed while this one was on screen: say so; the diff below is already the new one.
  useEffect(() => {
    if (seenReview.current !== review.review_id) { seenReview.current = review.review_id; setChanged(true); }
  }, [review.review_id]);
  const decision = ex.decision;
  const busy = decision.state === 'submitting' || decision.state === 'accepted';
  const data = shown.state === 'ready' || shown.state === 'degraded' ? shown.data : null;
  const truncated = data?.truncated === true;
  // The change counts as VIEWABLE only when Kriya proved it is THIS review's change (the answer is verified by contract): same review, same digest, still pending, and shown in full.
  const viewable = data !== null && data.review_id === review.review_id && data.candidate_digest === review.candidate_digest && data.status === 'PENDING' && !truncated;
  const staleRefusal = decision.state === 'refused' && decision.code === 'STALE_REVIEW';
  const facts = useLiveRunFacts(launchId, `${review.review_id}@${review.candidate_digest}`);
  const lines = facts && facts.state === 'ready' ? verificationSummary(facts.gates) : null;
  const added = (data?.files ?? review.files.map((f) => ({ additions: f.lines_added }))).reduce((n, f) => n + f.additions, 0);
  const removed = (data?.files ?? review.files.map((f) => ({ deletions: f.lines_removed }))).reduce((n, f) => n + ('deletions' in f ? f.deletions : 0), 0);
  const why = data?.review_summary ?? data?.policy_note ?? null;
  // Open in Editor: the first file that already exists in the project (a new file has nothing to open yet).
  const existing = data?.files.find((f) => f.change_type !== 'added') ?? null;
  const openEditor = () => { if (workspace && existing) void provider.openInEditor(`${workspace}/${existing.path}`); };
  const decide = (choice: 'approve' | 'reject') => { void ex.decide(choice, review.review_id, data?.candidate_digest ?? ''); }; // approve is only offered while `viewable` (the exact change is on screen); the decider also refuses a digest that is not the pending one
  return (
    <section className="ck-review ck-action" aria-label="Change review" data-review-id={review.review_id} tabIndex={-1}>
      <div className="ck-action-head"><span className="ck-action-flag">ACTION REQUIRED</span><strong>Kriya wants to change your project</strong></div>
      <div className="ck-subject muted small" data-testid="approval-subject">Review proposed code change</div>
      <div className="muted small">Kriya paused this run for your decision. Nothing is applied to your folder until you approve.</div>
      {changed || staleRefusal ? <div className="ck-state" role="alert" data-testid="change-changed"><strong>{CHANGED_MESSAGE}</strong></div> : null}

      <h4 className="ck-action-sub">What would change <span className="muted small">{review.files.length} file{review.files.length === 1 ? '' : 's'} · +{added} −{removed}</span></h4>
      {shown.state === 'loading' ? <p className="muted" role="status">Loading the proposed change…</p> : null}
      {viewable && data ? <div data-testid="proposed-change">{data.files.map((f) => <FileDiff key={f.path} file={f} />)}</div> : null}
      {!viewable && shown.state !== 'loading' ? (
        <div className="ck-state" role="alert" data-testid="unable-to-display">
          <strong>Unable to display the proposed change</strong>
          <div>{unableReason(shown, truncated)} Kriya cannot give you enough to make an informed decision, so approval is not offered.</div>
          <div className="muted small">You can open the project in your editor, or reject this change. <button type="button" className="ck-link" onClick={() => setNonce((n) => n + 1)}>Try again</button></div>
        </div>
      ) : null}

      <h4 className="ck-action-sub">Why</h4>
      {why !== null ? <div className="ck-why" data-testid="change-why">{sanitizeText(why)}</div> : <p className="muted">{shown.state === 'loading' ? 'Loading…' : 'Kriya recorded no explanation for this change.'}</p>}

      <h4 className="ck-action-sub">Verification</h4>
      {lines === null ? <p className="muted" role="status">{facts && facts.state === 'unavailable' ? `Verification results could not be read here (${facts.detail}).` : 'Reading the recorded results…'}</p>
        : lines.length === 0 ? <p className="muted">No verification result is recorded yet.</p>
        : <ul className="ck-verify" aria-label="Verification so far">{lines.map((l) => <li key={l.text} data-status={l.status}><span aria-hidden="true">{STATUS_MARK[l.status]}</span> {l.text}</li>)}</ul>}

      <details className="ck-tech">
        <summary>Technical details</summary>
        <div className="muted small mono">review {review.review_id} · launch {launchId} · candidate {shortDigest(review.candidate_digest)}{data ? ` · verified by Kriya against ${data.files.length} file digest${data.files.length === 1 ? '' : 's'}` : ''}</div>
      </details>

      <p className="muted small" data-testid="decision-help"><strong>Approve &amp; Continue</strong>: Kriya finishes its checks and, if they pass, applies this change to your folder. <strong>Reject</strong>: nothing is applied and this run ends.</p>
      <div className="ck-runbar-head ck-decision">
        <button type="button" onClick={openEditor} disabled={!existing} title={existing ? `Open ${existing.path} in your editor` : 'This change only adds new files; there is nothing to open yet'}>Open in Editor</button>
        <span className="grow" />
        {decision.state === 'submitting' ? <Badge tone="info">Recording your decision…</Badge> : null}
        {decision.state === 'accepted' ? <Badge tone="info">{decision.decision === 'approve' ? 'Approval sent' : 'Rejection sent'}</Badge> : null}
        {!busy ? (
          <>
            <button type="button" onClick={() => decide('reject')}>Reject</button>
            {viewable ? <button type="button" className="ck-primary ck-cta" onClick={() => decide('approve')}>Approve &amp; Continue</button> : null}
          </>
        ) : null}
      </div>
      {decision.state === 'accepted' ? <div className="muted small">Your decision was recorded. Kriya {decision.decision === 'approve' ? 'is running its final checks' : 'is ending the run'}.</div> : null}
      {decision.state === 'refused' && !staleRefusal ? <div role="alert"><strong>Kriya did not accept the decision</strong> ({decision.code}): {decision.message}{REFUSAL_HELP[decision.code] ? ` ${REFUSAL_HELP[decision.code]}` : ''}</div> : null}
    </section>
  );
}
