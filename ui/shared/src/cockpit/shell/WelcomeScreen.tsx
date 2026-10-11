import { useWorkspace } from '../core';

/** The folder's last path segment, for display only (presentation of a string; the folder's meaning is Kriya's). */
export const folderName = (path: string): string => path.replace(/\/+$/, '').split('/').pop() || path;

/** Names for a list of folders: the last segment, plus the parent folder's name only where two entries would otherwise read the same. */
export function folderLabels(paths: readonly string[]): Map<string, string> {
  const count = new Map<string, number>();
  for (const p of paths) count.set(folderName(p), (count.get(folderName(p)) ?? 0) + 1);
  return new Map(paths.map((p) => {
    const parent = folderName(p.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/');
    return [p, (count.get(folderName(p)) ?? 0) > 1 ? `${folderName(p)} — ${parent}` : folderName(p)];
  }));
}

const MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform);
export const OPEN_FOLDER_SHORTCUT = MAC ? '⌘O' : 'Ctrl+O';

/** What the person sees before any folder is open: the product name, Open Folder, and the folders they opened before. No runs, no fixtures, no assumptions. */
export function WelcomeScreen() {
  const ws = useWorkspace();
  const stale = ws.notice ? ws.recent.includes(ws.notice.path) : false;
  return (
    <main className="ck-welcome" aria-label="Welcome">
      <h1>Kriya</h1>
      <p className="muted">Open a folder to inspect its runs.</p>
      {ws.canChoose ? (
        <p>
          <button type="button" className="ck-primary" disabled={ws.busy} onClick={() => void ws.openFolder()}>Open Folder…</button>{' '}
          <span className="muted small">{OPEN_FOLDER_SHORTCUT}</span>
        </p>
      ) : <p className="ck-state" role="status">This host cannot choose a folder.</p>}
      {ws.busy ? <p className="muted" role="status">Asking Kriya about the folder…</p> : null}
      {ws.notice ? (
        <div className="ck-state" role="alert">
          <strong>Could not open {folderName(ws.notice.path)}</strong>
          {ws.notice.message} <span className="muted small">({ws.notice.code})</span>
          {ws.notice.detail ? <details><summary className="muted small">Technical detail</summary><span className="mono">{ws.notice.detail}</span></details> : null}
          <div>
            {stale ? <button type="button" onClick={() => void ws.forgetRecent(ws.notice!.path)}>Remove from recent</button> : null}{' '}
            <button type="button" onClick={ws.dismissNotice}>Dismiss</button>
          </div>
        </div>
      ) : null}
      {ws.recent.length ? (
        <section aria-label="Recent workspaces">
          <h2>Recent workspaces</h2>
          <ul className="ck-recent">
            {ws.recent.map((path) => (
              <li key={path}>
                <button type="button" disabled={ws.busy} onClick={() => void ws.openRecent(path)} aria-label={`Open ${folderName(path)}`}>
                  <span>{folderName(path)}</span>
                  <span className="muted small mono">{path}</span>
                </button>
                <button type="button" className="ck-quiet" aria-label={`Remove ${folderName(path)} from recent`} onClick={() => void ws.forgetRecent(path)}>Remove</button>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}

/** A refused open while a workspace is already open: the open workspace is untouched, and the reason is Kriya's. (On the welcome screen the same notice is part of the page.) */
export function WorkspaceNotice() {
  const ws = useWorkspace();
  if (!ws.notice || !ws.current) return null;
  const stale = ws.recent.includes(ws.notice.path);
  return (
    <div className="ck-state ck-notice" role="alert">
      <strong>Could not open {folderName(ws.notice.path)}; {ws.current.facts.name} is still open</strong>
      {ws.notice.message} <span className="muted small">({ws.notice.code})</span>{' '}
      {ws.notice.detail ? <details><summary className="muted small">Technical detail</summary><span className="mono">{ws.notice.detail}</span></details> : null}
      {stale ? <button type="button" onClick={() => void ws.forgetRecent(ws.notice!.path)}>Remove from recent</button> : null}{' '}
      <button type="button" onClick={ws.dismissNotice}>Dismiss</button>
    </div>
  );
}
