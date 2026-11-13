import { OpenInEditorButton, RevealButton } from './WorkspaceFacts';
import { folderFact } from './Readiness';
import { ApplicationResult, conciseWhy, renderMarkdown, reviewBody, ApprovalReview, RuntimeCommandReviewCard, VerifierRepairReviewCard, Badge, CopyText, FileDiff, exhaustedSummary, failedDuring, failureSummary, retrySummary, runtimeStopWords, membershipStopOf, membershipStopWords, runRecipeOf, HowToRun, ObservedOutput, RequirementTruthBlock, requirementTruthOf, requirementTruthWords, runtimeObservationOf, useRunRecords, OPEN_TARGETS, RUN_DOCUMENTS_PAGE, RunStepper, activityText, changeSummary, localClock, progressCounts, runPhase, useRunActivity, useWorkspaceUi, useApplicationOutcome, useChangeReview, useDispatch, useElapsed, useExecution, useNavigate, useRunRowFacts, useWorkspacePath, VerificationLines, FormalRequirementDisclosure, TechnicalVerification, formalRequirementStatus, primaryVerification, verificationGroups, type ChangedFile, type RunPhase } from '../../core';

const TONE = { info: 'info', warn: 'warn', ok: 'ok', bad: 'bad' } as const;
const ACTIVITY_MARK = { info: '•', ok: '✓', bad: '✕', wait: '⚠' } as const;
/** How many of the newest activity lines the run surface previews (the dock keeps the whole history). */
const RECENT_ACTIVITY = 5;
const APPROVAL_KIND: Record<string, string> = {
  change_approval: 'a proposed change needs approval', knowledge_gap_acknowledgement: 'a knowledge gap needs acknowledgement', skill_gap_decision: 'a skill gap needs a decision',
  skill_conflict_decision: 'two skills may conflict', web_lookup_review: 'live-lookup references need review', web_lookup_query: 'a live-lookup search needs approval',
};
const detailLines = (d: Record<string, unknown>): string[] => Object.entries(d).flatMap(([k, v]) => (Array.isArray(v) && v.length ? [`${k}: ${v.map(String).join(', ')}`] : []));

