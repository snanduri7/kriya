import { useEffect, useState, type ReactNode } from 'react';
import { Badge, useProvider, useSession } from '../../core';
import type { WorkspaceDescribe } from '../../../model/kup';

type Checkout = WorkspaceDescribe['checkout'];

/** Kriya's own words for what Git reported. No folder is classified: an empty folder, a plain project, a checkout and a modified checkout use these same fields. */
function repository(c: Checkout): string {
  switch (c.state) {
    case 'checkout_root': return 'Git checkout';
    case 'inside_repository': return `Inside a Git repository${c.root ? ` rooted at ${c.root}` : ''} (its facts are not applied to this folder)`;
    case 'not_a_repository': return 'Not a Git repository';
    case 'git_unavailable': return `Git did not answer${c.reason ? `: ${c.reason}` : ''}`;
    default: return String(c.state);
  }
}

const branchLine = (c: Checkout): string => (c.state !== 'checkout_root' ? 'Not recorded' : c.detached ? 'Detached HEAD' : c.branch ?? 'Not recorded');
const revisionLine = (c: Checkout): string => (c.state !== 'checkout_root' ? 'Not recorded' : c.head ?? 'No commit yet');
const treeLine = (c: Checkout): string => {
  if (c.state !== 'checkout_root' || c.dirty === null) return `Not recorded${c.reason ? ` (${c.reason})` : ''}`;
  if (!c.dirty) return 'No changes reported by Git';
  const n = c.changed_entries ?? 0;
  return `${n}${c.changed_truncated ? '+' : ''} changed or untracked ${n === 1 ? 'entry' : 'entries'} reported by Git`;
};
export const STATE_SOURCE: Record<string, string> = { env: 'KRIYA_STATE_DIR (operator environment)', config: 'paths.state (configuration)', default: 'Kriya default (~/.kriya/state)' };

/** The published snapshots Kriya lists, to pin another one. Choosing re-verifies its digest before anything is read and clears the selection. */
export function SnapshotPicker() {
  const provider = useProvider();
  const session = useSession();
  const [list, setList] = useState<Array<{ snapshot_id: string; acquisition_completed_at: string; rows: number }> | null>(null);
  useEffect(() => {
    let live = true;
    void provider.listSnapshots().then((r) => { if (live && r.ok) setList(r.data.snapshots as unknown as Array<{ snapshot_id: string; acquisition_completed_at: string; rows: number }>); });
    return () => { live = false; };
  }, [provider, session.snapshotId]);
  if (!session.snapshotId) return <span>none pinned</span>;
  if (!list || list.length < 2) return <span className="mono">{session.snapshotId}</span>;
  return (
    <select aria-label="Pinned snapshot" value={session.snapshotId} onChange={(e) => session.choose(e.target.value)}>
      {list.map((snap) => <option key={snap.snapshot_id} value={snap.snapshot_id}>{snap.snapshot_id} · acquired {snap.acquisition_completed_at.slice(0, 19).replace('T', ' ')} · {snap.rows} rows</option>)}
    </select>
  );
}

/** Shows the open workspace's folder in Finder. The host reveals the folder IT has open; no path is sent. Absent when the host cannot. */
export function RevealButton() {
  const provider = useProvider();
  const [note, setNote] = useState<string | null>(null);
  if (!provider.desktop?.revealWorkspace) return null;
  const reveal = () => provider.desktop!.revealWorkspace!();
  return (
    <>
      <button type="button" className="ck-quiet" onClick={() => void reveal().then((r) => setNote(r.revealed ? null : `Could not reveal: ${r.reason ?? 'unknown reason'}`), (e: unknown) => setNote(`Could not reveal: ${e instanceof Error ? e.message : String(e)}`))}>Reveal in Finder</button>
      {note ? <span className="muted small" role="status">{note}</span> : null}
    </>
  );
}

/** The open folder as Kriya describes it: facts only, each with the words Kriya used. */
export function workspaceRows(f: WorkspaceDescribe, snapshot: ReactNode, path: ReactNode): Array<readonly [string, ReactNode]> {
  return [
    ['Name', f.name],
    ['Path', path],
    ...(f.requested !== f.workspace ? [['Chosen as', <span key="r" className="mono">{f.requested}</span>] as const] : []),
    ['Identity', <span key="i" className="mono" title={f.identity}>{f.identity.slice(0, 16)}…</span>],
    ['Repository', repository(f.checkout)],
    ['Branch', branchLine(f.checkout)],
    ['Revision', <span key="h" className="mono">{revisionLine(f.checkout)}</span>],
    ['Working tree', treeLine(f.checkout)],
    ['Recovery status', <Badge key="s" tone={f.recovery.status === 'CLEAN' ? 'ok' : 'warn'}>{f.recovery.status}</Badge>],
    ['A run holds the workspace', f.recovery.run_active ? 'yes (RUN_ACTIVE)' : 'no'],
    ['Kriya state', <span key="d" className="mono">{f.state.directory}</span>],
    ['State chosen by', STATE_SOURCE[f.state.source] ?? f.state.source],
    ['Trace database', f.state.trace_database_present ? 'present' : 'not present'],
    ['Workspace .kriya directory', f.kriya_directory_present ? 'present' : 'not present'],
    ['Pinned snapshot', snapshot],
  ];
}
