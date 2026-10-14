import { ApprovalReview, Badge, CopyText, OPEN_TARGETS, RUN_DOCUMENTS_PAGE, describeGate, progressCounts, runPhase, useDispatch, useElapsed, useExecution, useNavigate, useRunRowFacts, type RunPhase } from '../../core';

const TONE = { info: 'info', warn: 'warn', ok: 'ok', bad: 'bad' } as const;
const MARK = { done: '✓', current: '●', pending: '○' } as const;
const APPROVAL_KIND: Record<string, string> = {
  change_approval: 'a proposed change needs approval', knowledge_gap_acknowledgement: 'a knowledge gap needs acknowledgement', skill_gap_decision: 'a skill gap needs a decision',
  skill_conflict_decision: 'two skills may conflict', web_lookup_review: 'live-lookup references need review', web_lookup_query: 'a live-lookup search needs approval',
};
const detailLines = (d: Record<string, unknown>): string[] => Object.entries(d).flatMap(([k, v]) => (Array.isArray(v) && v.length ? [`${k}: ${v.map(String).join(', ')}`] : []));

/** The run's outcome in plain words, from the recorded gates and staged changes of THIS run's evidence. Nothing is shown that the records do not say. */
function Outcome({ phase }: { phase: RunPhase }) {
  const ex = useExecution();
  const run = ex.run!;
  const outcome = run.outcome!;
  // Independent read of THIS finished run's row (after the refreshed snapshot is pinned); it never changes which run is selected elsewhere.
  const facts = useRunRowFacts(phase.state === 'COMPLETED' ? outcome.traceRunId : null, run.timing.refreshedAt !== null);
  const changes = facts && facts.state === 'ready' ? facts.changes : null;
  const gates = facts && facts.state === 'ready' ? facts.gates : null;
  const why = facts && facts.state === 'unavailable' ? `The recorded facts could not be read here (${facts.detail}). Use View Details.` : null;
  const reading = (what: string) => (why ?? (outcome.traceRunId === null ? `No trace row was recorded, so ${what} are not available here.` : `Reading the recorded ${what}…`));
  return (
    <div className="ck-outcome">
      {phase.state === 'COMPLETED' ? (
        <>
          <h3>Changed</h3>
          {changes === null ? <p className="muted" role="status">{reading('changes')}</p>
            : changes.length === 0 ? <p className="muted">No staged change is recorded for this run.</p>
            : <ul aria-label="Changed files">{changes.map((c) => <li key={c.path}><span className="mono">{c.path}</span> <span className="muted small">{c.linesAdded !== null && c.linesRemoved !== null ? `+${c.linesAdded} −${c.linesRemoved}` : 'line counts not recorded'}</span></li>)}</ul>}
          <h3>Validation</h3>
          {gates === null ? <p className="muted" role="status">{reading('gates')}</p>
            : gates.length === 0 ? <p className="muted">No gate result is recorded for this run.</p>
            : <div className="ck-badges" aria-label="Validation">{gates.map((g) => <Badge key={g.type} tone={g.passed === true ? 'ok' : g.passed === false ? 'bad' : 'neutral'}>{g.passed === true ? '✓ ' : g.passed === false ? '✕ ' : ''}{describeGate(g)}</Badge>)}</div>}
        </>
      ) : (
        <>
          <h3>Reason</h3>
          {outcome.approval ? (
            <div>
              <div><strong>{APPROVAL_KIND[outcome.approval.kind] ?? outcome.approval.kind}.</strong> {outcome.approval.reason}</div>
              {detailLines(outcome.approval.details).map((l) => <div key={l} className="muted small mono">{l}</div>)}
              <p className="muted small">{outcome.approval.kind === 'change_approval' ? 'Kriya could not hold this review open for you, so it stopped instead of deciding: nothing was approved on your behalf. Start a new run to review the change.' : 'Kriya stopped here instead of deciding for you. This version of the app cannot take this kind of decision: it must be performed outside the Kriya GUI (nothing was approved on your behalf).'}</p>
            </div>
          ) : <p>{outcome.detail}</p>}
          {outcome.recordedError && outcome.recordedError !== outcome.detail && outcome.kind !== 'approval_required' ? <p className="muted small"><strong>Kriya reported:</strong> <span className="mono">{outcome.recordedError}</span></p> : null}
          {phase.lastStage ? <p className="muted">Last stage the records show: <strong>{phase.lastStage}</strong>.</p> : <p className="muted">The records show no stage reached.</p>}
        </>
      )}
      {phase.state === 'COMPLETED' ? <p className="muted small">{outcome.detail}</p> : null}
      {outcome.declined.length ? <p className="muted small">Decisions declined, nothing approved (the run proceeded without them): {outcome.declined.map((d) => `${d.kind}: ${d.reason}`).join('; ')}</p> : null}
      <p className="muted small">Source: {outcome.source}.</p>
      {phase.state !== 'COMPLETED' ? <p><CopyText what="failure details" label="Copy details" text={[`Run ${run.launchId}: ${outcome.label}`, outcome.detail, outcome.recordedError ? `Kriya reported: ${outcome.recordedError}` : null, `Source: ${outcome.source}`].filter(Boolean).join('\n')} /></p> : null}
    </div>
  );
}