/** The run that finished SUCCESSFULLY, as a result: what changed, why, whether it works, and (when Kriya recorded executing it) what the application printed. */
function Completed({ phase }: { phase: RunPhase }) {
  const ex = useExecution();
  const run = ex.run!;
  const outcome = run.outcome!;
  const snapshotReady = run.timing.refreshedAt !== null;
  // Independent reads of THIS finished run (after the refreshed snapshot is pinned); none changes which run is selected elsewhere.
  const facts = useRunRowFacts(outcome.traceRunId, snapshotReady);
  const application = useApplicationOutcome(outcome.traceRunId, snapshotReady);
  const lastApproval = run.status?.launch.pending_approval?.status === 'APPROVED' ? run.status.launch.pending_approval : null;
  const approvedReview = lastApproval?.kind === 'change_approval' ? lastApproval : null;    // (a runtime-command approval is not a change review: the change diff is never asked of it)
  const allowedCommands = lastApproval?.kind === 'runtime_command';
  const lastDecision = run.status?.launch.pending_approval ?? null;
  const declinedCommands = lastDecision?.kind === 'runtime_command' && lastDecision.status === 'REJECTED';   // the run still completed: the verification was optional for this goal, and it was NOT run
  const reviewed = useChangeReview(run.launchId, approvedReview?.review_id ?? null);
  const review = reviewed.state === 'ready' || reviewed.state === 'degraded' ? reviewed.data : null;
  const gates = facts && facts.state === 'ready' ? facts.gates : null;
  const files: ChangedFile[] | null = review
    ? review.files.map((f) => ({ path: f.path, additions: f.additions, deletions: f.deletions, changeType: f.change_type }))
    : facts && facts.state === 'ready' ? facts.changes.map((c) => ({ path: c.path, additions: c.linesAdded, deletions: c.linesRemoved, changeType: c.kind === 'ADDED' || c.kind === 'CREATED' ? 'added' as const : null })) : null;
  const why = conciseWhy(review?.review_summary ?? review?.policy_note ?? null);
  const fullReview = reviewBody(review?.reason ?? null);
  const unreadable = facts && facts.state === 'unavailable' ? `The recorded results could not be read here (${facts.detail}). Use View Details.` : null;
  const reading = (what: string) => (unreadable ?? (outcome.traceRunId === null ? `No trace row was recorded, so ${what} are not available here.` : `Reading the recorded ${what}…`));
  const groups = gates ? verificationGroups(gates) : null;
  const app = application && (application.state === 'ready' || application.state === 'degraded') ? application.data : null;
  const applied = outcome.commitResult === 'COMMITTED';
  const workspace = useWorkspacePath();
  const recipe = runRecipeOf(run.status?.launch.ended);       // the backend's verified run recipe (null: Kriya did not run the application)
  const truth = requirementTruthOf(run.status?.launch.ended);   // the backend's requirement truth (null: not recorded for this run - never "verified")
  const truthWords = requirementTruthWords(truth);
  const observation = runtimeObservationOf(run.status?.launch.ended);   // what the application run printed (null: Kriya did not run it)
  // A NEW application: every recorded change is a file Kriya added AND Kriya recorded executing it successfully (from the executor's recorded steps - never a claim).
  const created = applied && app?.status === 'EXECUTED' && app.succeeded === true && files !== null && files.length > 0 && files.every((f) => f.changeType === 'added');
  const projectName = workspace ? workspace.split('/').filter(Boolean).pop() ?? null : null;
  return (
    <div className="ck-outcome">
      <p className="ck-applied" data-testid="applied-statement">
        {truthWords.applied !== null && applied ? <strong data-testid="applied-with-open-requirements">⚠ {truthWords.applied}</strong> : created ? <strong>✓ Application created successfully</strong> : applied ? <strong>✓ Your change was applied successfully.</strong> : <span>Kriya did not record that the change was applied to your folder. Check your folder before relying on it.</span>}
        {approvedReview ? ' You approved the change.' : ''}{allowedCommands ? ' You allowed Kriya to run the verification commands.' : ''}{declinedCommands ? ' You did not allow the verification commands, so the application was not verified by running it.' : ''}
      </p>
      {created && projectName ? <p className="ck-project-name" data-testid="created-project">{projectName}</p> : null}

      <RequirementTruthBlock truth={truth} words={truthWords} />

      <h3>What changed</h3>
      {files === null ? <p className="muted" role="status">{reading('changes')}</p>
        : files.length === 0 ? <p className="muted">No file change is recorded for this run.</p>
        : (
          <>
            <p className="muted small" data-testid="change-summary">{changeSummary(files)}</p>
            <ul aria-label="Changed files">{files.map((c) => <li key={c.path}><span className="mono">{c.path}</span> <span className="muted small">{c.additions !== null && c.deletions !== null ? `+${c.additions} −${c.deletions}` : ''}</span></li>)}</ul>
          </>
        )}


      <HowToRun recipe={recipe} />

      <h3>Verification</h3>
      {groups === null ? <p className="muted" role="status">{reading('results')}</p>
        : groups.groups.length === 0 ? <p className="muted">No verification result is recorded for this run.</p>
        : <VerificationLines lines={primaryVerification(groups.technical)} />}

      <ObservedOutput observation={observation} />

      {app ? <ApplicationResult app={app} /> : null}

      {groups ? <FormalRequirementDisclosure status={formalRequirementStatus(groups.technical)} /> : null}
      {groups ? <TechnicalVerification gates={groups.technical} /> : null}
      {why !== null ? <><h3>Why</h3><p data-testid="outcome-why">{why}</p>{fullReview !== null ? <details className="ck-tech" data-testid="outcome-full-review"><summary>Full review</summary>{renderMarkdown(fullReview)}</details> : null}</> : null}
      {outcome.declined.length ? <p className="muted small">Decisions declined, nothing approved (the run proceeded without them): {outcome.declined.map((d) => `${d.kind}: ${d.reason}`).join('; ')}</p> : null}
      {phase.state === 'COMPLETED' && review && review.files.length ? (
        <details className="ck-tech"><summary>The approved change</summary>{review.files.map((f) => <FileDiff key={f.path} file={f} />)}</details>
      ) : null}
    </div>
  );
}

