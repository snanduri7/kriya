import type { ApplicationOutcome } from '../../model/kup';
import type { TimelineEntry, TrustItem, VerificationLine } from './presentation';

const MARK = { passed: '✓', failed: '✕', unknown: '•' } as const;
const TRUST_MARK = { ok: '✓', bad: '✕', unknown: '•' } as const;

/** The verification lines of a run in meaning (Build passed, 53 / 53 tests passed, ...). Mechanism names are not here: they live under Technical verification. */
export function VerificationLines({ lines, label = 'Verification' }: { lines: readonly VerificationLine[]; label?: string }) {
  return <ul className="ck-verify" aria-label={label}>{lines.map((l) => <li key={l.text} data-status={l.status}><span aria-hidden="true">{MARK[l.status]}</span> {l.text}</li>)}</ul>;
}

/** What Kriya recorded about EXECUTING the application: only an EXECUTED record renders, and `succeeded` alone says "ran successfully". */
export function ApplicationResult({ app }: { app: ApplicationOutcome }) {
  if (app.status !== 'EXECUTED') return null;
  const ok = app.succeeded === true;
  const exitLine = ok ? null : app.timed_out ? 'It did not finish in time' : app.exit_code !== null && app.exit_code !== undefined ? `Exit code ${app.exit_code}` : null;
  return (
    <section aria-label="Application" data-testid="application-outcome">
      <h3>Application</h3>
      <p data-status={ok ? 'passed' : 'failed'}>{ok ? '✓ Ran successfully' : '✕ Runtime verification failed'}</p>
      {exitLine ? <div data-testid="application-exit">{exitLine}</div> : null}
      <div className="muted small" data-testid="application-runs-in">Runs in Kriya isolated worktree</div>
      <h4 className="ck-action-sub">{ok ? 'Output' : 'Output / error'}</h4>
      <pre className="mono ck-output" aria-label="Application output">{app.stdout?.text || app.stderr?.text || '(no output)'}</pre>
      {app.stdout?.truncated || app.stderr?.truncated ? <p className="muted small">The output is longer than can be shown here.</p> : null}
      {app.command ? <details className="ck-tech"><summary>Technical details</summary><div className="muted small">Command <span className="mono">{app.command}</span></div></details> : null}
    </section>
  );
}

/** The normal timeline is a handful of sentences; a very long run shows its first and last lines and says the rest is in the technical timeline (nothing is silently dropped). */
export const TIMELINE_SHOWN = 30;
export function HumanTimeline({ entries }: { entries: readonly TimelineEntry[] }) {
  if (entries.length === 0) return <p className="muted">Kriya recorded no events for this run.</p>;
  const half = TIMELINE_SHOWN / 2;
  const hidden = Math.max(0, entries.length - TIMELINE_SHOWN);
  const shown = hidden === 0 ? entries : [...entries.slice(0, half), ...entries.slice(-half)];
  return (
    <>
      <ol className="ck-human-timeline" aria-label="What happened">{shown.map((e, i) => <li key={i} data-kind={e.kind}><span className="mono small muted">{e.time}</span> {e.text}</li>)}</ol>
      {hidden > 0 ? <p className="muted small" data-testid="timeline-more">{hidden} more event{hidden === 1 ? '' : 's'} are in the technical timeline (Details).</p> : null}
    </>
  );
}

/** The user layer of evidence: each line is a statement a recorded fact supports. */
export function TrustList({ items }: { items: readonly TrustItem[] }) {
  return (
    <dl className="ck-trust" aria-label="Why you can trust this result">
      {items.map((i, n) => (
        <div key={n} data-state={i.state}><dt className="muted small">{i.category}</dt><dd><span aria-hidden="true">{TRUST_MARK[i.state]}</span> {i.text}</dd></div>
      ))}
    </dl>
  );
}
