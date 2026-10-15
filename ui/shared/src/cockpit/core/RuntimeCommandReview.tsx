import { useEffect, useRef, useState } from 'react';
import { sanitizeText } from '../../render/sanitize';
import type { RuntimeCommandReview } from '../../model/kup';
import { Badge } from '../design';
import { unavailable, type Availability } from './availability';
import { toAvailability, useAsync } from './async';
import { useProvider } from './context';
import { useExecution, type PendingRuntimeCommandReview } from './execution';
import { shortDigest } from './records';
import { useWorkspacePath } from './workspace';

const REFUSAL_HELP: Record<string, string> = {
  STALE_REVIEW: 'The commands under review are no longer the ones you were shown. Review the updated request.',
  WRONG_REVIEW_KIND: 'That request is not a request to run commands.',
  ALREADY_DECIDED: 'This request already has a decision.',
  REVIEW_EXPIRED: 'This request expired; nothing was run.',
  LAUNCH_ENDED: 'The run has already ended; there is nothing left to decide.',
  LAUNCH_NOT_ACTIVE: 'The run that was waiting for this decision is no longer running.',
  NOT_PENDING: 'No request to run commands is waiting.',
  WRONG_WORKSPACE: 'This run belongs to a different folder.',
  COMMAND_NOT_REVIEWABLE: 'Kriya could not confirm the exact commands that are waiting, so nothing was sent.',
};

/** The exact recorded command set of one runtime-command review, re-proved by Kriya against its fingerprint. Re-read whenever the review identity changes. */
export function useRuntimeCommandReview(launchId: string, reviewId: string | null, nonce = 0): Availability<RuntimeCommandReview> {
  const provider = useProvider();
  const workspace = useWorkspacePath();
  return useAsync<RuntimeCommandReview>(workspace === null || reviewId === null ? null : `runtime-review:${workspace}:${launchId}:${reviewId}:${nonce}`,
    async (signal) => toAvailability(await provider.runtimeCommandReview(workspace!, launchId, reviewId!, signal)),
    unavailable<RuntimeCommandReview>('unknown_run', 'No workspace is open.'));
}

