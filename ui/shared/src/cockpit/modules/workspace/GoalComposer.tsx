import { Badge, useExecution } from '../../core';
import { BLOCKER_FIX, type ReadinessSummary } from './Readiness';

export const GOAL_MAX = 16000;
const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
export const START_SHORTCUT = MAC ? '⌘ Enter' : 'Ctrl Enter';

/** The one line under the goal box when everything needed is in place: ready, the model, how many Skills and tools Kriya can use. Nothing else competes with Start Run. */
function ContextLine({ summary, onDetails }: { summary: ReadinessSummary; onDetails(): void }) {
  const bits = [summary.model, summary.skills === null ? null : `${summary.skills} skill${summary.skills === 1 ? '' : 's'}`, summary.tools === null ? null : `${summary.tools} tool${summary.tools === 1 ? '' : 's'}`].filter(Boolean);
  return (
    <span className="ck-contextline">
      {summary.state === 'checking' ? <span className="muted" role="status">Checking this project…</span> : <Badge tone={summary.state === 'ready' ? 'ok' : 'warn'}>{summary.state === 'ready' ? '✓ Ready' : '✓ Ready, with a warning'}</Badge>}
      {bits.length ? <span className="muted"> · {bits.join(' · ')}</span> : null}{' '}
      <button type="button" className="ck-link" onClick={onDetails}>View readiness details</button>
    </span>
  );
}

/** Why Start Run is off, in plain words; null when it is on. */
export function startDisabledReason(summary: ReadinessSummary, empty: boolean, tooLong: boolean): string | null {
  if (summary.state === 'unavailable') return 'This host cannot start a run.';
  if (summary.state === 'blocked' || summary.state === 'error') return 'Kriya cannot start a run here yet (see above).';
  if (summary.state === 'checking') return 'Checking this project…';
  if (tooLong) return `The goal is longer than ${GOAL_MAX} characters.`;
  if (empty) return 'Enter a goal to start.';
  return null;
}

/** Goal entry and Start Run: the visual centre of the Project page. The readiness detail lives under "Project details"; only a blocker is allowed above the button. */
export function GoalComposer({ summary, onDetails, onRecheck }: { summary: ReadinessSummary; onDetails(): void; onRecheck(): void }) {
  const ex = useExecution();
  const empty = ex.goal.trim().length === 0;
  const tooLong = ex.goal.length > GOAL_MAX;
  const reason = startDisabledReason(summary, empty, tooLong);
  const canStart = reason === null && !ex.locked;
  const submit = () => { if (canStart && !ex.starting) void ex.start(); };
  return (
    <section className="ck-composer" aria-label="Start a run">
      <h2>What do you want Kriya to do?</h2>
      <label htmlFor="ck-goal" className="sr-only">Engineering goal</label>
      <textarea id="ck-goal" className="ck-goal" rows={4} value={ex.goal} maxLength={GOAL_MAX} disabled={ex.locked} aria-describedby="ck-goal-help"
        placeholder="Describe the engineering task, e.g. “Add a clamp(value, low, high) function with tests.”"
        onChange={(e) => ex.setGoal(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) { e.preventDefault(); submit(); } }} />
      {summary.state === 'blocked' || summary.state === 'error' ? (
        <div className="ck-state ck-blocker" role="alert">
          <strong>⚠ Cannot start a run</strong>
          <ul>{summary.blockers.map((b) => <li key={b.code}>{b.message} <span className="muted small">({b.code})</span>{BLOCKER_FIX[b.code] ? <div className="muted small">{BLOCKER_FIX[b.code]}</div> : null}</li>)}</ul>
          <button type="button" onClick={onRecheck}>Check again</button>{' '}
          <button type="button" className="ck-link" onClick={onDetails}>View configuration</button>
        </div>
      ) : null}
      {summary.warnings.length ? <div className="ck-state" role="note"><strong>⚠ A run can start, but</strong><ul>{summary.warnings.map((w) => <li key={w}>{w}</li>)}</ul><button type="button" className="ck-link" onClick={onDetails}>View readiness details</button></div> : null}
      {ex.refusal ? (
        <div className="ck-state" role="alert">
          <strong>The run was not started</strong>
          {ex.refusal.message} <span className="muted small">({ex.refusal.code})</span>
        </div>
      ) : null}
      <div className="ck-composer-foot">
        {summary.state === 'blocked' || summary.state === 'error' || summary.state === 'unavailable' ? <span /> : <ContextLine summary={summary} onDetails={onDetails} />}
        <span className="grow" />
        <span id="ck-goal-help" className="muted small">{ex.goal.length}/{GOAL_MAX}</span>
        <kbd className="ck-kbd" aria-hidden="true">{START_SHORTCUT}</kbd>
        <button type="button" className="ck-primary ck-cta ck-start" disabled={!canStart || ex.starting} aria-describedby="ck-start-why" onClick={submit}>{ex.starting ? 'Starting…' : 'Start Run'}</button>
      </div>
      <div id="ck-start-why" className="muted small" role="status">{reason && summary.state !== 'blocked' && summary.state !== 'error' ? reason : ''}</div>
    </section>
  );
}
