import { useEffect, useMemo, useState } from 'react';
import { AvailabilityView, useDispatch, useNavigate, useModuleSelection, useProvider, useSession, useWorkspacePath, type ModuleDefinition } from '../../core';
import type { HistoryList, RunSummary } from '../../../model/kup';

const PAGE = 200;

const mergeScope = (a: Scope | null, b: Scope | null): Scope | null => (a && b ? { ...b, matched: a.matched + b.matched, other: a.other + b.other, unattributed: a.unattributed + b.unattributed, scanned: a.scanned + b.scanned } : b ?? a);

/** What Kriya set aside, in its own terms: attributed to another workspace, or carrying no recorded workspace at all (never guessed either way). */
function scopeNote(scope: Scope): string {
  const parts = [`${scope.other} run(s) in this state store belong to other workspaces`, `${scope.unattributed} have no recorded workspace and are not listed`];
  return `${parts.join('; ')}.${scope.truncated ? ' The scan stopped before the end of history; load more to continue.' : ''}`;
}

const statusLabel = (status: string | null | undefined) => (status === 'in_progress' ? 'Unfinished' : status ?? 'status not recorded');

type Scope = NonNullable<HistoryList['workspace_scope']>;
interface Loaded { key: string; runs: RunSummary[]; next: string | null; error: string | null; busy: boolean; scope: Scope | null }

function RunsList() {
  const provider = useProvider();
  const session = useSession();
  const selected = useModuleSelection('runId');
  const dispatch = useDispatch();
  const snapshotId = session.snapshotId;
  const go = useNavigate();
  // The list is the open workspace's runs: Kriya scopes it by what each run's evidence manifest records; Runs only reacts to the workspace.
  const workspace = useWorkspacePath();
  const key = snapshotId ? `${snapshotId}|${workspace ?? ''}` : null;
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [query, setQuery] = useState('');
  const current = loaded && loaded.key === key ? loaded : null;

  useEffect(() => {
    if (!snapshotId || key === null) return undefined;
    const ac = new AbortController();
    void provider.listRuns(snapshotId, { limit: PAGE, ...(workspace ? { workspace } : {}) }, ac.signal).then((r) => {
      if (ac.signal.aborted) return;
      setLoaded(r.ok ? { key, runs: r.data.runs, next: r.data.next_cursor ?? null, error: null, busy: false, scope: r.data.workspace_scope ?? null } : { key, runs: [], next: null, error: `${r.error.code}: ${r.error.message}`, busy: false, scope: null });
    });
    return () => ac.abort();
  }, [provider, snapshotId, workspace, key]);

  const more = () => {
    if (!snapshotId || !current?.next) return;
    const { next } = current;
    setLoaded({ ...current, busy: true });
    void provider.listRuns(snapshotId, { limit: PAGE, cursor: next, ...(workspace ? { workspace } : {}) }).then((r) => {
      setLoaded((prev) => (prev && prev.key === key ? (r.ok ? { ...prev, runs: [...prev.runs, ...r.data.runs], next: r.data.next_cursor ?? null, busy: false, error: null, scope: mergeScope(prev.scope, r.data.workspace_scope ?? null) } : { ...prev, busy: false, error: `${r.error.code}: ${r.error.message}` }) : prev));
    });
  };

  const groups = useMemo(() => {
    const byGroup = new Map<string, RunSummary[]>();
    const needle = query.trim().toLowerCase();
    for (const run of (current?.runs ?? []).filter((r) => !needle || `${r.goal ?? ''} ${r.status ?? ''}`.toLowerCase().includes(needle))) {
      const key = run.milestone_group_id ? `Milestone ${run.milestone_group_id}` : `${(run.timestamp ?? 'time not recorded').slice(0, 10)}`;
      byGroup.set(key, [...(byGroup.get(key) ?? []), run]);
    }
    return [...byGroup.entries()];
  }, [current, query]);

  return (
    <div>
      <div className="ck-runs-head">
        <h2 style={{ fontSize: 14, margin: 0 }}>Runs</h2>
        <span>
          <button type="button" title="Acquire a new consistent snapshot of the history store" onClick={() => void session.acquire()} disabled={snapshotId === null && session.status.state === 'loading'}>Refresh runs</button>{' '}
          {workspace ? <button type="button" className="ck-primary" onClick={() => go('workspace')}>New Run</button> : null}
        </span>
      </div>
      {(current?.runs.length ?? 0) > 6 ? <input type="search" aria-label="Filter runs" placeholder="Filter by goal or status" value={query} onChange={(e) => setQuery(e.target.value)} className="ck-filter" /> : null}
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
      {current && workspace && current.runs.length === 0 && !current.error ? <div className="ck-state"><strong>No run is recorded for this workspace</strong>Kriya found no run whose evidence names this folder. Start one with New Run.</div> : null}
      {current?.scope ? <div className="muted small" data-testid="run-scope">{scopeNote(current.scope)}</div> : null}
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
