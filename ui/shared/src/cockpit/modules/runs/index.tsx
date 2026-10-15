import { useEffect, useMemo, useState } from 'react';
import { AvailabilityView, PROJECT_PAGE, RunCardView, RUN_DOCUMENTS_PAGE, useDispatch, useExecution, useNavigate, useModuleSelection, useProvider, useSession, useWorkspacePath, type ModuleDefinition } from '../../core';
import { NO_FILTER, filterRuns, isFiltering, type RangeFilter, type RunFilter, type StatusFilter } from './filter';
import type { HistoryList, RunSummary } from '../../../model/kup';

const PAGE = 200;

const mergeScope = (a: Scope | null, b: Scope | null): Scope | null => (a && b ? { ...b, matched: a.matched + b.matched, other: a.other + b.other, unattributed: a.unattributed + b.unattributed, scanned: a.scanned + b.scanned } : b ?? a);

/** What Kriya set aside, in its own terms: attributed to another workspace, or carrying no recorded workspace at all (never guessed either way). */
function scopeNote(scope: Scope): string {
  const parts = [`${scope.other} run(s) in this state store belong to other workspaces`, `${scope.unattributed} have no recorded workspace and are not listed`];
  return `${parts.join('; ')}.${scope.truncated ? ' The scan stopped before the end of history; load more to continue.' : ''}`;
}


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
  const [filter, setFilter] = useState<RunFilter>(NO_FILTER);
  // "now" for the time ranges, taken when the person picks a range or refreshes (not on every render, which would also be impure)
  const [asOf, setAsOf] = useState(() => Date.now());
  const ex = useExecution();
  const active = ex.run && ex.run.stage !== 'finished' ? ex.run : null;
  const liveSelected = useModuleSelection('liveLaunchId');
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

  const filtered = useMemo(() => filterRuns(current?.runs ?? [], filter, asOf), [current, filter, asOf]);
  const groups = useMemo(() => {
    const byGroup = new Map<string, RunSummary[]>();
    for (const run of filtered.runs) {
      const key = run.milestone_group_id ? `Milestone ${run.milestone_group_id}` : `${(run.timestamp ?? 'time not recorded').slice(0, 10)}`;
      byGroup.set(key, [...(byGroup.get(key) ?? []), run]);
    }
    return [...byGroup.entries()];
  }, [filtered]);
  const filtering = isFiltering(filter);

  return (
    <div>
      <div className="ck-runs-head">
        <h2 style={{ fontSize: 14, margin: 0 }}>Run history</h2>
        <span>
          <button type="button" title="Acquire a new consistent snapshot of the history store" onClick={() => { setAsOf(Date.now()); void session.acquire(); }} disabled={snapshotId === null && session.status.state === 'loading'}>Refresh runs</button>{' '}
          {workspace ? <button type="button" onClick={() => go(PROJECT_PAGE)}>New Run</button> : null}
        </span>
      </div>
      {active ? (
        <div className="ck-active-row">
          <button type="button" aria-selected={liveSelected === active.launchId} aria-label="Active run - return to it" onClick={() => { dispatch({ type: 'selectLiveRun', launchId: active.launchId }); go(RUN_DOCUMENTS_PAGE); }}>
            <div><span aria-hidden="true">● </span>Active run{active.review || active.runtimeReview ? ' · approval waiting' : ''}</div>
            <div className="muted small">{active.stage === 'starting' ? 'Starting' : 'Running'} · return to it</div>
          </button>
        </div>
      ) : null}
      {(current?.runs.length ?? 0) > 0 ? (
        <div className="ck-filters" role="search" aria-label="Filter runs">
          <input type="search" aria-label="Filter runs" placeholder="Search by what you asked" value={filter.query} onChange={(e) => setFilter({ ...filter, query: e.target.value })} className="ck-filter" />
          <select aria-label="Status" value={filter.status} onChange={(e) => setFilter({ ...filter, status: e.target.value as StatusFilter })}>
            <option value="all">Any status</option><option value="success">Success</option><option value="failure">Failure</option><option value="unfinished">Unfinished</option><option value="not_recorded">Status not recorded</option>
          </select>
          <select aria-label="Time" value={filter.range} onChange={(e) => { setAsOf(Date.now()); setFilter({ ...filter, range: e.target.value as RangeFilter }); }}>
            <option value="any">Any time</option><option value="today">Today</option><option value="7d">Last 7 days</option><option value="30d">Last 30 days</option>
          </select>
          {filtering ? <button type="button" className="ck-quiet" onClick={() => setFilter(NO_FILTER)}>Clear filters</button> : null}
        </div>
      ) : null}
      {snapshotId === null && session.status.state === 'unavailable' && session.status.reason === 'store_absent' ? (
        <div className="ck-state" data-testid="no-history">
          <strong>{active ? 'No completed runs yet.' : 'No runs yet.'}</strong>
          Nothing has been recorded for this project. Work starts on the Project page.
          <div><button type="button" className="ck-primary" onClick={() => go(PROJECT_PAGE)}>Go to Project</button>{' '}<button type="button" className="ck-quiet" title="Ask Kriya for a new consistent snapshot of its history" onClick={() => void session.acquire()}>Check for history again</button></div>
        </div>
      ) : null}
      {snapshotId === null && !(session.status.state === 'unavailable' && session.status.reason === 'store_absent') ? <AvailabilityView value={session.status}>{() => null}</AvailabilityView> : null}
      {session.status.state !== 'ready' && session.status.state !== 'loading' && !(session.status.state === 'unavailable' && session.status.reason === 'store_absent') ? <button type="button" onClick={() => void session.acquire()}>Acquire snapshot</button> : null}
      {snapshotId !== null && current === null ? <div className="ck-state muted" role="status">Loading…</div> : null}
      {current?.error ? <div className="ck-state" role="alert"><strong>Runs could not be read</strong>{current.error}</div> : null}
      {groups.map(([group, runs]) => (
        <div key={group}>
          <div className="ck-group">{group}</div>
          <ul className="ck-list">
            {runs.map((run) => (
              <li key={run.run_id}>
                <button type="button" aria-selected={run.run_id === selected} onClick={() => dispatch({ type: 'selectRun', runId: run.run_id })}>
                  <RunCardView run={run} />
                </button>
              </li>
            ))}
          </ul>
        </div>
      ))}
      {current ? (
        <div className="muted small" role="status">
          {filtering ? `${filtered.runs.length} of ${current.runs.length} loaded run(s) match` : `${current.runs.length} run(s) loaded${current.next ? '' : ' (all)'}`}
          {filtering && current.next ? '. Filters apply to the runs loaded so far; load more to include older runs' : ''}
          {filtering && filter.range !== 'any' && filtered.untimed ? `. ${filtered.untimed} run(s) have no recorded time and cannot be placed in a time range` : ''}
        </div>
      ) : null}
      {current && filtering && filtered.runs.length === 0 && current.runs.length > 0 ? <div className="ck-state"><strong>No loaded run matches these filters</strong><button type="button" onClick={() => setFilter(NO_FILTER)}>Clear filters</button></div> : null}
      {current && workspace && current.runs.length === 0 && !current.error ? <div className="ck-state"><strong>{active ? 'No completed runs yet.' : 'No runs yet.'}</strong>Kriya found no run whose evidence names this project. <button type="button" className="ck-primary" onClick={() => go(PROJECT_PAGE)}>Go to Project</button></div> : null}
      {current?.scope ? <details className="ck-tech"><summary>About this list</summary><div className="muted small" data-testid="run-scope">{scopeNote(current.scope)}</div></details> : null}
      {current?.next ? <button type="button" disabled={current.busy} onClick={more}>{current.busy ? 'Loading…' : 'Load more runs'}</button> : null}
    </div>
  );
}

export const runsModule: ModuleDefinition = {
  id: 'runs',
  placement: { page: 'runs', surface: 'secondary', label: 'Runs', order: 10 },
  requiredCapabilities: ['snapshot'], optionalCapabilities: [], selectionDependencies: ['runId', 'liveLaunchId'],
  render: RunsList,
};
