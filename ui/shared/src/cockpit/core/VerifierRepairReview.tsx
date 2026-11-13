import { useEffect, useRef, useState } from 'react';
import { sanitizeText } from '../../render/sanitize';
import { Badge } from '../design';
import { useExecution, type PendingVerifierRepairReview } from './execution';
import { shortDigest } from './records';

const REFUSAL_HELP: Record<string, string> = {
  STALE_REVIEW: 'The verifier or the run state under review is no longer the one you were shown. Review the updated request.',
  WRONG_REVIEW_KIND: 'That request is not a verifier repair authorization.',
  ALREADY_DECIDED: 'This request already has a decision.',
  REVIEW_EXPIRED: 'This request expired; nothing was authorized.',
  LAUNCH_ENDED: 'The run has already ended; there is nothing left to decide.',
  LAUNCH_NOT_ACTIVE: 'The run that was waiting for this decision is no longer running.',
  NOT_PENDING: 'No verifier repair authorization is waiting.',
  WRONG_WORKSPACE: 'This run belongs to a different folder.',
  AUTHORIZATION_NOT_REVIEWABLE: 'Kriya could not confirm the exact verifier and contract that are waiting, so nothing was sent.',
};

/** True only when the pending review can be put in front of a person: Kriya's own read-time proof holds and everything the decision rests on is present. */
export function verifierReviewViewable(review: PendingVerifierRepairReview): boolean {
  return review.verified === true && review.status === 'PENDING' && review.verifier_files.length > 0
    && review.verifier_files.every((file) => file.source.length > 0) && review.contract_text.length > 0
    && review.candidate_digest === review.authorization_fingerprint;
}

/**
 * ACTION REQUIRED: the person's SEMANTIC decision whether the exact verifier Kriya shows conflicts with the canonical contract (owner rulings 2026-11-13). It holds NO authority and shows
 * nothing it invented: the verifier source and digest, the contract text and digest and both gate outcomes are what Kriya recorded, and `verified` is Kriya's own proof that the recorded
 * texts are the bytes the authorization fingerprint binds. This is a THIRD subject: it is not a change review (nothing is applied here - the repaired verifier gets its own source review
 * later) and not a request to run commands.
 *
 * Kriya states no conclusion about the verifier and neither does this card: a failing verifier is normally evidence against the code it checks. Only the person can say the expectation
 * itself is wrong. Two steps, on purpose: this review is the evidence; the commitment is a NATIVE confirmation shown by the application's main process after [Continue to Authorization…].
 * INFORMED CONSENT: Continue does not exist when the evidence is not on screen or not verified (Reject does). The decision carries the fingerprint of the review that was SHOWN; the host
 * re-proves it and Kriya refuses a request that is no longer the pending one, and re-checks every binding again immediately before the repair.
 */
