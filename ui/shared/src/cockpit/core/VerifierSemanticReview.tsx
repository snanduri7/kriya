import { useEffect, useRef, useState } from 'react';
import { sanitizeText } from '../../render/sanitize';
import { Badge } from '../design';
import { useExecution, type PendingVerifierSemanticReview, type VerifierSemanticWord } from './execution';
import { shortDigest } from './records';

const REFUSAL_HELP: Record<string, string> = {
  STALE_REVIEW: 'The verifier or the run state under review is no longer the one you were shown. Review the updated request.',
  WRONG_REVIEW_KIND: 'That request is not a verifier semantic review.',
  WRONG_DECISION_FOR_REVIEW_KIND: 'That decision does not exist for a verifier semantic review. Nothing was recorded.',
  REVIEW_EXPIRED: 'This request expired; no decision was recorded.',
  LAUNCH_ENDED: 'The run has already ended; there is nothing left to decide.',
  LAUNCH_NOT_ACTIVE: 'The run that was waiting for this decision is no longer running.',
  SEMANTIC_REVIEW_NOT_REVIEWABLE: 'Kriya could not confirm the exact verifier and contract that are waiting, so nothing was sent.',
};

/** The three buttons, in the order shown. The label states the polarity in words; there is no generic Approve / Reject on this card. */
export const SEMANTIC_BUTTONS: ReadonlyArray<{ word: VerifierSemanticWord; label: string; sent: string; after: string }> = [
  { word: 'expectation_valid', label: 'Expectation is valid…', sent: 'Recorded: expectation is valid', after: 'continues with this expectation. Nothing is changed by this decision, and it does not say which code is wrong' },
  { word: 'expectation_invalid', label: 'Expectation is invalid…', sent: 'Recorded: expectation is invalid', after: 'regenerates this verifier once; the new verifier is reviewed and gated as source' },
  { word: 'cannot_decide', label: 'Cannot decide', sent: 'Recorded: cannot decide', after: 'changes nothing and sends the run to review' },
];

/** True only when the pending review can be put in front of a person: Kriya's own read-time proof holds and everything the decision rests on is present. */
export function semanticReviewViewable(review: PendingVerifierSemanticReview): boolean {
  return review.verified === true && review.status === 'PENDING' && review.verifier_files.length > 0
    && review.verifier_files.every((file) => file.source.length > 0) && review.contract_text.length > 0
    && review.candidate_digest === review.semantic_fingerprint;
}

/**
 * ACTION REQUIRED: the person's SEMANTIC decision whether the exact INITIAL generated verifier Kriya shows expects the right thing under the canonical contract
 * (VERIFIER-SEMANTIC-REVIEW-001, owner ruling 2026-11-14). A FOURTH subject, beside the replay-specific verifier-repair authorization: it has its own three decisions and never shows
 * Approve / Reject (on the repair card "approve" means the verifier is WRONG - the opposite polarity).
 *
 * It holds NO authority and shows nothing it invented: the verifier source and digests, the contract text, the unit's criteria and invariants and the gate output are what Kriya recorded,
 * and `verified` is Kriya's own proof that the recorded texts are the bytes the semantic fingerprint binds. Kriya states no conclusion about the verifier and neither does this card.
 * The two decisions that carry authority open a NATIVE confirmation in the application's main process and do not exist when the evidence is not on screen or not verified. `Cannot decide`
 * is always available: it is a real, recorded answer that changes nothing and sends the run to review. Cancelling the confirmation records nothing at all.
 */