/** The run this window started, as the whole page: what Kriya is doing now, whether it needs the person, and the outcome. Everything comes from Kriya's records through runPhase. */
export function CurrentRun() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const run = ex.run;
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished');
  if (!run) return null;
  const phase = runPhase(run);
  const finished = phase.terminal;
  const goalChars = run.status?.launch.goal.chars ?? null;
  const viewDetails = (tab?: (typeof OPEN_TARGETS)[keyof typeof OPEN_TARGETS]) => {
    if (finished && run.outcome?.traceRunId) dispatch({ type: 'selectRun', runId: run.outcome.traceRunId });
    else dispatch({ type: 'selectLiveRun', launchId: run.launchId });
    if (tab) dispatch({ type: 'centralTab', tab });
    go(RUN_DOCUMENTS_PAGE);
  };
  return (
    <section className="ck-current" aria-label="Current run" data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-state={phase.state} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-current-head">
        <div role="status" aria-live="polite" className="ck-current-status">
          <span className={`ck-dot ${phase.tone}`} aria-hidden="true">{finished ? (phase.state === 'COMPLETED' ? '✓' : phase.state === 'STOPPED' ? '■' : '✕') : '●'}</span>
          <h2>{finished ? (phase.state === 'COMPLETED' ? 'Run completed' : phase.state === 'STOPPED' ? 'Run stopped' : 'Run failed') : phase.label}</h2>
          <Badge tone={TONE[phase.tone]}>{finished ? run.outcome!.label : phase.state === 'WAITING_FOR_APPROVAL' ? 'needs you' : 'in progress'}</Badge>
        </div>
        {elapsed ? <span className="muted small" title="Local clock since the launch was recorded - presentation only">elapsed {elapsed}</span> : null}
      </div>
      <div className="ck-goalline"><span className="muted">Goal</span><div>{ex.goal.trim() ? ex.goal : `(the goal text is not kept by Kriya; ${goalChars ?? '?'} characters)`}</div></div>
      {!finished ? <p className="ck-detail">{phase.detail}</p> : null}
      {phase.steps ? (
        <ol className="ck-steps" aria-label="Progress">
          {phase.steps.map((s) => <li key={s.id} data-status={s.status} aria-current={s.status === 'current' ? 'step' : undefined}><span aria-hidden="true">{MARK[s.status]}</span> {s.label}<span className="sr-only"> ({s.status === 'done' ? 'done' : s.status === 'current' ? 'in progress' : 'not started'})</span></li>)}
        </ol>
      ) : null}
      {!finished && run.review ? <ApprovalReview review={run.review} launchId={run.launchId} /> : null}
      {!finished && !run.review && run.status?.launch.phase === 'RUNNING' && (run.status.launch.pending_approval?.status === 'APPROVED' || run.status.launch.pending_approval?.status === 'REJECTED') ? (
        <div role="note"><strong>Your {run.status.launch.pending_approval.status === 'APPROVED' ? 'approval' : 'rejection'} is recorded by Kriya</strong>; the run is continuing from it. Its outcome will come from Kriya&apos;s records.</div>
      ) : null}
      {finished ? <Outcome phase={phase} /> : null}
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
        {run.host && run.host.phase === 'exited' && !finished ? <div className="muted small">The execution process has ended; reading Kriya&apos;s final records…</div> : null}
      </details>
      {run.progress.error ? <div className="muted small" role="note">Live records could not be read: {run.progress.error}</div> : null}
      {run.statusError ? <div className="muted small" role="alert">Launch status unreadable: {run.statusError.code}: {run.statusError.message}</div> : null}
      <div className="ck-current-actions">
        {finished ? (
          <>
            {phase.state === 'COMPLETED' ? <button type="button" onClick={() => viewDetails(OPEN_TARGETS.changes)}>View Changes</button> : null}
            <button type="button" onClick={() => viewDetails(OPEN_TARGETS.evidence)}>View Evidence</button>
            <button type="button" onClick={() => viewDetails()}>View Details</button>
            <span className="grow" />
            <button type="button" className="ck-primary" onClick={ex.dismiss}>New Goal</button>
          </>
        ) : <button type="button" onClick={() => viewDetails()}>View Details</button>}
      </div>
    </section>
  );
}