/** A run that did not complete: what happened, how far it got, and what the person can do; reason codes and raw detail stay under Details. */
function Stopped({ phase }: { phase: RunPhase }) {
  const ex = useExecution();
  const run = ex.run!;
  const outcome = run.outcome!;
  // Why runtime verification could not start, when Kriya recorded one of its closed reasons (read from the run's recorded ApplicationOutcome; never inferred from text here).
  const application = useApplicationOutcome(outcome.traceRunId, run.timing.refreshedAt !== null);
  const appData = application && (application.state === 'ready' || application.state === 'degraded') ? application.data : null;
  const blockedCode = appData?.status === 'NOT_APPLICABLE' && typeof appData.blocked_reason_code === 'string' ? appData.blocked_reason_code : null;
  const blocked = outcome.kind === 'failure' ? runtimeStopWords(blockedCode) : null;
  // Why Kriya refused to START (an unowned file in a new project folder): the repository layer's typed reason code and entry names on the launch record; nothing is read out of the error sentence.
  const membershipData = outcome.kind === 'failure' ? membershipStopOf(run.status?.launch.ended) : null;
  const refusedToStart = membershipData ? membershipStopWords(membershipData.code, membershipData.paths) : null;
  // How many attempts the run used and what the last unresolved finding was (recorded gate types only): the exhausted-run sentence. Nothing here reads model prose.
  const records = useRunRecords(run.launchId, outcome.traceRunId, true, run.timing.refreshedAt !== null, run.progress.lastSeq);
  const recorded = records && (records.state === 'ready' || records.state === 'degraded') ? records.data.records : null;
  const retry = recorded ? retrySummary(recorded) : null;
  return (
    <div className="ck-outcome">
      <p data-testid="what-happened"><span className="muted">What happened</span><br />
        {outcome.approval ? (
          <span><strong>{APPROVAL_KIND[outcome.approval.kind] ?? outcome.approval.kind}.</strong> {outcome.approval.reason}</span>
        ) : refusedToStart ? (
          <span data-testid="membership-stop"><strong>{refusedToStart.title}</strong><br />{refusedToStart.body}{refusedToStart.names ? <><br /><span className="mono" data-testid="membership-stop-names">{refusedToStart.names}</span></> : null}<br />{refusedToStart.action}<br /><span className="muted">{refusedToStart.note}</span></span>
        ) : blocked ? (
          <span data-testid="runtime-stop"><strong>{blocked.title}</strong><br />{blocked.body}<br /><span className="muted">{blocked.note}</span></span>
        ) : <span data-testid="failure-summary">{outcome.kind === 'failure' ? (exhaustedSummary(run.status?.launch.ended?.failure_category, outcome.commitResult, retry) ?? failureSummary(run.status?.launch.ended?.failure_category, outcome.commitResult)) : outcome.detail}</span>}
        {stoppedByYou(run) ? <span data-testid="stopped-by-you"><br />You made this decision. A new attempt is a new run - there is nothing to resume.</span> : null}
      </p>
      {outcome.approval ? (
        <div>
          {detailLines(outcome.approval.details).map((l) => <div key={l} className="muted small mono">{l}</div>)}
          <p className="muted small">{outcome.approval.kind === 'change_approval' ? 'Kriya could not hold this review open for you, so it stopped instead of deciding: nothing was approved on your behalf. Start a new run to review the change again.' : 'Nothing was approved on your behalf. A desktop run stops at a decision it cannot take for you.'}</p>
        </div>
      ) : null}
      {recorded && failedDuring(recorded) ? <p data-testid="failed-during"><span className="muted">Failed during</span><br />{failedDuring(recorded)}</p> : null}
      <p data-testid="last-stage"><span className="muted">How far it got</span><br />{phase.lastStage ? <span>{phase.lastStage}</span> : <span>It stopped before any work was recorded.</span>}</p>
      {outcome.declined.length ? <p className="muted small">Decisions declined, nothing approved (the run proceeded without them): {outcome.declined.map((d) => `${d.kind}: ${d.reason}`).join('; ')}</p> : null}
      <details className="ck-tech">
        <summary>Technical reason</summary>
        <div className="muted small">{blockedCode ? <div data-testid="runtime-stop-code">Reason code <span className="mono">{blockedCode}</span></div> : null}<span className="mono">{outcome.kind === 'failure' ? outcome.detail : `Outcome kind ${outcome.kind}`}</span>{outcome.recordedError && outcome.recordedError !== outcome.detail && outcome.kind !== 'approval_required' ? <span> · <strong>Kriya reported:</strong> <span className="mono">{outcome.recordedError}</span></span> : null}</div>
        <p><CopyText what="failure details" label="Copy details" text={[`Run ${run.launchId}: ${outcome.label}`, outcome.detail, outcome.recordedError ? `Kriya reported: ${outcome.recordedError}` : null, `Source: ${outcome.source}`].filter(Boolean).join('\n')} /></p>
      </details>
    </div>
  );
}