/** One argument as a person reads it in a terminal: quoted only when it holds whitespace or a quote/shell character. Presentation only - the exact argv is in Technical details. */
export function displayArg(arg: string): string {
  const text = sanitizeText(arg);
  return /[\s"'\\$`]/.test(text) ? `"${text.replace(/(["\\$`])/g, '\\$1')}"` : text;
}
export const displayCommand = (command: readonly string[]): string => command.map(displayArg).join(' ');

/** Why the person cannot be shown the commands, in words; the Continue control does not exist in any of these states. */
function unableReason(state: Availability<RuntimeCommandReview>): string {
  if (state.state === 'error') return state.code === 'REVIEW_COMMAND_UNVERIFIED' ? 'Kriya could not confirm that the recorded commands are the ones this request is bound to.' : `${state.message}`;
  if (state.state === 'unavailable') return state.detail;
  return 'Kriya could not provide the commands.';
}

/**
 * ACTION REQUIRED: the human review of the commands Kriya paused a run for, to verify the application. It holds NO authority and shows nothing it invented: the commands, the directory
 * they run in and the input channel are what Kriya recorded and re-proved against the request's fingerprint. This is a DIFFERENT subject from a change review - nothing is being applied to the
 * project here; the person is deciding whether Kriya may RUN these exact commands in its isolated worktree.
 *
 * Two steps, on purpose: this review is the explanation; the commitment is a NATIVE confirmation shown by the application's main process after [Continue to Run Approval…]. INFORMED CONSENT: there is
 * no way to continue when the commands are not on screen - while they load, if Kriya cannot prove them, or if the request changed underneath, Continue does not exist (Reject does). The decision
 * carries the fingerprint of the command set that was SHOWN; the host re-proves it and Kriya refuses a request that is no longer the pending one.
 */
export function RuntimeCommandReviewCard({ review, launchId }: { review: PendingRuntimeCommandReview; launchId: string }) {
  const ex = useExecution();
  const [nonce, setNonce] = useState(0);
  const [changed, setChanged] = useState(false);
  const shown = useRuntimeCommandReview(launchId, review.review_id, nonce);
  // A new review identity means the request changed while this one was on screen: say so; the commands below are already the new ones.
  const seenReview = useRef(review.review_id);
  useEffect(() => { if (seenReview.current !== review.review_id) { seenReview.current = review.review_id; setChanged(true); } }, [review.review_id]);
  const data = shown.state === 'ready' || shown.state === 'degraded' ? shown.data : null;
  const viewable = data !== null && data.review_id === review.review_id && data.command_fingerprint === review.command_fingerprint && data.status === 'PENDING' && data.commands.length > 0;
  const decision = ex.runtimeDecision;
  const busy = decision.state === 'submitting' || decision.state === 'accepted';
  const staleRefusal = decision.state === 'refused' && decision.code === 'STALE_REVIEW';
  const criterion = data?.criterion ?? review.criterion;
  const inputText = (data?.input_channel ?? review.input_channel) === 'none' && !(data?.stdin_present ?? review.stdin_present) ? 'No interactive input' : 'Input is provided by Kriya';
  const decide = (choice: 'approve' | 'reject') => { void ex.decideRuntimeCommand(choice, review.review_id, data?.command_fingerprint ?? ''); };
  return (
    <section className="ck-review ck-action" aria-label="Runtime command review" data-review-id={review.review_id} data-subject="runtime_command" tabIndex={-1}>
      <div className="ck-action-head"><span className="ck-action-flag">ACTION REQUIRED</span><strong>Kriya wants to run commands to verify your application</strong></div>
      <div className="ck-subject muted small" data-testid="approval-subject">Allow Kriya to run verification commands</div>
      <div className="muted small">Kriya paused this run for your decision. Nothing further is run until you decide.</div>
      {changed || staleRefusal ? <div className="ck-state" role="alert" data-testid="runtime-changed"><strong>The commands changed while you were reviewing them. Review the updated request before continuing.</strong></div> : null}

      <h4 className="ck-action-sub">Why</h4>
      <div className="ck-why" data-testid="runtime-why">{sanitizeText(criterion) || 'Kriya recorded no explanation for this request.'}</div>

      <h4 className="ck-action-sub">Runs in</h4>
      <div data-testid="runtime-runs-in">Kriya isolated worktree</div>
      <p className="muted small">Kriya runs verification in an isolated worktree. Your project folder is updated only through the normal approved apply path. Before the commands run, Kriya may prepare the project&apos;s build or Python environment in that worktree. The worktree is a separate working copy of your project, not a security sandbox.</p>

      <h4 className="ck-action-sub">Commands</h4>
      {shown.state === 'loading' ? <p className="muted" role="status">Loading the commands…</p> : null}
      {viewable && data ? (
        <ol className="ck-commands mono" data-testid="runtime-commands" aria-label="Commands Kriya will run, in order">
          {data.commands.map((command, i) => <li key={`${i}:${command.join('\u0000')}`}>{displayCommand(command)}</li>)}
        </ol>
      ) : null}
      {!viewable && shown.state !== 'loading' ? (
        <div className="ck-state" role="alert" data-testid="unable-to-display-commands">
          <strong>Unable to display the commands</strong>
          <div>{unableReason(shown)} Kriya cannot give you enough to make an informed decision, so continuing is not offered.</div>
          <div className="muted small">You can reject this request. <button type="button" className="ck-link" onClick={() => setNonce((n) => n + 1)}>Try again</button></div>
        </div>
      ) : null}

      <h4 className="ck-action-sub">Input</h4>
      <div data-testid="runtime-input">{inputText}</div>

      <details className="ck-tech">
        <summary>Technical details</summary>
        <div className="muted small mono" data-testid="runtime-technical">
          <div>review {review.review_id} · launch {launchId}{review.unit_id ? ` · unit ${review.unit_id}` : ''}</div>
          <div>command fingerprint {shortDigest(review.command_fingerprint)}{data ? ' · verified by Kriya against the recorded commands, directory and input' : ''}</div>
          <div>worktree {sanitizeText(data?.working_directory ?? review.working_directory)}</div>
          <div>input channel {sanitizeText(data?.input_channel ?? review.input_channel)}{(data?.stdin_present ?? review.stdin_present) ? ' · stdin supplied by Kriya (content not shown)' : ''}</div>
          <div>criterion {sanitizeText(criterion)}</div>
          {data ? <div>argv {JSON.stringify(data.commands.map((c) => c.map((a) => sanitizeText(a))))}</div> : null}
        </div>
      </details>

      <p className="muted small" data-testid="runtime-decision-help"><strong>Continue to Run Approval…</strong> opens a confirmation from the application; nothing runs until you confirm there. <strong>Reject</strong>: Kriya will not run these commands; if this check is required for your goal, the run stops.</p>
      <div className="ck-runbar-head ck-decision">
        <span className="grow" />
        {decision.state === 'submitting' ? <Badge tone="info">{decision.decision === 'approve' ? 'Waiting for your confirmation…' : 'Recording your decision…'}</Badge> : null}
        {decision.state === 'accepted' ? <Badge tone="info">{decision.decision === 'approve' ? 'Permission sent' : 'Rejection sent'}</Badge> : null}
        {!busy ? (
          <>
            <button type="button" onClick={() => decide('reject')} disabled={!ex.canDecideRuntimeCommand}>Reject</button>
            {viewable && ex.canDecideRuntimeCommand ? <button type="button" className="ck-primary ck-cta" onClick={() => { setChanged(false); decide('approve'); }}>Continue to Run Approval…</button> : null}
          </>
        ) : null}
      </div>
      {!ex.canDecideRuntimeCommand ? <div role="note" className="muted small" data-testid="runtime-cannot-decide">This window cannot record that decision. Use the application that started the run.</div> : null}
      {decision.state === 'declined' ? <div role="status" data-testid="runtime-declined"><strong>Nothing was run.</strong> You cancelled the confirmation, so no decision was made. The request is still waiting for you.</div> : null}
      {decision.state === 'accepted' ? <div className="muted small">Your decision was recorded. Kriya {decision.decision === 'approve' ? 'is running the commands you reviewed' : 'is ending the run'}.</div> : null}
      {decision.state === 'refused' && !staleRefusal ? <div role="alert"><strong>Kriya did not accept the decision</strong> ({decision.code}): {decision.message}{REFUSAL_HELP[decision.code] ? ` ${REFUSAL_HELP[decision.code]}` : ''}</div> : null}
    </section>
  );
}
