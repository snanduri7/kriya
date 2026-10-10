import { Badge, KeyValue, Panel, useSession, useWorkspace, type ModuleDefinition } from '../../core';
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
const STATE_SOURCE: Record<string, string> = { env: 'KRIYA_STATE_DIR (operator environment)', config: 'paths.state (configuration)', default: 'Kriya default (~/.kriya/state)' };

/** The open workspace as Kriya describes it. Not a file explorer: facts only, each with the words Kriya used. */
function Workspace() {
  const ws = useWorkspace();
  const session = useSession();
  const open = ws.current;
  if (!open) return <Panel title="Workspace"><div className="ck-state"><strong>No workspace is open</strong>Open a folder from the toolbar or File &gt; Open Folder.</div></Panel>;
  const f = open.facts;
  return (
    <Panel title="Workspace">
      <KeyValue items={[
        ['Name', f.name],
        ['Path', <span key="p" className="mono">{f.workspace}</span>],
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
        ['Pinned snapshot', session.snapshotId ? <span key="n" className="mono">{session.snapshotId}</span> : 'none pinned'],
      ]} />
      {f.configuration.file_in_workspace ? <p className="muted small" role="note">{f.configuration.note}</p> : null}
      <p className="muted small">Opening a folder only reads it. Nothing was written to it and no run was started.</p>
    </Panel>
  );
}

export const workspaceModule: ModuleDefinition = {
  id: 'workspace',
  placement: { page: 'workspace', surface: 'page', label: 'Workspace', order: 10 },
  requiredCapabilities: ['op:workspace.describe'], optionalCapabilities: [], selectionDependencies: [],
  render: Workspace,
};