/** Kriya's own record that the person rejected the review (the launch's approval history): the run then ends as the person's own decision, not as a failure. */
export function stoppedByYou(run: { status: { launch: { approval_history?: ReadonlyArray<{ status: string }>; pending_approval?: { status: string } | null } } | null }): boolean {
  const launch = run.status?.launch;
  return Boolean(launch && ((launch.approval_history ?? []).some((r) => r.status === 'REJECTED') || launch.pending_approval?.status === 'REJECTED'));
}

/**
 * A cancelled run, in Kriya's words about the FOLDER (read after the process ended): Cancelled (Kriya reports CLEAN), Cancelled - recovery required (any other recovery status; Recover Project is
 * offered), or Cancelled - folder state not confirmed (the status could not be read; never "clean"). A new attempt is a NEW run: nothing here is a resume.
 */
function CancelledCard() {
  const ex = useExecution();
  const state = ex.cancelState;
  if (state.phase !== 'settled') return null;
  const folder = state.folder;
  const clean = folder.kind === 'clean';
  return (
    <div className="ck-outcome" data-testid="cancelled" data-folder={folder.kind}>
      <p data-testid="what-happened"><span className="muted">What happened</span><br />
        {clean ? <span>You cancelled this run. Kriya reports the project folder is clean: nothing was applied and nothing needs recovery.</span>
          : folder.kind === 'recovery' ? <span><strong>You cancelled this run, and the project folder needs recovery.</strong> {folderFact(folder.status).note ?? ''} <span className="muted small">(Kriya reports {folder.status})</span></span>
          : <span><strong>You cancelled this run, but the project folder&apos;s state is not confirmed.</strong> {folder.reason}. Nothing is being assumed - recover the project, or check it before starting another run.</span>}
      </p>
      {ex.recoverState.note ? <p className="muted small" role="status" data-testid="recover-note">{ex.recoverState.note}</p> : null}
      <h4 className="ck-action-sub">What you can do</h4>
      <div className="ck-current-actions">
        {!clean && ex.canRecover ? <button type="button" className="ck-primary" disabled={ex.recoverState.running} onClick={() => void ex.recoverProject()}>{ex.recoverState.running ? 'Recovering…' : 'Recover Project'}</button> : null}
        {clean ? <button type="button" className="ck-primary" onClick={ex.rerun}>Run again</button> : null}
        <button type="button" onClick={ex.runAgain}>Edit goal and retry</button>
        <button type="button" onClick={ex.newGoal}>New Goal</button>
        <OpenInEditorButton />
        <RevealButton />
      </div>
    </div>
  );
}

