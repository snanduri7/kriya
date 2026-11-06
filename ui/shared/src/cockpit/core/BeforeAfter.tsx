import { changeSummary, type ChangedFile, type VerificationGroup } from './presentation';

export type RowStatus = 'passed' | 'failed' | 'unknown';
export interface SummaryRow { label: string; status: RowStatus; text: string }

const MARK: Record<RowStatus, string> = { passed: 'PASS', failed: 'FAIL', unknown: '-' };
const LINES_OF = (groups: readonly VerificationGroup[], id: VerificationGroup['id']) => groups.find((g) => g.id === id)?.lines ?? [];

/**
 * The customer-facing before/after summary, built ONLY from what the run recorded: the goal, the recorded change set, the recorded verification groups and the recorded attempts.
 * Nothing is inferred about meaning: a row with no recorded result says so ("not recorded"), the delivered line counts files and recorded line changes (never "features"), and
 * recovery is the number of attempts the evidence opened with the outcome of each.
 */
export function summaryRows(groups: readonly VerificationGroup[]): SummaryRow[] {
  const row = (label: string, id: VerificationGroup['id']): SummaryRow => {
    const lines = LINES_OF(groups, id);
    if (lines.length === 0) return { label, status: 'unknown', text: 'not recorded' };
    const failed = lines.some((l) => l.status === 'failed');
    const allPassed = lines.every((l) => l.status === 'passed');
    return { label, status: failed ? 'failed' : allPassed ? 'passed' : 'unknown', text: lines.map((l) => l.text).join(' · ') };
  };
  return [row('Build', 'build'), row('Tests', 'tests'), row('Runtime', 'runtime'), row('Safety checks', 'safety')];
}

/** "1 attempt" / "2 attempts (#1 failed, #2 succeeded)": from the recorded attempts only; null when the evidence recorded none. */
export function recoveryText(concluded: readonly boolean[], opened: number): string | null {
  const n = Math.max(opened, concluded.length);
  if (n === 0) return null;
  if (n === 1) return `1 attempt${concluded.length === 1 ? (concluded[0] ? ' (succeeded)' : ' (failed)') : ''}`;
  return `${n} attempts (${concluded.map((ok, i) => `#${i + 1} ${ok ? 'succeeded' : 'failed'}`).join(', ') || 'outcomes not recorded'})`;
}

export function BeforeAfter({ request, files, groups, concluded, opened }: {
  request: string; files: readonly ChangedFile[] | null; groups: readonly VerificationGroup[] | null; concluded: readonly boolean[]; opened: number;
}) {
  const added = files ? files.reduce((n, f) => n + (f.additions ?? 0), 0) : null;
  const removed = files ? files.reduce((n, f) => n + (f.deletions ?? 0), 0) : null;
  const recoveryLine = recoveryText(concluded, opened);
  return (
    <section className="ck-before-after" aria-label="Request and delivery" data-testid="before-after">
      <dl>
        <dt>Request</dt>
        <dd>{request}</dd>
        <dt>Delivered</dt>
        <dd>{files === null ? 'The change set is not readable from this run\'s evidence.' : `${changeSummary(files)}${files.length && added !== null && removed !== null && files.every((f) => f.additions !== null && f.deletions !== null) ? ` (+${added} −${removed} lines)` : ''}`}</dd>
        {groups === null ? null : summaryRows(groups).map((r) => (
          <div key={r.label} data-row={r.label} data-status={r.status} className="ck-ba-row"><dt>{r.label}</dt><dd><strong>{MARK[r.status]}</strong> <span className="muted">{r.text}</span></dd></div>
        ))}
        <dt>Recovery</dt>
        <dd data-row="Recovery">{recoveryLine ?? 'not recorded'}</dd>
      </dl>
    </section>
  );
}
