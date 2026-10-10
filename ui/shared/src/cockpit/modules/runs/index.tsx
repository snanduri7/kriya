import { useMemo } from 'react';
import { AvailabilityView, hasData, loading, toAvailability, useAsync, useDispatch, useModuleSelection, useProvider, useSession, type ModuleDefinition } from '../../core';
import type { HistoryList, RunSummary } from '../../../model/kup';

const statusLabel = (status: string | null | undefined) => (status === 'in_progress' ? 'Unfinished' : status ?? 'status not recorded');

function RunsList() {
  const provider = useProvider();
  const session = useSession();
  const selected = useModuleSelection('runId');
  const dispatch = useDispatch();
  const list = useAsync<HistoryList>(session.snapshotId ? `runs:${session.snapshotId}` : null, async (signal) => toAvailability(await provider.listRuns(session.snapshotId!, { limit: 200 }, signal)), loading());

  const groups = useMemo(() => {
    if (!hasData(list)) return [];
    const byGroup = new Map<string, RunSummary[]>();
    for (const run of list.data.runs) {
      const key = run.milestone_group_id ? `Milestone ${run.milestone_group_id}` : `${(run.timestamp ?? 'time not recorded').slice(0, 10)}`;
      byGroup.set(key, [...(byGroup.get(key) ?? []), run]);
    }
    return [...byGroup.entries()];
  }, [list]);

  return (
    <div>
      <h2 style={{ fontSize: 14, margin: '0 0 6px' }}>Runs</h2>
      {session.snapshotId === null ? (
        <AvailabilityView value={session.status}>{() => null}</AvailabilityView>
      ) : null}
      {session.status.state !== 'ready' && session.status.state !== 'loading' ? <button type="button" onClick={() => void session.acquire()}>Acquire snapshot</button> : null}
      {session.snapshotId !== null ? (
        <AvailabilityView value={list}>
          {() => groups.map(([group, runs]) => (
            <div key={group}>
              <div className="ck-group">{group}</div>
              <ul className="ck-list">
                {runs.map((run) => (
                  <li key={run.run_id}>
                    <button type="button" aria-selected={run.run_id === selected} onClick={() => dispatch({ type: 'selectRun', runId: run.run_id })}>
                      <div>{run.goal ?? run.run_id}</div>
                      <div className="muted small">{statusLabel(run.status)} · {run.attempts ?? '?'} attempt(s){run.duration_sec != null ? ` · ${run.duration_sec}s` : ''}</div>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </AvailabilityView>
      ) : null}
    </div>
  );
}

export const runsModule: ModuleDefinition = {
  id: 'runs',
  placement: { page: 'runs', surface: 'secondary', label: 'Runs', order: 10 },
  requiredCapabilities: ['snapshot'], optionalCapabilities: [], selectionDependencies: ['runId'],
  render: RunsList,
};