/** The run this window started, as the whole page: what Kriya is doing now, whether it needs the person, and the result. Everything comes from Kriya's records through runPhase and the presentation layer. */
export function CurrentRun() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const run = ex.run;
  const { entries } = useRunActivity(run);
  const dockUi = useWorkspaceUi();
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished', run?.status?.launch.ended?.at ?? null);
  if (!run) return null;
  const phase = runPhase(run);
  const finished = phase.terminal;
  const outcome0 = run.outcome;
  const decided = run.status?.launch.pending_approval?.status;
  const pendingKind = run.status?.launch.pending_approval?.kind;
  const finishing = !finished && !run.review && !run.runtimeReview && !run.verifierReview && run.status?.launch.phase === 'RUNNING' && decided === 'APPROVED' && pendingKind !== 'runtime_command' && pendingKind !== 'verifier_defect_repair_authorization';
  const viewDetails = (tab?: (typeof OPEN_TARGETS)[keyof typeof OPEN_TARGETS]) => {
    if (finished && run.outcome?.traceRunId) dispatch({ type: 'selectRun', runId: run.outcome.traceRunId });
    else dispatch({ type: 'selectLiveRun', launchId: run.launchId });
    if (tab) dispatch({ type: 'centralTab', tab });
    go(RUN_DOCUMENTS_PAGE);
  };
  const cancelling = ex.cancelState.phase === 'cancelling';
  const cancelled = ex.cancelState.phase === 'settled' && ex.cancelState.launchId === run.launchId;
  const byYou = finished && phase.state === 'STOPPED' && stoppedByYou(run);
  const cancelHeading = cancelled && ex.cancelState.phase === 'settled' ? (ex.cancelState.folder.kind === 'clean' ? 'Cancelled' : ex.cancelState.folder.kind === 'recovery' ? 'Cancelled — recovery required' : 'Cancelled — folder state not confirmed') : null;
  const completedWords = requirementTruthWords(requirementTruthOf(run.status?.launch.ended));   // ALL_VERIFIED / not recorded: 'Run completed'; open or contradicted: never ordinary green
  const heading = cancelHeading ?? (cancelling ? 'Cancelling…' : finished ? (phase.state === 'COMPLETED' ? completedWords.heading : phase.state === 'STOPPED' ? (byYou ? 'Stopped by you' : 'Run stopped') : 'Run failed') : finishing ? 'Finishing your change' : phase.label);
  const live = !finished && !cancelled;
  // A finished, completed run whose requirement truth carries no extra badge already says everything in its heading ("Completed — verified", or plain "Completed"): a second green "Run succeeded"
  // pill beside it is noise (owner ruling 2026-11-09). The process outcome stays in the details; every other state keeps its badge (open requirements, failure, cancelled, in progress, needs you).
  const showOutcomeBadge = !(finished && !cancelled && !byYou && phase.state === 'COMPLETED' && completedWords.badge === null);
  const goalLine = ex.goal.trim() ? <div className="ck-goalline"><span className="muted">Goal</span><div>{ex.goal}</div></div> : null;
  const nowText = live ? activityText(phase.state, finishing, phase.verifyingApplication === true) : null;
  const recent = live ? entries.slice(-RECENT_ACTIVITY) : [];
  // Recover Project is offered only when Kriya itself says the folder needs recovery (never inferred from the run having failed).
  const needsRecovery = ex.preflight.state === 'ready' && ex.preflight.data.recovery.status === 'RECOVERY_AVAILABLE';
  return (
    <section className="ck-current" aria-label="Current run" data-live={live ? 'true' : 'false'} data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-state={phase.state} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-current-head">
        <div role="status" aria-live="polite" className="ck-current-status">
          <span className={`ck-dot ${finished && phase.state === 'COMPLETED' && completedWords.tone !== 'ok' ? completedWords.tone : phase.tone}`} aria-hidden="true">{cancelled || byYou ? '■' : finished ? (phase.state === 'COMPLETED' ? (completedWords.tone === 'ok' ? '✓' : '⚠') : phase.state === 'STOPPED' ? '■' : '✕') : '●'}</span>
          <h2>{heading}</h2>
          {showOutcomeBadge ? <Badge tone={cancelled || byYou ? 'warn' : finished && phase.state === 'COMPLETED' && completedWords.tone !== 'ok' ? completedWords.tone : TONE[phase.tone]}>{cancelled ? (ex.cancelState.phase === 'settled' && ex.cancelState.folder.kind === 'clean' ? 'cancelled by you' : 'cancelled by you - check the folder') : cancelling ? 'cancelling' : finished ? (phase.state === 'COMPLETED' && completedWords.badge !== null ? completedWords.badge : run.outcome!.label) : phase.state === 'WAITING_FOR_APPROVAL' ? 'needs you' : 'in progress'}</Badge> : null}
        </div>
        {elapsed ? <span className="muted small" title="Local clock since the launch was recorded - presentation only">elapsed {elapsed}</span> : null}
        {ex.canCancel && live ? <button type="button" className="ck-cancel" onClick={() => void ex.cancelRun()}>Cancel Run</button> : null}
        {cancelling ? <button type="button" className="ck-cancel" disabled>Cancelling…</button> : null}
      </div>
      {!live && goalLine}
      {phase.steps && live ? <RunStepper steps={phase.steps} waiting={phase.needsAction} /> : null}
      {live && goalLine}
      {live && finishing ? <p className="ck-detail" data-testid="finishing-note">{phase.detail}</p> : null}
      {live && !run.review && !run.runtimeReview && !run.verifierReview && nowText ? <div className="ck-now" data-testid="current-activity"><span className="ck-now-label">What Kriya is doing now</span><p>{nowText}</p></div> : null}
      {live && !run.review && !run.runtimeReview && !run.verifierReview && recent.length ? (
        <div className="ck-recent" data-testid="recent-activity">
          <h3>Recent activity</h3>
          <ol aria-label="Recent activity">{recent.map((e) => <li key={e.key} data-tone={e.tone}><span className="mono small muted">{localClock(e.at)}</span> <span aria-hidden="true">{ACTIVITY_MARK[e.tone]}</span> {e.text}</li>)}</ol>
        </div>
      ) : null}
      {live && run.review ? <ApprovalReview review={run.review} launchId={run.launchId} /> : null}
      {live && run.runtimeReview ? <RuntimeCommandReviewCard review={run.runtimeReview} launchId={run.launchId} /> : null}
      {live && run.verifierReview ? <VerifierRepairReviewCard review={run.verifierReview} launchId={run.launchId} /> : null}
      {!finished && !run.review && !run.runtimeReview && !run.verifierReview && run.status?.launch.phase === 'RUNNING' && decided === 'REJECTED' ? <div role="note"><strong>Your rejection was recorded.</strong> {pendingKind === 'verifier_defect_repair_authorization' ? 'Kriya will not repair the verifier; the run continues.' : pendingKind === 'runtime_command' ? 'Kriya will not run those commands.' : 'Kriya is ending the run; nothing will be applied.'}</div> : null}
      {cancelled ? <CancelledCard /> : null}
      {finished && !cancelled && phase.state === 'COMPLETED' ? <Completed phase={phase} /> : null}
      {finished && !cancelled && phase.state !== 'COMPLETED' ? <Stopped phase={phase} /> : null}
      <details className="ck-tech">
        <summary>Technical status</summary>
        <div><span className="muted">Run ID</span> <span className="mono small">{run.launchId}</span> <CopyText text={run.launchId} what="run ID" /></div>
        {run.status ? (
          <div className="muted small">
            Observed in Kriya&apos;s records ({run.progress.lastSeq} evidence record{run.progress.lastSeq === 1 ? '' : 's'}): {progressCounts(run.progress).map((c) => `${c.label} ${c.count}`).join(' · ')}
            {run.progress.recent.length ? ` · latest: ${run.progress.recent[run.progress.recent.length - 1]!.kind}` : ''}
            {' · '}liveness {run.status.liveness} <span title={run.status.liveness_note}>(DERIVED)</span>
          </div>
        ) : null}
        {finished ? <div className="muted small">{phase.state === 'COMPLETED' ? `Recorded outcome: ${outcome0!.detail} ` : ''}Source: {outcome0!.source}{outcome0!.commitResult ? ` · commit result ${outcome0!.commitResult}` : ''}.</div> : null}
        {run.host && run.host.phase === 'exited' && !finished ? <div className="muted small">The execution process has ended; reading Kriya&apos;s final records…</div> : null}
      </details>
      {run.progress.error ? <div className="muted small" role="note">Live records could not be read: {run.progress.error}</div> : null}
      {run.statusError ? <div className="muted small" role="alert">Launch status unreadable: {run.statusError.code}: {run.statusError.message}</div> : null}
      {!cancelled ? (
        <>
          {finished && phase.state !== 'COMPLETED' ? <h4 className="ck-action-sub" data-testid="what-you-can-do">What you can do</h4> : null}
          <div className="ck-current-actions">
            {finished ? (
              <>
                {phase.state !== 'COMPLETED' ? <button type="button" className="ck-primary" onClick={ex.rerun}>Run again</button> : null}
                {phase.state !== 'COMPLETED' ? <button type="button" onClick={ex.runAgain}>Edit goal and retry</button> : null}
                {phase.state !== 'COMPLETED' && needsRecovery && ex.canRecover ? <button type="button" disabled={ex.recoverState.running} onClick={() => void ex.recoverProject()}>{ex.recoverState.running ? 'Recovering…' : 'Recover Project'}</button> : null}
                <OpenInEditorButton />
                <RevealButton />
                {phase.state === 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.changes)}>View Changes</button> : null}
                {phase.state === 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.validation)}>View Verification</button> : <button type="button" onClick={() => viewDetails()}>View Details</button>}
                {phase.state !== 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.changes)}>View Changes</button> : null}
                {phase.state !== 'COMPLETED' ? <button type="button" data-testid="view-kriya-log" onClick={dockUi.showKriyaLog}>View Kriya Log</button> : null}
                <span className="grow" />
                <button type="button" className={phase.state === 'COMPLETED' ? 'ck-primary' : undefined} onClick={ex.newGoal}>New Goal</button>
              </>
            ) : <button type="button" onClick={() => viewDetails()}>View Details</button>}
          </div>
          {ex.recoverState.note ? <p className="muted small" role="status" data-testid="recover-note">{ex.recoverState.note}</p> : null}
        </>
      ) : null}
    </section>
  );
}
