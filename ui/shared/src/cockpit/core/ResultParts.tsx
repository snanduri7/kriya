import type { ApplicationOutcome } from '../../model/kup';
import type { FormalRequirementStatus, TimelineEntry, TrustItem, VerificationLine } from './presentation';
import type { GateFact } from './runFacts';
import { gateName } from './runFacts';

const MARK = { passed: '✓', failed: '✕', unknown: '•' } as const;
const TRUST_MARK = { ok: '✓', bad: '✕', unknown: '•' } as const;

/** The verification lines of a run in meaning (Build passed, 53 / 53 tests passed, ...). Mechanism names are not here: they live under Technical verification. */
export function VerificationLines({ lines, label = 'Verification' }: { lines: readonly VerificationLine[]; label?: string }) {
  return <ul className="ck-verify" aria-label={label}>{lines.map((l) => <li key={l.text} data-status={l.status}><span aria-hidden="true">{MARK[l.status]}</span> {l.text}</li>)}</ul>;
}

/** How many lines of the captured output are shown before "Show full output". The output is Kriya's record of what the command printed - the program's own lines and the tool that launched it share one
 * stream, and nothing here parses it to pick "the" program line: it is shown as captured, bounded, with the whole text one click away. */
export const OUTPUT_PREVIEW_LINES = 15;
export function CapturedOutput({ text }: { text: string }) {
  const trimmed = text.replace(/\s+$/, '');
  if (trimmed === '') return <pre className="mono ck-output" aria-label="Application output">(no output)</pre>;
  const lines = trimmed.split('\n');
  if (lines.length <= OUTPUT_PREVIEW_LINES) return <pre className="mono ck-output" aria-label="Application output">{trimmed}</pre>;
  return (
    <>
      <pre className="mono ck-output" aria-label="Application output">{lines.slice(0, OUTPUT_PREVIEW_LINES).join('\n')}</pre>
      <details className="ck-tech" data-testid="full-output"><summary>Show full output ({lines.length} lines)</summary><pre className="mono ck-output" aria-label="Full application output">{trimmed}</pre></details>
    </>
  );
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
      <CapturedOutput text={app.stdout?.text || app.stderr?.text || ''} />
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

/** The formal requirement ledger, under its own disclosure: separate from the checks above it, never a peer warning beside Build / Tests / Application. */
export function FormalRequirementDisclosure({ status }: { status: FormalRequirementStatus | null }) {
  if (status === null) return null;
  return (
    <details className="ck-tech" data-testid="formal-requirements">
      <summary>Formal requirement verification</summary>
      <p><strong>{status.headline}</strong></p>
      <p className="muted small">{status.explanation}</p>
      {status.notes.length ? <VerificationLines lines={status.notes} label="Other recorded assessments" /> : null}
    </details>
  );
}

/** One row per recorded check for the technical layer: a readable name and the exact recorded type. A row with no type is never rendered, and the whole disclosure is omitted when no row is left. */
export function TechnicalVerification({ gates }: { gates: readonly GateFact[] }) {
  const rows = gates.filter((g) => g.type.trim() !== '');
  if (rows.length === 0) return null;
  return (
    <details className="ck-tech" data-testid="technical-verification">
      <summary>Technical verification</summary>
      <ul aria-label="Technical verification">{rows.map((g, i) => <li key={`${g.type}${i}`} className="mono small">{g.passed === true ? '✓' : g.passed === false ? '✕' : '•'} {gateName(g.type) === g.type ? g.type : `${gateName(g.type)} (${g.type})`}</li>)}</ul>
    </details>
  );
}
