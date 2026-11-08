import type { RequirementTruth, RequirementTruthWords } from './presentation';

/** The backend's requirement truth as one quiet block: the counts, then the requirements that are not verified. Shown exactly; nothing here computes or infers a status. */
export function RequirementTruthBlock({ truth, words }: { truth: RequirementTruth | null; words: RequirementTruthWords }) {
  if (truth === null || (words.summary === null && truth.unresolved.length === 0)) return null;
  return (
    <section className="ck-truth" data-testid="requirement-truth" data-status={truth.status} aria-label="Requirement verification">
      {words.summary ? <p data-testid="requirement-truth-summary"><strong>{words.summary}</strong></p> : null}
      {truth.unresolved.length ? <ul className="ck-truth-open" aria-label="Unverified requirements">{truth.unresolved.map((u) => <li key={u.id} data-testid="requirement-unresolved"><span className="mono small">{u.id}</span> {u.text}{u.outcome === 'violated' ? <span className="muted small"> · contradicted by evidence</span> : null}</li>)}</ul> : null}
    </section>
  );
}
