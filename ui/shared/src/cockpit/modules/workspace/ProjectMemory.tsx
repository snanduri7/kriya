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

const evidenceLine = (f: ProjectMemory['facts'][number]): string =>
  f.evidence.map((e) => (e.kind === 'file' ? e.path : `a ${e.path} file in the repository`)).join(', ') || 'the run\'s own gate evidence';

/**
 * VERIFIED PROJECT MEMORY: what Kriya has learned and verified about THIS repository, and what it reused. The renderer decides nothing: every fact, status, reason,
 * count and provenance sentence is a field of Kriya's `execution.preflight` answer (`memory`), a projection of verified run evidence re-checked against the files now.
 * "Last run" figures are the most recent observed run's own; a fact is CURRENT only while the file it came from still has the digest it was learned from.
 */
export function ProjectMemoryCard({ memory }: { memory: ProjectMemory }) {
  const { counts } = memory;
  const usable = memory.store_state === 'OK' || memory.store_state === 'ABSENT';
  return (
    <section className="ck-memory" aria-label="Verified Project Memory" data-testid="project-memory">
      <h2>Verified Project Memory</h2>
      <p className="muted small">Kriya learns safe, validated facts about this repository and reuses them in later runs. It is not model training: a fact is kept only after a run ended in verified success, and it is re-checked against the files before every use.</p>
      <p data-testid="memory-counts" className="ck-memory-counts">
        <span>Reused last run <strong>{counts.reused_last_run}</strong></span>
        {' · '}<span>Learned last run <strong>{counts.learned_last_run}</strong></span>
        {' · '}<span>Stale <strong>{counts.stale}</strong></span>
        {' · '}<span>Rejected <strong>{counts.rejected_last_run}</strong></span>
      </p>
      {!usable ? <p role="alert" className="muted"><Badge tone="warn">{memory.store_state}</Badge> {STORE_WORDS[memory.store_state] ?? 'The stored memory cannot be used.'}</p> : null}
      {usable && memory.facts.length === 0 ? <p className="muted">Nothing is remembered yet. After a run that ends in verified success, the facts it confirmed appear here.</p> : null}
      <ul className="ck-memory-facts">
        {memory.facts.map((f) => (
          <li key={f.id} data-fact={f.id} data-status={f.status}>
            {f.status === 'CURRENT' ? <Badge tone="ok">✓ Current</Badge> : <Badge tone="warn">↻ Stale</Badge>}
            {' '}<strong>{f.label}</strong>: {f.value}
            <span className="muted small"> · evidence {evidenceLine(f)} · learned in run <span className="mono">{f.learned_run_id}</span>{f.reused_runs ? ` · reused in ${f.reused_runs} later run${f.reused_runs === 1 ? '' : 's'}` : ''}</span>
            {f.status === 'STALE' ? <span className="muted small" role="note"> · not reused: {f.stale_reason ? (STALE_WORDS[f.stale_reason] ?? f.stale_reason) : 'unknown'} <span className="mono">({f.stale_reason})</span></span> : null}
            <details className="ck-setup"><summary>Why does Kriya know this?</summary><p className="small">{f.why}</p></details>
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