export function VerifierSemanticReviewCard({ review, launchId }: { review: PendingVerifierSemanticReview; launchId: string }) {
  const ex = useExecution();
  const [changed, setChanged] = useState(false);
  const seenReview = useRef(review.review_id);
  useEffect(() => { if (seenReview.current !== review.review_id) { seenReview.current = review.review_id; setChanged(true); } }, [review.review_id]);
  const viewable = semanticReviewViewable(review);
  const decision = ex.semanticDecision;
  const busy = decision.state === 'submitting' || decision.state === 'accepted';
  const staleRefusal = decision.state === 'refused' && decision.code === 'STALE_REVIEW';
  const decide = (word: VerifierSemanticWord) => { setChanged(false); void ex.decideVerifierSemantic(word, review.review_id, review.semantic_fingerprint); };
  const failing = review.review_phase === 'failing_verifier';
  const chosen = decision.state === 'submitting' || decision.state === 'accepted' ? SEMANTIC_BUTTONS.find((b) => b.word === decision.decision) ?? null : null;
  return (
    <section className="ck-review ck-action" aria-label="Verifier semantic review" data-review-id={review.review_id} data-subject="verifier_semantic_review" tabIndex={-1}>
      <div className="ck-action-head"><span className="ck-action-flag">ACTION REQUIRED</span><strong>A generated verifier {failing ? 'failed' : 'passed'}. Does it expect the right thing?</strong></div>
      <div className="ck-subject muted small" data-testid="approval-subject">Decide whether what one verifier expects is correct</div>
      <div className="muted small">Kriya paused this run for your decision about the verifier of unit {sanitizeText(review.verifier_unit_id)}. Neither a pass nor a failure of a generated verifier is the truth about the contract.</div>
      {changed || staleRefusal ? <div className="ck-state" role="alert" data-testid="semantic-changed"><strong>The request changed while you were reviewing it. Review the updated evidence before deciding.</strong></div> : null}

      <h4 className="ck-action-sub">What you are deciding</h4>
      <div className="ck-why" data-testid="semantic-question"><strong>{sanitizeText(review.question)}</strong></div>
      <p className="muted small" data-testid="semantic-notice">{sanitizeText(review.notice)}</p>

      {!viewable ? (
        <div className="ck-state" role="alert" data-testid="unable-to-display-semantic">
          <strong>Unable to display the evidence</strong>
          <div>Kriya could not confirm that the recorded verifier and contract are the ones this request is bound to. It cannot give you enough to judge the expectation, so those two decisions are not offered.</div>
          <div className="muted small">You can record that you cannot decide.</div>
        </div>
      ) : null}

      {viewable ? (
        <>
          <h4 className="ck-action-sub">Canonical contract</h4>
          <pre className="ck-commands mono" data-testid="semantic-contract">{sanitizeText(review.contract_text)}</pre>

          {review.acceptance_criteria.length > 0 ? (
            <>
              <h4 className="ck-action-sub">Acceptance criteria of this verifier unit</h4>
              <ul data-testid="semantic-criteria">{review.acceptance_criteria.map((item) => <li key={item.id}><span className="mono small">{sanitizeText(item.id)}</span> {sanitizeText(item.text)}</li>)}</ul>
            </>
          ) : null}
          {review.invariants.length > 0 ? (
            <>
              <h4 className="ck-action-sub">Invariants that apply to it</h4>
              <ul data-testid="semantic-invariants">{review.invariants.map((item) => <li key={item.id}><span className="mono small">{sanitizeText(item.id)}</span> {sanitizeText(item.text)}</li>)}</ul>
            </>
          ) : null}

          <h4 className="ck-action-sub">The verifier under review</h4>
          {review.verifier_files.map((file) => (
            <div key={file.path} data-testid="semantic-file">
              <div className="mono small">{sanitizeText(file.path)} <span className="muted">· {shortDigest(file.sha256)}</span></div>
              <pre className="ck-commands mono" data-testid="semantic-source">{sanitizeText(file.source)}</pre>
            </div>
          ))}

          <h4 className="ck-action-sub">{failing ? 'The failure under review (the verifier’s own gate output)' : 'The verifier’s own gate output (it passed)'}</h4>
          <pre className="ck-commands mono" data-testid="semantic-outcome">{sanitizeText(review.outcome) || 'Kriya recorded no gate output for this.'}</pre>
        </>
      ) : null}

      <details className="ck-tech">
        <summary>Technical details</summary>
        <div className="muted small mono" data-testid="semantic-technical">
          <div>review {review.review_id} · launch {launchId} · verifier unit {sanitizeText(review.verifier_unit_id)} · phase {sanitizeText(review.review_phase)}</div>
          <div>semantic fingerprint {shortDigest(review.semantic_fingerprint)}{review.verified === true ? ' · verified by Kriya against the recorded verifier, contract and bindings' : ' · NOT verified'}</div>
          <div>contract digest {shortDigest(review.contract_digest)} · run {sanitizeText(review.bindings.run_id)} · plan {shortDigest(review.bindings.plan_hash)}</div>
        </div>
      </details>

      <p className="muted small" data-testid="semantic-decision-help"><strong>Expectation is valid…</strong> and <strong>Expectation is invalid…</strong> open a confirmation from the application; nothing is recorded until you confirm there. <strong>Cannot decide</strong> is recorded as your answer: Kriya changes nothing and the run goes to review.</p>
      <div className="ck-runbar-head ck-decision">
        <span className="grow" />
        {decision.state === 'submitting' ? <Badge tone="info">{decision.decision === 'cannot_decide' ? 'Recording your decision…' : 'Waiting for your confirmation…'}</Badge> : null}
        {decision.state === 'accepted' && chosen ? <Badge tone="info">{chosen.sent}</Badge> : null}
        {!busy ? SEMANTIC_BUTTONS.map((button) => {
          const needsEvidence = button.word !== 'cannot_decide';
          if (needsEvidence && !(viewable && ex.canDecideVerifierSemantic)) return null;
          return <button key={button.word} type="button" data-decision={button.word} className={needsEvidence ? 'ck-primary' : undefined} onClick={() => decide(button.word)} disabled={!ex.canDecideVerifierSemantic}>{button.label}</button>;
        }) : null}
      </div>
      {!ex.canDecideVerifierSemantic ? <div role="note" className="muted small" data-testid="semantic-cannot-record">This window cannot record that decision. Use the application that started the run.</div> : null}
      {decision.state === 'declined' ? <div role="status" data-testid="semantic-declined"><strong>Nothing was recorded.</strong> You cancelled the confirmation, so no decision was made. The request is still waiting for you.</div> : null}
      {decision.state === 'accepted' && chosen ? <div className="muted small">Your decision was recorded. Kriya {chosen.after}.</div> : null}
      {decision.state === 'refused' && !staleRefusal ? <div role="alert"><strong>Kriya did not accept the decision</strong> ({decision.code}): {decision.message}{REFUSAL_HELP[decision.code] ? ` ${REFUSAL_HELP[decision.code]}` : ''}</div> : null}
    </section>
  );
}
