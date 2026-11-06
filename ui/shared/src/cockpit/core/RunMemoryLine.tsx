import type { ProjectMemory } from '../../model/kup';

/**
 * What Verified Project Memory did for ONE recorded run: the entry Kriya recorded when that run finished (reused / learned / rejected), with the names of the facts it carried.
 * Nothing is shown for a run Kriya recorded no memory entry for (it ran before memory existed, or memory was unreadable) - absence is never presented as "reused nothing".
 */
export function RunMemoryLine({ memory, runId }: { memory: ProjectMemory | undefined; runId: string }) {
  const entry = memory?.runs.find((r) => r.run_id === runId);
  if (!memory || !entry) return null;
  const names = (ids: readonly string[]): string => ids.map((id) => memory.facts.find((f) => f.id === id)?.label ?? id).join(', ');
  const learned = [...entry.learned, ...entry.refreshed];
  return (
    <>
      <h3>Verified Project Memory</h3>
      <p data-testid="run-memory" className="ck-run-memory">
        {entry.reused.length ? <>Reused <strong>{entry.reused.length}</strong> verified fact{entry.reused.length === 1 ? '' : 's'} from earlier runs ({names(entry.reused)}). </> : <>Reused <strong>0</strong> facts. </>}
        {entry.outcome === 'learned' ? <>Learned or confirmed <strong>{learned.length}</strong> ({names(learned)}). </> : <>Learned <strong>0</strong>: this run did not end in verified success, so nothing was kept. </>}
        {entry.rejected ? <>Rejected <strong>{entry.rejected}</strong>.</> : null}
      </p>
    </>
  );
}
