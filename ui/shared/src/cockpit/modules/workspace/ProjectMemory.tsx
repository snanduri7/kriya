import { useState } from 'react';
import { Badge } from '../../core';
import type { ProjectMemory } from '../../../model/kup';

/** The reasons Kriya gives for a fact it no longer reuses, in its own closed vocabulary (a code Kriya adds later is shown as the code itself). */
const STALE_WORDS: Record<string, string> = {
  EVIDENCE_CHANGED: 'the file it was learned from has changed',
  EVIDENCE_MISSING: 'the file it was learned from is gone',
  EVIDENCE_UNREADABLE: 'the file it was learned from can no longer be read as evidence',
};
const STORE_WORDS: Record<string, string> = {
  STORE_UNREADABLE: 'The stored memory could not be read, so nothing is reused.',
  STORE_TOO_LARGE: 'The stored memory is larger than Kriya will read, so nothing is reused.',
  STORE_SCHEMA_UNSUPPORTED: 'The stored memory is from a different Kriya version, so nothing is reused.',
  STORE_FOREIGN_REPOSITORY: 'The stored memory belongs to another repository, so nothing is reused.',
};

/** The four counters, each with the one-sentence meaning shown on hover/focus (and read by assistive technology). */
const STATS: ReadonlyArray<{ key: 'reused_last_run' | 'learned_last_run' | 'stale' | 'rejected_last_run'; label: string; help: string }> = [
  { key: 'reused_last_run', label: 'Reused', help: 'Verified facts Kriya used from prior work.' },
  { key: 'learned_last_run', label: 'Learned', help: 'New verified facts saved from the last run.' },
  { key: 'stale', label: 'Stale', help: 'Previous facts whose evidence changed, so they are not reused.' },
  { key: 'rejected_last_run', label: 'Rejected', help: 'Candidate facts Kriya refused to trust or store.' },
];

const evidenceLine = (f: ProjectMemory['facts'][number]): string =>
  f.evidence.map((e) => (e.kind === 'file' ? e.path : `a ${e.path} file in the repository`)).join(', ') || 'the run\'s own gate evidence';

/**
 * VERIFIED PROJECT MEMORY: what Kriya has learned and verified about THIS repository, and what it reused. The renderer decides nothing: every fact, status, reason,
 * count and provenance sentence is a field of Kriya's `execution.preflight` answer (`memory`), a projection of verified run evidence re-checked against the files now.
 * "Last run" figures are the most recent observed run's own; a fact is CURRENT only while the file it came from still has the digest it was learned from.
 */
export function ProjectMemoryCard({ memory, onRecheck }: { memory: ProjectMemory; onRecheck?: () => void }) {
  const { counts } = memory;
  const usable = memory.store_state === 'OK' || memory.store_state === 'ABSENT';
  const [staleOnly, setStaleOnly] = useState(false);
  const stale = memory.facts.filter((f) => f.status === 'STALE');
  const allChanged = stale.length > 0 && stale.every((f) => f.stale_reason === 'EVIDENCE_CHANGED');
  const shown = staleOnly ? stale : memory.facts;
  return (
    <section className="ck-memory" aria-label="Verified Project Memory" data-testid="project-memory">
      <h2>Verified Project Memory</h2>
      <p className="muted small">Kriya learns safe, validated facts about this repository and reuses them in later runs. It is not model training: a fact is kept only after a run ended in verified success, and it is re-checked against the files before every use.</p>
      <dl data-testid="memory-counts" className="ck-memory-counts">
        {STATS.map((stat) => (
          <div key={stat.key} className="ck-stat" title={stat.help} data-stat={stat.key}>
            <dt>{stat.label}</dt>
            <dd>{counts[stat.key]}</dd>
            <span className="sr-only">{stat.help}</span>
          </div>
        ))}
      </dl>
      {stale.length > 0 ? (
        <div className="ck-state" role="status" data-testid="memory-stale-notice">
          <strong>{stale.length} project fact{stale.length === 1 ? '' : 's'} became stale because {allChanged ? 'their evidence changed' : 'their evidence changed or is no longer available'}.</strong>
          <div className="muted small">Kriya does not reuse a stale fact. It learns it again from the next verified run.</div>
          <button type="button" className="ck-quiet" onClick={() => setStaleOnly((v) => !v)}>{staleOnly ? 'Show all facts' : 'View stale facts'}</button>
          {onRecheck ? <> <button type="button" className="ck-quiet" onClick={onRecheck}>Re-check project memory</button></> : null}
        </div>
      ) : null}
      {!usable ? <p role="alert" className="muted"><Badge tone="warn">{memory.store_state}</Badge> {STORE_WORDS[memory.store_state] ?? 'The stored memory cannot be used.'}</p> : null}
      {usable && memory.facts.length === 0 ? <p className="muted">Nothing is remembered yet. After a run that ends in verified success, the facts it confirmed appear here.</p> : null}
      <ul className="ck-memory-facts">
        {shown.map((f) => (
          <li key={f.id} data-fact={f.id} data-status={f.status}>
            <div className="ck-fact-main">
              {f.status === 'CURRENT' ? <Badge tone="ok">✓ Current</Badge> : <Badge tone="warn">↻ Stale</Badge>}
              {' '}<strong>{f.label}</strong>: <span className="ck-fact-value">{f.value}</span>
            </div>
            <div className="muted small">Evidence: {evidenceLine(f)} · learned in run <span className="mono">{f.learned_run_id}</span>{f.reused_runs ? ` · reused in ${f.reused_runs} later run${f.reused_runs === 1 ? '' : 's'}` : ''}</div>
            {f.status === 'STALE' ? <div className="muted small" role="note">Not reused: {f.stale_reason ? (STALE_WORDS[f.stale_reason] ?? f.stale_reason) : 'unknown'} <span className="mono">({f.stale_reason})</span></div> : null}
            <details className="ck-setup ck-why-link"><summary>Why does Kriya know this?</summary><p className="small">{f.why}</p></details>
          </li>
        ))}
      </ul>
      {memory.rejected.length ? (
        <details className="ck-setup"><summary>Rejected · {memory.rejected.length}</summary>
          <ul className="small">{memory.rejected.slice(-10).map((r, i) => <li key={`${r.run_id}-${r.kind}-${i}`}><span className="mono">{r.kind}</span> from run <span className="mono">{r.run_id}</span>: {r.reason === 'RUN_NOT_VERIFIED' ? 'the run did not end in verified success, so nothing was learned' : r.reason === 'UNSAFE_VALUE' ? 'the value looked like it could carry a secret, so it was not stored' : r.reason}</li>)}</ul>
        </details>
      ) : null}
    </section>
  );
}
