import { Badge } from '../../core';
import type { ExecutionPreflight, SecurityDemonstration } from '../../../model/kup';

type HealthRow = NonNullable<ExecutionPreflight['health']>[number];
type Tone = 'ok' | 'warn' | 'bad' | 'info' | 'neutral';

/** BLOCKED is the protection working (a refusal), so it is shown as a positive, protective state; ALLOWED is the in-scope control; REDACTED is the masking done. */
const TONE: Record<string, Tone> = { BLOCKED: 'ok', ALLOWED: 'info', REDACTED: 'ok' };
const ROW_TONE: Record<string, Tone> = { AVAILABLE: 'ok', DEGRADED: 'warn', UNAVAILABLE: 'bad', NOT_REQUIRED: 'neutral', DISABLED: 'neutral', UNKNOWN: 'neutral' };
const ROW_WORDS: Record<string, string> = { AVAILABLE: 'In force', DEGRADED: 'Degraded', UNAVAILABLE: 'Unavailable', NOT_REQUIRED: 'Not required', DISABLED: 'Disabled', UNKNOWN: 'Unknown' };
const PROTECTION_ROWS = ['security', 'private_state'];

/**
 * SECURITY, as a product feature: what protects the repository, and proof that it holds. The renderer states nothing of its own: the protection rows are Kriya's `health` rows,
 * and every refusal below is the canonical owner's own decision - its outcome, typed reason code and rule - about a situation Kriya built in a disposable folder (so it is a
 * demonstration of the real protections, never recorded data). No path, no file content and no secret is carried; a redaction row reports only lengths.
 */
export function SecurityCard({ rows, demonstrations }: { rows: readonly HealthRow[]; demonstrations: readonly SecurityDemonstration[] }) {
  const protections = rows.filter((r) => PROTECTION_ROWS.includes(r.id));
  const refused = demonstrations.filter((d) => d.outcome === 'BLOCKED').length;
  return (
    <section className="ck-security" aria-label="Security" data-testid="security-card">
      <h2>Security</h2>
      {protections.length ? (
        <ul className="ck-security-protections" aria-label="Protections in force">
          {protections.map((r) => (
            <li key={r.id} data-protection={r.id} title={`${r.reason_code}; evidence: ${r.evidence.join(', ') || 'none'}`}>
              <Badge tone={ROW_TONE[r.state] ?? 'neutral'}>{ROW_WORDS[r.state] ?? r.state}</Badge> <strong>{r.label}</strong> <span className="muted small">{r.detail}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {demonstrations.length ? (
        <>
          <h3>What Kriya refuses{refused ? ` · ${refused} of ${demonstrations.length} checks blocked` : ''}</h3>
          <p className="muted small">Checked just now by asking Kriya&apos;s real file protections about situations built in a disposable folder. Nothing in your project was read or changed, and no secret is shown.</p>
          <ul className="ck-security-demos" aria-label="Verified refusals">
            {demonstrations.map((d) => (
              <li key={d.id} data-demo={d.id} data-outcome={d.outcome}>
                <Badge tone={TONE[d.outcome] ?? 'neutral'}>{d.outcome}</Badge> <strong>{d.requested}</strong>
                <div className="muted small">{d.protection} · <span className="mono">{d.reason_code}</span> · rule <span className="mono">{d.authority}</span></div>
                <div className="small">{d.explanation}</div>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </section>
  );
}
