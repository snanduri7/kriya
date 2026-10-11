import { useEffect, useRef, useState } from 'react';
import { sanitizeText } from '../../render/sanitize';
import { Badge, OPEN_TARGETS, RUN_DOCUMENTS_PAGE, shortDigest, useCapabilities, useDispatch, useExecution, useNavigate, useRunEvidence, type PendingReview } from '../core';

const REFUSAL_HELP: Record<string, string> = {
  STALE_REVIEW: 'The change under review is no longer the one you were shown. Review it again before deciding.',
  ALREADY_DECIDED: 'This review already has a decision.',
  REVIEW_EXPIRED: 'This review expired; nothing was applied.',
  LAUNCH_ENDED: 'The run has already ended; there is nothing left to decide.',
  LAUNCH_NOT_ACTIVE: 'The run that was waiting for this decision is no longer running.',
  NOT_PENDING: 'No change is waiting for review.',
  WRONG_WORKSPACE: 'This run belongs to a different folder.',
};

/**
 * The human review of a change Kriya paused a run for (GUI final). It holds NO authority and shows nothing it invented: the files, the sizes, the digest and the
 * reviewer's text are Kriya's own record of the pending review, verbatim. The diff and the validation results are NOT re-implemented here - the buttons open the
 * existing Changes and Validation modules on this run's live evidence. Approve and Reject send one typed decision carrying the identities Kriya gave the review;
 * Kriya checks them against its durable state, records the decision, and only then does the waiting run continue.
 */
export function ApprovalReview({ review, launchId }: { review: PendingReview; launchId: string }) {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const caps = useCapabilities();
  const [confirming, setConfirming] = useState(false);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  // The confirmation opens with focus on the SAFE choice (Cancel): a stray Enter never approves. Escape cancels it.
  useEffect(() => { if (confirming) cancelRef.current?.focus(); }, [confirming]);
  const decision = ex.decision;
  const evidence = useRunEvidence();
  const policyHidden = !caps.has('evidence_content'); // Kriya's content-visibility policy (an operator setting; this app cannot change it) hides the diff text
  // The diff counts as VIEWABLE only when policy allows it AND this review's own live evidence is loaded and kept content (not digests only). Anything else - still
  // loading, unreadable, digest-only capture - is "not viewable", so the explicit "Approve without viewing diff" confirmation applies (fail closed; review P2-3).
  const evidenceReady = evidence.state === 'ready' || evidence.state === 'degraded';
  const capture = evidenceReady ? evidence.data.manifest.manifest?.capture : undefined;
  const hidden = policyHidden || !evidenceReady || capture === undefined || capture === 'digest_only';
  const hiddenReason = policyHidden ? 'policy' : !evidenceReady ? 'not_loaded' : 'digest_only';
  const busy = decision.state === 'submitting' || decision.state === 'accepted';
  const open = (tab: (typeof OPEN_TARGETS)[keyof typeof OPEN_TARGETS]) => { dispatch({ type: 'selectLiveRun', launchId }); dispatch({ type: 'centralTab', tab }); go(RUN_DOCUMENTS_PAGE); };
  const added = review.files.reduce((n, f) => n + f.lines_added, 0);
  const removed = review.files.reduce((n, f) => n + f.lines_removed, 0);
  return (
    <section className="ck-review" aria-label="Change review" data-review-id={review.review_id}>
      <div><strong>Review the proposed change.</strong> Kriya paused this run for your decision; nothing is applied to your folder until you approve.</div>
      <ul aria-label="Files in this change" style={{ margin: '4px 0' }}>
        {review.files.map((f) => <li key={f.filepath}><span className="mono">{f.filepath}</span> <span className="muted small">+{f.lines_added} −{f.lines_removed} · diff {shortDigest(f.diff_sha256)}</span></li>)}
      </ul>
      <div className="muted small">{review.files.length} file{review.files.length === 1 ? '' : 's'}, +{added} −{removed} · candidate {shortDigest(review.candidate_digest)} · review {review.review_id}</div>
      <details open>
        <summary>Reviewer text (as Kriya recorded it{review.reason_truncated ? '; shortened by Kriya' : ''})</summary>
        <pre className="mono small" style={{ whiteSpace: 'pre-wrap', maxHeight: 220, overflow: 'auto', margin: '4px 0' }}>{sanitizeText(review.reason)}</pre>
      </details>
      {!policyHidden && hiddenReason !== 'digest_only' ? (
        <div className="muted small" role="note">Open Changes to read the exact authorized diff and Validation to see the gate results - both read this run&apos;s live evidence.</div>
      ) : hiddenReason === 'policy' ? (
        <div role="note">
          <strong>Diff hidden by Kriya content-visibility policy.</strong>{' '}
          <span className="muted small">The file list, line counts and digests above are Kriya&apos;s own metadata; Validation still shows the recorded gate results. Showing stored content is an operator setting of Kriya (evidence.attempt_recorder.content_visibility); this app has no control for it.</span>
        </div>
      ) : (
        <div role="note">
          <strong>The diff cannot be shown for this review.</strong>{' '}
          <span className="muted small">This run recorded digests of the change, not its content. The file list, line counts and digests above are Kriya&apos;s own metadata.</span>
        </div>
      )}
      <div className="ck-runbar-head">
        <button type="button" onClick={() => open(OPEN_TARGETS.changes)}>Review changes</button>
        <button type="button" onClick={() => open(OPEN_TARGETS.validation)}>Review validation</button>
        <span className="grow" />
        {decision.state === 'submitting' ? <Badge tone="info">Recording your decision…</Badge> : null}
        {decision.state === 'accepted' ? <Badge tone="info">{decision.decision === 'approve' ? 'Approval sent' : 'Rejection sent'}</Badge> : null}
        {!busy && !confirming ? (
          <>
            <button type="button" onClick={() => setConfirming(true)}>Approve changes</button>
            <button type="button" onClick={() => { void ex.decide('reject', review.review_id); }}>Reject changes</button>
          </>
        ) : null}
      </div>
      {confirming && !busy ? (
        <div role="alertdialog" aria-label="Confirm approval" className="ck-state" onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); setConfirming(false); } }}>
          <div><strong>Approve these {review.files.length} file change{review.files.length === 1 ? '' : 's'}?</strong> Kriya will continue its verification and, if it passes, apply the change to your folder.</div>
          {hidden ? <div role="note"><strong>{hiddenReason === 'policy' ? 'Diff hidden by Kriya content-visibility policy.' : hiddenReason === 'digest_only' ? 'The diff cannot be shown for this review.' : 'The diff has not been loaded for this review.'}</strong> You have not seen the diff text: you would be approving from the file list, the line counts, the digests and the reviewer&apos;s text above.</div> : null}
          <div className="ck-runbar-head">
            <button type="button" onClick={() => { setConfirming(false); void ex.decide('approve', review.review_id); }}>{hidden ? 'Approve without viewing diff' : 'Confirm approval'}</button>
            <button type="button" ref={cancelRef} onClick={() => setConfirming(false)}>Cancel</button>
          </div>
        </div>
      ) : null}
      {decision.state === 'accepted' ? <div className="muted small">Kriya accepted your decision and is recording it; the run continues only once its own record says so, and the outcome comes from Kriya&apos;s records.</div> : null}
      {decision.state === 'refused' ? <div role="alert"><strong>Kriya did not accept the decision</strong> ({decision.code}): {decision.message}{REFUSAL_HELP[decision.code] ? ` ${REFUSAL_HELP[decision.code]}` : ''}</div> : null}
    </section>
  );
}
