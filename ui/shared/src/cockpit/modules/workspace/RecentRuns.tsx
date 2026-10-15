import { RUN_DOCUMENTS_PAGE, RunCardView, toAvailability, useAsync, useDispatch, useExecution, useNavigate, useProvider, useSession, useWorkspace } from '../../core';
import { STATE_SOURCE } from './WorkspaceFacts';

const SHOWN = 3;   // a compact shortcut (Demo V2): the current run stays dominant; everything else is one click away under "View all runs"


/** The last few runs recorded for this folder (Kriya's own list, read from the pinned snapshot). It is a shortcut to Runs, never a place to start work. */
export function RecentRuns() {
  const provider = useProvider();
  const session = useSession();
  const dispatch = useDispatch();
  const go = useNavigate();
  const current = useWorkspace().current;
  const workspace = current?.path ?? null;
  // This window reads history from ONE state directory (the host's, not the folder's). A folder Kriya has worked in (it has a .kriya directory) that shows no run here may have been run under another state directory: say which one this window reads, rather than a bare "No runs yet".
  const elsewhere = current?.facts.kriya_directory_present === true ? (
    <p className="muted small" data-testid="history-elsewhere">This folder has a .kriya directory, so Kriya has worked here before. This window reads run history from <span className="mono">{current.facts.state.directory}</span> ({STATE_SOURCE[current.facts.state.source] ?? current.facts.state.source}); if those runs were made with a different Kriya state directory, start Kriya with that one.</p>
  ) : null;
  const ex = useExecution();
  const running = ex.run !== null && ex.run.stage !== 'finished'; // a run is in progress: "No runs yet" would read as if nothing is happening
  const snapshotId = session.snapshotId;
  // a finished run refreshes the snapshot (session.snapshotId changes), which re-keys this read
  const list = useAsync(snapshotId && workspace ? `recent:${snapshotId}:${workspace}` : null, async (signal) => toAvailability(await provider.listRuns(snapshotId!, { limit: SHOWN, workspace: workspace! }, signal)), { state: 'unavailable', code: 'store_absent', detail: 'No run history yet.' } as never);
  if (list.state === 'loading') return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted" role="status">Loading…</p></section>;
  if (list.state !== 'ready' && list.state !== 'degraded') {
    // no snapshot yet (nothing has run here) or unreadable: the quiet truth, not an error banner
    if (running && list.state !== 'error') return null; // nothing completed to list yet; the run itself is the page
    return <section aria-label="Recent runs"><h3>Recent runs</h3><p className="muted">{list.state === 'error' ? `History could not be read: ${list.message}` : 'No runs yet.'}</p>{list.state === 'error' ? null : elsewhere}</section>;
  }
  const runs = list.data.runs.slice(0, SHOWN);
  return (
    <section aria-label="Recent runs">
      <h3>Recent runs</h3>
      {runs.length === 0 ? <><p className="muted">{running ? 'No completed runs yet.' : 'No runs yet.'}</p>{running ? null : elsewhere}</> : (
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
      <button type="button" className="ck-link" onClick={() => go(RUN_DOCUMENTS_PAGE)}>View all runs</button>
    </section>
  );
}
