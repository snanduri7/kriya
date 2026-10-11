import { useCallback, useEffect, useState } from 'react';
import { Badge, CopyText, RUN_DOCUMENTS_PAGE, progressCounts, useDispatch, useExecution, useNavigate, useProvider, type RunView } from '../core';
import { ApprovalReview } from './ApprovalReview';

const STAGE_LABEL: Record<RunView['stage'], string> = { starting: 'Starting', running: 'Running', finished: 'Finished' };

function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Elapsed time since the launch record's own timestamp. Presentation only: it is a local clock difference and decides nothing. */
function useElapsed(since: string | null, running: boolean): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (since === null) return null;
  const start = Date.parse(since);
  return Number.isFinite(start) ? clock(now - start) : null;
}

const TONE = { success: 'ok', failure: 'bad', needs_review: 'warn', refused: 'bad', interrupted: 'warn', not_recorded: 'warn', approval_required: 'warn', rejected: 'warn', review_expired: 'warn' } as const;
const APPROVAL_KIND: Record<string, string> = {
  change_approval: 'a proposed change needs approval', knowledge_gap_acknowledgement: 'a knowledge gap needs acknowledgement', skill_gap_decision: 'a skill gap needs a decision',
  skill_conflict_decision: 'two skills may conflict', web_lookup_review: 'live-lookup references need review', web_lookup_query: 'a live-lookup search needs approval',
};
const detailLines = (d: Record<string, unknown>): string[] => Object.entries(d).flatMap(([k, v]) => (Array.isArray(v) && v.length ? [`${k}: ${v.map(String).join(', ')}`] : []));

