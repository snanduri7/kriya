import { RUN_DOCUMENTS_PAGE, RunCardView, toAvailability, useAsync, useDispatch, useExecution, useNavigate, useProvider, useSession, useWorkspacePath } from '../../core';

const SHOWN = 5;


/** The last few runs recorded for this folder (Kriya's own list, read from the pinned snapshot). It is a shortcut to Runs, never a place to start work. */
export function RecentRuns() {
  const provider = useProvider();
  const session = useSession();
  const dispatch = useDispatch();
  const go = useNavigate();
  const workspace = useWorkspacePath();
  const ex = useExecution();
  const running = ex.run !== null && ex.run.stage !== 'finished'; // a run is in progress: "No runs yet" would read as if nothing is happening
  const snapshotId = session.snapshotId;
  // a finished run refreshes the snapshot (session.snapshotId changes), which re-keys this read
  const list = useAsync(snapshotId && workspace ? `recent:${snapshotId}:${workspace}` : null, async (signal) => toAvailability(await provider.listRuns(snapshotId!, { limit: SHOWN, workspace: workspace! }, signal)), { state: 'unavailable', code: 'store_absent', detail: 'No run history yet.' } as never);
  if (list.state === 'loading') return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted" role="status">Loading…</p></section>;
  if (list.state !== 'ready' && list.state !== 'degraded') {
    // no snapshot yet (nothing has run here) or unreadable: the quiet truth, not an error banner
    if (running && list.state !== 'error') return null; // nothing completed to list yet; the run itself is the page
    return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted">{list.state === 'error' ? `History could not be read: ${list.message}` : 'No runs yet.'}</p></section>;
  }
  const runs = list.data.runs.slice(0, SHOWN);
  return (
    <section aria-label="Recent runs">
      <h3>Recent runs</h3>
      {runs.length === 0 ? <p className="muted">{running ? 'No completed runs yet.' : 'No runs yet.'}</p> : (
        <ul className="ck-list">
          {runs.map((r) => (
            <li key={r.run_id}>
              <button type="button" onClick={() => { dispatch({ type: 'selectRun', runId: r.run_id }); go(RUN_DOCUMENTS_PAGE); }}>
                <RunCardView run={r} />
              </button>
            </li>
          ))}
        </ul>
      )}
      <button type="button" className="ck-link" onClick={() => go(RUN_DOCUMENTS_PAGE)}>All runs</button>
    </section>
  );
}
