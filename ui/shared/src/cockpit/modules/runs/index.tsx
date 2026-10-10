import { useEffect, useMemo, useState } from 'react';
import { AvailabilityView, useDispatch, useModuleSelection, useProvider, useSession, type ModuleDefinition } from '../../core';
import type { RunSummary } from '../../../model/kup';

const PAGE = 200;

const statusLabel = (status: string | null | undefined) => (status === 'in_progress' ? 'Unfinished' : status ?? 'status not recorded');

interface Loaded { key: string; runs: RunSummary[]; next: string | null; error: string | null; busy: boolean }

function RunsList() {
  const provider = useProvider();
  const session = useSession();
  const selected = useModuleSelection('runId');
  const dispatch = useDispatch();
  const snapshotId = session.snapshotId;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const current = loaded && loaded.key === snapshotId ? loaded : null;

  useEffect(() => {
    if (!snapshotId) return undefined;
    const ac = new AbortController();
    void provider.listRuns(snapshotId, { limit: PAGE }, ac.signal).then((r) => {
      if (ac.signal.aborted) return;
      setLoaded(r.ok ? { key: snapshotId, runs: r.data.runs, next: r.data.next_cursor ?? null, error: null, busy: false } : { key: snapshotId, runs: [], next: null, error: `${r.error.code}: ${r.error.message}`, busy: false });
    });
    return () => ac.abort();
  }, [provider, snapshotId]);

  const more = () => {
    if (!snapshotId || !current?.next) return;
    const { next } = current;
    setLoaded({ ...current, busy: true });
    void provider.listRuns(snapshotId, { limit: PAGE, cursor: next }).then((r) => {
      setLoaded((prev) => (prev && prev.key === snapshotId ? (r.ok ? { ...prev, runs: [...prev.runs, ...r.data.runs], next: r.data.next_cursor ?? null, busy: false, error: null } : { ...prev, busy: false, error: `${r.error.code}: ${r.error.message}` }) : prev));
    });
  };

  const groups = useMemo(() => {
    const byGroup = new Map<string, RunSummary[]>();
    for (const run of current?.runs ?? []) {
      const key = run.milestone_group_id ? `Milestone ${run.milestone_group_id}` : `${(run.timestamp ?? 'time not recorded').slice(0, 10)}`;
      byGroup.set(key, [...(byGroup.get(key) ?? []), run]);
    }
    return [...byGroup.entries()];
  }, [current]);

  return (
    <div>
      <h2 style={{ fontSize: 14, margin: '0 0 6px' }}>Runs</h2>
      {snapshotId === null ? <AvailabilityView value={session.status}>{() => null}</AvailabilityView> : null}
      {session.status.state !== 'ready' && session.status.state !== 'loading' ? <button type="button" onClick={() => void session.acquire()}>Acquire snapshot</button> : null}
      {snapshotId !== null && current === null ? <div className="ck-state muted" role="status">Loading…</div> : null}
      {current?.error ? <div className="ck-state" role="alert"><strong>Runs could not be read</strong>{current.error}</div> : null}
      {groups.map(([group, runs]) => (
        <div key={group}>
          <div className="ck-group">{group}</div>
          <ul className="ck-list">
            {runs.map((run) => (
              <li key={run.run_id}>
                <button type="button" aria-selected={run.run_id === selected} onClick={() => dispatch({ type: 'selectRun', runId: run.run_id })}>
                  <div>{run.goal ?? run.run_id}</div>
                  <div className="muted small">{statusLabel(run.status)} · {run.attempts ?? '?'} attempt(s){run.duration_sec != null ? ` · ${Math.round(run.duration_sec * 10) / 10}s` : ''}</div>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {current ? <div className="muted small" role="status">{current.runs.length} run(s) loaded{current.next ? '' : ' (all)'}</div> : null}
      {current?.next ? <button type="button" disabled={current.busy} onClick={more}>{current.busy ? 'Loading…' : 'Load more runs'}</button> : null}
    </div>
  );
}

export const runsModule: ModuleDefinition = {
  id: 'runs',
  placement: { page: 'runs', surface: 'secondary', label: 'Runs', order: 10 },
  requiredCapabilities: ['snapshot'], optionalCapabilities: [], selectionDependencies: ['runId'],
  render: RunsList,
};