/** The one run this window started, across every page: what Kriya's records say now, and the durable outcome once there is one. */
export function ActiveRunBanner() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const desktop = useProvider().desktop;
  const run = ex.run;
  // Back to the run this window started: select it (live while running, its recorded row once finished), open the run documents, and put keyboard focus on the
  // pending review when there is one. Used by the "Return to run" button, the approval indicator and a clicked notification.
  const showRun = useCallback(() => {
    if (!run) return;
    if (run.stage !== 'finished') dispatch({ type: 'selectLiveRun', launchId: run.launchId });
    else if (run.outcome?.traceRunId) dispatch({ type: 'selectRun', runId: run.outcome.traceRunId });
    go(RUN_DOCUMENTS_PAGE);
    if (run.stage !== 'finished' && run.review) {
      // the page may still be rendering: try for a short while until the review section exists
      let tries = 0;
      const focus = () => { const el = document.querySelector<HTMLElement>('section[aria-label="Change review"]'); if (el) el.focus(); else if (tries++ < 10) setTimeout(focus, 50); };
      setTimeout(focus, 0);
    }
  }, [run, dispatch, go]);
  // A notification click arrives as the fixed command `showRun` (no payload): the current run is the only run there is.
  useEffect(() => (desktop ? desktop.onMenuCommand((command) => { if (command === 'showRun') showRun(); }) : undefined), [desktop, showRun]);
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished');
  if (!run) return null;
  const finished = run.stage === 'finished' && run.outcome !== null;
  const counts = progressCounts(run.progress).map((c) => [c.label, c.count] as const);
  const lines = run.progress.lastSeq;
  const last = run.progress.recent[run.progress.recent.length - 1];
  return (
    <section className="ck-runbar ck-state" role="status" aria-live="polite" aria-label="Active run" data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-runbar-head">
        <strong>{finished ? run.outcome!.label : run.review ? 'Approval required' : `Run ${STAGE_LABEL[run.stage].toLowerCase()}`}</strong>
        <span className="muted mono small" title="The launch id is the run id">{run.launchId}</span> <CopyText text={run.launchId} what="run ID" />
        {!finished ? (run.review ? <Badge tone="warn">approval required</Badge> : <Badge tone="info">{STAGE_LABEL[run.stage]}</Badge>) : <Badge tone={TONE[run.outcome!.kind]}>{run.outcome!.kind.replace('_', ' ')}</Badge>}
        {elapsed ? <span className="muted small" title="Local clock since the launch was recorded - presentation only">elapsed {elapsed}</span> : null}
        <span className="grow" />
        {!finished ? (
          <>
            {run.review ? <button type="button" className="ck-primary" onClick={showRun}>1 approval waiting · Go to review</button> : null}
            <button type="button" onClick={showRun}>Return to run</button>
          </>
        ) : null}
        {finished ? (
          <>
            {run.outcome!.traceRunId ? <button type="button" onClick={() => dispatch({ type: 'selectRun', runId: run.outcome!.traceRunId })}>View run</button> : null}
            <button type="button" onClick={ex.dismiss}>Dismiss</button>
          </>
        ) : null}
      </div>
      {!finished && run.review ? <ApprovalReview review={run.review} launchId={run.launchId} /> : null}
      {!finished && !run.review && run.status?.launch.phase === 'RUNNING' && (run.status.launch.pending_approval?.status === 'APPROVED' || run.status.launch.pending_approval?.status === 'REJECTED') ? <div role="note"><strong>Your {run.status.launch.pending_approval.status === 'APPROVED' ? 'approval' : 'rejection'} is recorded by Kriya</strong>; the run is continuing from it. Its outcome will come from Kriya&apos;s records.</div> : null}
      {finished && run.outcome!.approval ? (
        <div>
          <div><strong>{APPROVAL_KIND[run.outcome!.approval.kind] ?? run.outcome!.approval.kind}.</strong> {run.outcome!.approval.reason}</div>
          {detailLines(run.outcome!.approval.details).map((l) => <div key={l} className="muted small mono">{l}</div>)}
          <div className="muted small">{run.outcome!.approval.kind === 'change_approval' ? 'Kriya could not hold this review open for you, so it stopped instead of deciding: nothing was approved on your behalf. Start the run again to review the change.' : 'Kriya stopped here instead of deciding for you. This version of the app cannot take this kind of decision: it must be performed outside the Kriya GUI (nothing was approved on your behalf).'} Source: {run.outcome!.source}.</div>
        </div>
      ) : null}
      {finished && run.outcome!.recordedError && run.outcome!.recordedError !== run.outcome!.detail && run.outcome!.kind !== 'success' && run.outcome!.kind !== 'approval_required' ? <div role="note"><strong>Kriya reported:</strong> <span className="mono small">{run.outcome!.recordedError}</span></div> : null}
      {finished && !run.outcome!.approval ? <div>{run.outcome!.detail} <span className="muted small">Source: {run.outcome!.source}.</span></div> : null}
      {finished && run.outcome!.kind !== 'success' ? <div><CopyText what="failure details" label="Copy details" text={[`Run ${run.launchId}: ${run.outcome!.label}`, run.outcome!.detail, run.outcome!.recordedError ? `Kriya reported: ${run.outcome!.recordedError}` : null, `Source: ${run.outcome!.source}`].filter(Boolean).join('\n')} /></div> : null}
      {finished && run.outcome!.declined.length ? <div className="muted small">Decisions declined, nothing approved (the run proceeded without them): {run.outcome!.declined.map((d) => `${d.kind}: ${d.reason}`).join('; ')}</div> : null}
      {run.stage === 'starting' && !finished ? <div className="muted">Waiting for Kriya to record the launch…</div> : null}
      {run.status ? (
        <div className="muted small">
          Observed in Kriya&apos;s records ({lines} evidence record{lines === 1 ? '' : 's'}): {counts.map(([label, n]) => `${label} ${n}`).join(' · ')}
          {last ? ` · latest: ${last.kind}${last.unit ? ` (${last.unit})` : ''}` : ''}
          {' · '}liveness {run.status.liveness} <span title={run.status.liveness_note}>(DERIVED)</span>
        </div>
      ) : null}
      {run.progress.error ? <div className="muted small" role="note">Live records could not be read: {run.progress.error}</div> : null}
      {run.statusError ? <div className="muted small" role="alert">Launch status unreadable: {run.statusError.code}: {run.statusError.message}</div> : null}
      {run.host && run.host.phase === 'exited' && !finished ? <div className="muted small">The execution process has ended; reading Kriya&apos;s final records…</div> : null}
    </section>
  );
}
