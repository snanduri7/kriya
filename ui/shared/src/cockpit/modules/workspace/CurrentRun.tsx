import { useState } from 'react';
import { ActivityDrawer, ApplicationResult, ApprovalReview, RuntimeCommandReviewCard, Badge, CopyText, FileDiff, failureSummary, runtimeStopWords, OPEN_TARGETS, RUN_DOCUMENTS_PAGE, activityText, changeSummary, progressCounts, runPhase, useApplicationOutcome, useChangeReview, useDispatch, useElapsed, useExecution, useNavigate, useProvider, useRunRowFacts, useWorkspacePath, VerificationLines, verificationGroups, type ChangedFile, type RunPhase } from '../../core';

const TONE = { info: 'info', warn: 'warn', ok: 'ok', bad: 'bad' } as const;
const MARK = { done: '✓', current: '●', pending: '○' } as const;
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
  const reviewed = useChangeReview(run.launchId, approvedReview?.review_id ?? null);
  const review = reviewed.state === 'ready' || reviewed.state === 'degraded' ? reviewed.data : null;
  const gates = facts && facts.state === 'ready' ? facts.gates : null;
  const files: ChangedFile[] | null = review
    ? review.files.map((f) => ({ path: f.path, additions: f.additions, deletions: f.deletions, changeType: f.change_type }))
    : facts && facts.state === 'ready' ? facts.changes.map((c) => ({ path: c.path, additions: c.linesAdded, deletions: c.linesRemoved, changeType: c.kind === 'ADDED' || c.kind === 'CREATED' ? 'added' as const : null })) : null;
  const why = review?.review_summary ?? review?.policy_note ?? null;
  const unreadable = facts && facts.state === 'unavailable' ? `The recorded results could not be read here (${facts.detail}). Use View Details.` : null;
  const reading = (what: string) => (unreadable ?? (outcome.traceRunId === null ? `No trace row was recorded, so ${what} are not available here.` : `Reading the recorded ${what}…`));
  const groups = gates ? verificationGroups(gates) : null;
  const app = application && (application.state === 'ready' || application.state === 'degraded') ? application.data : null;
  const applied = outcome.commitResult === 'COMMITTED';
  const workspace = useWorkspacePath();
  // A NEW application: every recorded change is a file Kriya added AND Kriya recorded executing it successfully (from the executor's recorded steps - never a claim).
  const created = applied && app?.status === 'EXECUTED' && app.succeeded === true && files !== null && files.length > 0 && files.every((f) => f.changeType === 'added');
  const projectName = workspace ? workspace.split('/').filter(Boolean).pop() ?? null : null;
  return (
    <div className="ck-outcome">
      <p className="ck-applied" data-testid="applied-statement">
        {created ? <strong>✓ Application created successfully</strong> : applied ? <strong>✓ Your change was applied successfully.</strong> : <span>Kriya did not record that the change was applied to your folder. Check your folder before relying on it.</span>}
        {approvedReview ? ' You approved the change.' : ''}{allowedCommands ? ' You allowed Kriya to run the verification commands.' : ''}
      </p>
      {created && projectName ? <p className="ck-project-name" data-testid="created-project">{projectName}</p> : null}

      <h3>What changed</h3>
      {files === null ? <p className="muted" role="status">{reading('changes')}</p>
        : files.length === 0 ? <p className="muted">No file change is recorded for this run.</p>
        : (
          <>
            <p className="muted small" data-testid="change-summary">{changeSummary(files)}</p>
            <ul aria-label="Changed files">{files.map((c) => <li key={c.path}><span className="mono">{c.path}</span> <span className="muted small">{c.additions !== null && c.deletions !== null ? `+${c.additions} −${c.deletions}` : ''}</span></li>)}</ul>
          </>
        )}

      {why !== null ? <><h3>Why</h3><p data-testid="outcome-why">{why}</p></> : null}

      <h3>Verification</h3>
      {groups === null ? <p className="muted" role="status">{reading('results')}</p>
        : groups.groups.length === 0 ? <p className="muted">No verification result is recorded for this run.</p>
        : <VerificationLines lines={groups.groups.flatMap((g) => g.lines)} />}

      {app ? <ApplicationResult app={app} /> : null}

      {groups && groups.technical.length ? (
        <details className="ck-tech">
          <summary>Technical verification</summary>
          <ul aria-label="Technical verification">{groups.technical.map((g, i) => <li key={`${g.type}${i}`} className="mono small">{g.passed === true ? '✓' : g.passed === false ? '✕' : '•'} {g.type}</li>)}</ul>
        </details>
      ) : null}
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
  return (
    <div className="ck-outcome">
      <p data-testid="what-happened"><span className="muted">What happened</span><br />
        {outcome.approval ? (
          <span><strong>{APPROVAL_KIND[outcome.approval.kind] ?? outcome.approval.kind}.</strong> {outcome.approval.reason}</span>
        ) : blocked ? (
          <span data-testid="runtime-stop"><strong>{blocked.title}</strong><br />{blocked.body}<br /><span className="muted">{blocked.note}</span></span>
        ) : <span>{outcome.kind === 'failure' ? failureSummary(run.status?.launch.ended?.failure_category, outcome.commitResult) : outcome.detail}</span>}
      </p>
      {outcome.approval ? (
        <div>
          {detailLines(outcome.approval.details).map((l) => <div key={l} className="muted small mono">{l}</div>)}
          <p className="muted small">{outcome.approval.kind === 'change_approval' ? 'Kriya could not hold this review open for you, so it stopped instead of deciding: nothing was approved on your behalf. Start a new run to review the change again.' : 'Nothing was approved on your behalf. A desktop run stops at a decision it cannot take for you.'}</p>
        </div>
      ) : null}
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

/** The run this window started, as the whole page: what Kriya is doing now, whether it needs the person, and the result. Everything comes from Kriya's records through runPhase and the presentation layer. */
export function CurrentRun() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const run = ex.run;
  const [activityOpen, setActivityOpen] = useState(false);
  const desktop = useProvider().desktop;
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished', run?.status?.launch.ended?.at ?? null);
  if (!run) return null;
  const phase = runPhase(run);
  const finished = phase.terminal;
  const outcome0 = run.outcome;
  const decided = run.status?.launch.pending_approval?.status;
  const pendingKind = run.status?.launch.pending_approval?.kind;
  const finishing = !finished && !run.review && !run.runtimeReview && run.status?.launch.phase === 'RUNNING' && decided === 'APPROVED' && pendingKind !== 'runtime_command';
  const viewDetails = (tab?: (typeof OPEN_TARGETS)[keyof typeof OPEN_TARGETS]) => {
    if (finished && run.outcome?.traceRunId) dispatch({ type: 'selectRun', runId: run.outcome.traceRunId });
    else dispatch({ type: 'selectLiveRun', launchId: run.launchId });
    if (tab) dispatch({ type: 'centralTab', tab });
    go(RUN_DOCUMENTS_PAGE);
  };
  const openProject = desktop?.revealWorkspace ? () => { void desktop.revealWorkspace!(); } : null;
  const heading = finished ? (phase.state === 'COMPLETED' ? 'Run completed' : phase.state === 'STOPPED' ? 'Run stopped' : 'Run failed') : finishing ? 'Finishing your change' : phase.label;
  return (
    <section className="ck-current" aria-label="Current run" data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-state={phase.state} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-current-head">
        <div role="status" aria-live="polite" className="ck-current-status">
          <span className={`ck-dot ${phase.tone}`} aria-hidden="true">{finished ? (phase.state === 'COMPLETED' ? '✓' : phase.state === 'STOPPED' ? '■' : '✕') : '●'}</span>
          <h2>{heading}</h2>
          <Badge tone={TONE[phase.tone]}>{finished ? run.outcome!.label : phase.state === 'WAITING_FOR_APPROVAL' ? 'needs you' : 'in progress'}</Badge>
        </div>
        {elapsed ? <span className="muted small" title="Local clock since the launch was recorded - presentation only">elapsed {elapsed}</span> : null}
      </div>
      {ex.goal.trim() ? <div className="ck-goalline"><span className="muted">Goal</span><div>{ex.goal}</div></div> : null}
      {finishing ? <p className="ck-detail" data-testid="finishing-note">{phase.detail}</p> : null}
      {phase.steps && !finished ? (
        <ol className="ck-steps" aria-label="Progress">
          {phase.steps.map((s) => <li key={s.id} data-status={s.status} aria-current={s.status === 'current' ? 'step' : undefined}><span aria-hidden="true">{MARK[s.status]}</span> {s.label}<span className="sr-only"> ({s.status === 'done' ? 'done' : s.status === 'current' ? 'in progress' : 'not started'})</span></li>)}
        </ol>
      ) : null}
      {!finished && !run.review && !run.runtimeReview ? <p className="ck-detail" data-testid="current-activity"><span className="muted">Current activity</span><br />{activityText(phase.state, finishing)}</p> : null}
      {!finished && run.review ? <ApprovalReview review={run.review} launchId={run.launchId} /> : null}
      {!finished && run.runtimeReview ? <RuntimeCommandReviewCard review={run.runtimeReview} launchId={run.launchId} /> : null}
      {!finished && !run.review && !run.runtimeReview && run.status?.launch.phase === 'RUNNING' && decided === 'REJECTED' ? <div role="note"><strong>Your rejection was recorded.</strong> Kriya is ending the run; {pendingKind === 'runtime_command' ? 'nothing will be run' : 'nothing will be applied'}.</div> : null}
      {finished && phase.state === 'COMPLETED' ? <Completed phase={phase} /> : null}
      {finished && phase.state !== 'COMPLETED' ? <Stopped phase={phase} /> : null}
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
      {activityOpen ? <ActivityDrawer launchId={run.launchId} onClose={() => setActivityOpen(false)} /> : null}
      <div className="ck-current-actions">
        {!activityOpen ? <button type="button" className="ck-quiet" onClick={() => setActivityOpen(true)}>Show Activity</button> : null}
        {finished ? (
          <>
            {phase.state === 'COMPLETED' && openProject ? <button type="button" onClick={openProject}>Open Project</button> : null}
            {phase.state === 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.changes)}>View Changes</button> : null}
            {phase.state === 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.validation)}>View Verification</button> : <button type="button" onClick={() => viewDetails()}>View Failure Details</button>}
            {phase.state !== 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.changes)}>View Changes</button> : null}
            <span className="grow" />
            <button type="button" className="ck-primary" onClick={ex.dismiss}>New Goal</button>
          </>
        ) : <button type="button" onClick={() => viewDetails()}>View Details</button>}
      </div>
    </section>
  );
}
