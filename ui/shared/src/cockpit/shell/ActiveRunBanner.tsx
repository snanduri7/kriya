import { useCallback, useEffect } from 'react';
import { PROJECT_PAGE, runPhase, useDispatch, useElapsed, useExecution, useNavigate, useProvider } from '../core';

const GOAL_SHOWN = 56;
const short = (goal: string): string => { const one = goal.trim().replace(/\s+/g, ' '); return one.length > GOAL_SHOWN ? `${one.slice(0, GOAL_SHOWN - 1)}…` : one; };

/**
 * The compact strip shown on every page except the Project page (where the run IS the page): the run this window started, what Kriya is doing, and one button back to it.
 * When Kriya needs the person, the strip changes to ACTION REQUIRED and outranks everything else on the page. It never holds the approval controls: those live on the
 * Project page's current run, so there is exactly one place to decide. Every word comes from runPhase (Kriya's records) or the derived outcome.
 */
export function ActiveRunBanner() {
  const ex = useExecution();
  const dispatch = useDispatch();
  const go = useNavigate();
  const desktop = useProvider().desktop;
  const run = ex.run;
  // Back to the run this window started: select it (live while running, its recorded row once finished), open the Project page, and put keyboard focus on the
  // pending review when there is one. Used by "Return to Run", "Review" and a clicked notification.
  const showRun = useCallback(() => {
    if (!run) return;
    if (run.stage !== 'finished') dispatch({ type: 'selectLiveRun', launchId: run.launchId });
    else if (run.outcome?.traceRunId) dispatch({ type: 'selectRun', runId: run.outcome.traceRunId });
    go(PROJECT_PAGE);
    if (run.stage !== 'finished' && (run.review || run.runtimeReview)) {
      // the page may still be rendering: try for a short while until the review section exists
      let tries = 0;
      const focus = () => { const el = document.querySelector<HTMLElement>('section[aria-label="Change review"], section[aria-label="Runtime command review"]'); if (el) el.focus(); else if (tries++ < 10) setTimeout(focus, 50); };
      setTimeout(focus, 0);
    }
  }, [run, dispatch, go]);
  // A notification click arrives as the fixed command `showRun` (no payload): the current run is the only run there is.
  useEffect(() => (desktop ? desktop.onMenuCommand((command) => { if (command === 'showRun') showRun(); }) : undefined), [desktop, showRun]);
  const elapsed = useElapsed(run?.status?.launch.requested_at ?? null, run !== null && run.stage !== 'finished', run?.status?.launch.ended?.at ?? null);
  if (!run) return null;
  const phase = runPhase(run);
  const goal = ex.goal.trim() ? short(ex.goal) : null;
  const mark = phase.terminal ? (phase.state === 'COMPLETED' ? '✓' : phase.state === 'STOPPED' ? '■' : '✕') : phase.needsAction ? '⚠' : '●';
  const word = phase.terminal ? (phase.state === 'COMPLETED' ? 'Run completed' : phase.state === 'STOPPED' ? 'Run stopped' : 'Run failed') : 'Run active';
  return (
    <section className={`ck-runbar ck-state${phase.needsAction ? ' ck-attention' : ''}`} role="status" aria-live="polite" aria-label="Active run" data-stage={run.stage} data-outcome={run.outcome?.kind ?? ''} data-state={phase.state} data-timing={JSON.stringify(run.timing)}>
      <div className="ck-runbar-head">
        {phase.needsAction ? (
          <>
            <span className="ck-action-flag">ACTION REQUIRED</span>
            <strong>Change approval waiting</strong>
          </>
        ) : (
          <>
            <span className={`ck-dot ${phase.tone}`} aria-hidden="true">{mark}</span>
            <strong>{word}</strong>
            {!phase.terminal ? <span>· {phase.label}</span> : null}
          </>
        )}
        {goal ? <span className="muted"> · {goal}</span> : null}
        {elapsed && !phase.terminal ? <span className="muted small"> · {elapsed}</span> : null}
        <span className="grow" />
        {phase.needsAction ? <button type="button" className="ck-primary ck-cta" onClick={showRun}>Review</button> : null}
        {!phase.needsAction && !phase.terminal ? <button type="button" onClick={showRun}>Return to Run</button> : null}
        {phase.terminal ? (
          <>
            <button type="button" onClick={showRun}>View Result</button>
            <button type="button" onClick={ex.dismiss}>Dismiss</button>
          </>
        ) : null}
      </div>
    </section>
  );
}
