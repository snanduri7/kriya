import { RUN_DOCUMENTS_PAGE, toAvailability, useAsync, useDispatch, useNavigate, useProvider, useSession, useWorkspacePath } from '../../core';

const SHOWN = 5;
const statusText = (s: string | null | undefined): string => (s === 'success' ? 'Succeeded' : s === 'failure' ? 'Failed' : s === 'in_progress' ? 'Unfinished' : s ? s : 'Status not recorded');

/** The last few runs recorded for this folder (Kriya's own list, read from the pinned snapshot). It is a shortcut to Runs, never a place to start work. */
export function RecentRuns() {
  const provider = useProvider();
  const session = useSession();
  const dispatch = useDispatch();
  const go = useNavigate();
  const workspace = useWorkspacePath();
  const snapshotId = session.snapshotId;
  // a finished run refreshes the snapshot (session.snapshotId changes), which re-keys this read
  const list = useAsync(snapshotId && workspace ? `recent:${snapshotId}:${workspace}` : null, async (signal) => toAvailability(await provider.listRuns(snapshotId!, { limit: SHOWN, workspace: workspace! }, signal)), { state: 'unavailable', code: 'store_absent', detail: 'No run history yet.' } as never);
  if (list.state === 'loading') return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted" role="status">Loading…</p></section>;
  if (list.state !== 'ready' && list.state !== 'degraded') {
    // no snapshot yet (nothing has run here) or unreadable: the quiet truth, not an error banner
    return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted">{list.state === 'error' ? `History could not be read: ${list.message}` : 'No runs yet.'}</p></section>;
  }
  const runs = list.data.runs.slice(0, SHOWN);
  return (
    <section aria-label="Recent runs">
      <h3>Recent runs</h3>
      {runs.length === 0 ? <p className="muted">No runs yet.</p> : (
        <ul className="ck-list">
          {runs.map((r) => (
            <li key={r.run_id}>
              <button type="button" onClick={() => { dispatch({ type: 'selectRun', runId: r.run_id }); go(RUN_DOCUMENTS_PAGE); }}>
                <div>{r.goal ?? r.run_id}</div>
                <div className="muted small">{statusText(r.status)}{r.timestamp ? ` · ${r.timestamp.slice(0, 16).replace('T', ' ')}` : ''}{r.duration_sec != null ? ` · ${Math.round(r.duration_sec)}s` : ''}</div>
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="ck-link" onClick={() => go(RUN_DOCUMENTS_PAGE)}>All runs</button>
    </section>
  );
}
