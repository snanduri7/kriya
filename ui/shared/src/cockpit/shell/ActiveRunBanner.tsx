import { useEffect, useState } from 'react';
import { Badge, useDispatch, useExecution, type RunView } from '../core';

const STAGE_LABEL: Record<RunView['stage'], string> = { starting: 'Starting', running: 'Running', finished: 'Finished' };
const COUNTS: Array<[string, string]> = [['unit.opened', 'Units opened'], ['attempt.opened', 'Attempts opened'], ['model.request', 'Model requests'], ['gate.result', 'Gate results']];

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

const TONE = { success: 'ok', failure: 'bad', needs_review: 'warn', refused: 'bad', interrupted: 'warn', not_recorded: 'warn', approval_required: 'warn' } as const;
const APPROVAL_KIND: Record<string, string> = {
  change_approval: 'a proposed change needs approval', knowledge_gap_acknowledgement: 'a knowledge gap needs acknowledgement', skill_gap_decision: 'a skill gap needs a decision',
  skill_conflict_decision: 'two skills may conflict', web_lookup_review: 'live-lookup references need review', web_lookup_query: 'a live-lookup search needs approval',
};
const detailLines = (d: Record<string, unknown>): string[] => Object.entries(d).flatMap(([k, v]) => (Array.isArray(v) && v.length ? [`${k}: ${v.map(String).join(', ')}`] : []));

/** The one run this window started, across every page: what Kriya's records say now, and the durable outcome once there is one. */
export function ActiveRunBanner() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const run = ex.run;
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished');
  if (!run) return null;
  const finished = run.stage === 'finished' && run.outcome !== null;
  const counts = COUNTS.map(([kind, label]) => [label, run.progress.kinds[kind] ?? 0] as const);
  const lines = run.progress.lastSeq;
  const last = run.progress.recent[run.progress.recent.length - 1];
  return (
    <section className="ck-runbar ck-state" role="status" aria-live="polite" aria-label="Active run" data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-runbar-head">
        <strong>{finished ? run.outcome!.label : `Run ${STAGE_LABEL[run.stage].toLowerCase()}`}</strong>
        <span className="muted mono small" title="The launch id is the run id">{run.launchId}</span>
        {!finished ? <Badge tone="info">{STAGE_LABEL[run.stage]}</Badge> : <Badge tone={TONE[run.outcome!.kind]}>{run.outcome!.kind.replace('_', ' ')}</Badge>}
        {elapsed ? <span className="muted small" title="Local clock since the launch was recorded - presentation only">elapsed {elapsed}</span> : null}
        <span className="grow" />
        {finished ? (
          <>
            {run.outcome!.traceRunId ? <button type="button" onClick={() => dispatch({ type: 'selectRun', runId: run.outcome!.traceRunId })}>View run</button> : null}
            <button type="button" onClick={ex.dismiss}>Dismiss</button>
          </>
        ) : null}
      </div>
      {finished && run.outcome!.approval ? (
        <div>
          <div><strong>{APPROVAL_KIND[run.outcome!.approval.kind] ?? run.outcome!.approval.kind}.</strong> {run.outcome!.approval.reason}</div>
          {detailLines(run.outcome!.approval.details).map((l) => <div key={l} className="muted small mono">{l}</div>)}
          <div className="muted small">Kriya stopped here instead of deciding for you. This version of the app cannot approve it: the approval must currently be performed outside the Kriya GUI (nothing was approved on your behalf). Source: {run.outcome!.source}.</div>
        </div>
      ) : null}
      {finished && !run.outcome!.approval ? <div>{run.outcome!.detail} <span className="muted small">Source: {run.outcome!.source}.</span></div> : null}
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