export function VerifierRepairReviewCard({ review, launchId }: { review: PendingVerifierRepairReview; launchId: string }) {
  const ex = useExecution();
  const [changed, setChanged] = useState(false);
  // A new review identity means the request changed while this one was on screen: say so; the evidence below is already the new one.
  const seenReview = useRef(review.review_id);
  useEffect(() => { if (seenReview.current !== review.review_id) { seenReview.current = review.review_id; setChanged(true); } }, [review.review_id]);
  const viewable = verifierReviewViewable(review);
  const decision = ex.verifierDecision;
  const busy = decision.state === 'submitting' || decision.state === 'accepted';
  const staleRefusal = decision.state === 'refused' && decision.code === 'STALE_REVIEW';
  const decide = (choice: 'approve' | 'reject') => { void ex.decideVerifierRepair(choice, review.review_id, review.authorization_fingerprint); };
  return (
    <section className="ck-review ck-action" aria-label="Verifier repair authorization" data-review-id={review.review_id} data-subject="verifier_defect_repair_authorization" tabIndex={-1}>
      <div className="ck-action-head"><span className="ck-action-flag">ACTION REQUIRED</span><strong>A verifier failed against the repaired code. Is the verifier itself wrong?</strong></div>
      <div className="ck-subject muted small" data-testid="approval-subject">Decide whether one verifier may be repaired</div>
      <div className="muted small">Kriya paused this run for your decision. Unit {sanitizeText(review.verifier_unit_id)} verifies the work of unit {sanitizeText(review.owner_unit_id)}.</div>
      {changed || staleRefusal ? <div className="ck-state" role="alert" data-testid="verifier-changed"><strong>The request changed while you were reviewing it. Review the updated evidence before continuing.</strong></div> : null}

      <h4 className="ck-action-sub">What you are deciding</h4>
      <div className="ck-why" data-testid="verifier-question"><strong>{sanitizeText(review.question)}</strong></div>
      <p className="muted small" data-testid="verifier-notice">{sanitizeText(review.notice)}</p>

      {!viewable ? (
        <div className="ck-state" role="alert" data-testid="unable-to-display-verifier">
          <strong>Unable to display the evidence</strong>
          <div>Kriya could not confirm that the recorded verifier and contract are the ones this request is bound to. It cannot give you enough to make an informed decision, so continuing is not offered.</div>
          <div className="muted small">You can reject this request.</div>
        </div>
      ) : null}

      {viewable ? (
        <>
          <h4 className="ck-action-sub">Canonical contract</h4>
          <pre className="ck-commands mono" data-testid="verifier-contract">{sanitizeText(review.contract_text)}</pre>

          <h4 className="ck-action-sub">The verifier under review</h4>
          {review.verifier_files.map((file) => (
            <div key={file.path} data-testid="verifier-file">
              <div className="mono small">{sanitizeText(file.path)} <span className="muted">· {shortDigest(file.sha256)}</span></div>
              <pre className="ck-commands mono" data-testid="verifier-source">{sanitizeText(file.source)}</pre>
            </div>
          ))}

          <h4 className="ck-action-sub">Result against the code BEFORE its repair</h4>
          <pre className="ck-commands mono" data-testid="verifier-pre-repair-outcome">{sanitizeText(review.pre_repair_outcome) || 'Kriya recorded no gate output for this.'}</pre>

          <h4 className="ck-action-sub">Result against the REPAIRED code (the failure under review{review.replay_failure_type ? `: ${sanitizeText(review.replay_failure_type)}` : ''})</h4>
          <pre className="ck-commands mono" data-testid="verifier-replay-outcome">{sanitizeText(review.replay_outcome) || 'Kriya recorded no gate output for this.'}</pre>
        </>
      ) : null}

      <details className="ck-tech">
        <summary>Technical details</summary>
        <div className="muted small mono" data-testid="verifier-technical">
          <div>review {review.review_id} · launch {launchId} · verifier unit {sanitizeText(review.verifier_unit_id)} · owner unit {sanitizeText(review.owner_unit_id)}</div>
          <div>authorization fingerprint {shortDigest(review.authorization_fingerprint)}{review.verified === true ? ' · verified by Kriya against the recorded verifier, contract and bindings' : ' · NOT verified'}</div>
          <div>contract digest {shortDigest(review.contract_digest)} · run {sanitizeText(review.bindings.run_id)} · plan {shortDigest(review.bindings.plan_hash)}</div>
          <div>recovery {sanitizeText(review.bindings.recovery_plan_id)} · generation {review.bindings.recovery_generation}</div>
          <div>code before repair {review.pre_repair_owner_files.map((f) => `${sanitizeText(f.path)} ${shortDigest(f.sha256)}`).join(', ')}</div>
          <div>code after repair {review.repaired_owner_files.map((f) => `${sanitizeText(f.path)} ${shortDigest(f.sha256)}`).join(', ')}</div>
        </div>
      </details>

      <p className="muted small" data-testid="verifier-decision-help"><strong>Continue to Authorization…</strong> opens a confirmation from the application; nothing is authorized until you confirm there. <strong>Reject</strong>: the verifier stays as it is and Kriya keeps treating its failure as evidence against the code. Kriya asks this at most once per run.</p>
      <div className="ck-runbar-head ck-decision">
        <span className="grow" />
        {decision.state === 'submitting' ? <Badge tone="info">{decision.decision === 'approve' ? 'Waiting for your confirmation…' : 'Recording your decision…'}</Badge> : null}
        {decision.state === 'accepted' ? <Badge tone="info">{decision.decision === 'approve' ? 'Authorization sent' : 'Rejection sent'}</Badge> : null}
        {!busy ? (
          <>
            <button type="button" onClick={() => decide('reject')} disabled={!ex.canDecideVerifierRepair}>Reject</button>
            {viewable && ex.canDecideVerifierRepair ? <button type="button" className="ck-primary ck-cta" onClick={() => { setChanged(false); decide('approve'); }}>Continue to Authorization…</button> : null}
          </>
        ) : null}
      </div>
      {!ex.canDecideVerifierRepair ? <div role="note" className="muted small" data-testid="verifier-cannot-decide">This window cannot record that decision. Use the application that started the run.</div> : null}
      {decision.state === 'declined' ? <div role="status" data-testid="verifier-declined"><strong>Nothing was authorized.</strong> You cancelled the confirmation, so no decision was made. The request is still waiting for you.</div> : null}
      {decision.state === 'accepted' ? <div className="muted small">Your decision was recorded. Kriya {decision.decision === 'approve' ? 'is repairing the verifier once; you will be asked to approve its source' : 'keeps the verifier and continues the run'}.</div> : null}
      {decision.state === 'refused' && !staleRefusal ? <div role="alert"><strong>Kriya did not accept the decision</strong> ({decision.code}): {decision.message}{REFUSAL_HELP[decision.code] ? ` ${REFUSAL_HELP[decision.code]}` : ''}</div> : null}
    </section>
  );
}
