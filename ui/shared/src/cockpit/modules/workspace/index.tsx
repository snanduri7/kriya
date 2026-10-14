import { useState } from 'react';
import { Badge, CopyText, KeyValue, Panel, hasData, useExecution, useWorkspace, useWorkspacePath, type ModuleDefinition } from '../../core';
import { CurrentRun } from './CurrentRun';
import { GoalComposer } from './GoalComposer';
import { PreflightSummary, ReadinessChecklist, preflightBody, summarizeReadiness } from './Readiness';
import { RecentRuns } from './RecentRuns';
import { useRunSetup } from './runSetup';
import { ModelRouting, SkillsSummary, ToolsSummary } from './SetupSections';
import { RevealButton, SnapshotPicker, workspaceRows } from './WorkspaceFacts';

/** Project Home: the open folder's name and state, the goal and Start Run, or - once a run has started - that run; recent runs; and everything else, one disclosure down. */
function ProjectHome() {
  const ws = useWorkspace();
  const ex = useExecution();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const { context, probe } = useRunSetup(workspace, nonce, ex.locked);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const open = ws.current;
  if (!open) return <div className="ck-state"><strong>No project is open</strong>Open a folder from the toolbar or File &gt; Open Folder.</div>;
  const f = open.facts;
  const pre = ex.preflight.state === 'ready' ? ex.preflight.data : null;
  const summary = summarizeReadiness(ex, probe, context);
  const recheck = () => { setNonce((n) => n + 1); ex.refreshPreflight(); };
  const skillCount = hasData(context) && context.data.skills ? context.data.skills.total : null;
  const unverified = hasData(context) && context.data.skills ? context.data.skills.items.filter((k) => !k.verified).length : 0;
  const mcp = hasData(context) && context.data.tools ? context.data.tools.mcp_servers.length : null;
  const showDetails = () => setDetailsOpen(true);
  return (
    <div className="ck-project">
      <div className="ck-project-head">
        <h1>{f.name}</h1>
        <span className="muted mono small" title={f.workspace}>{f.workspace}</span>
        <span className="grow" />
        {ex.run ? null : summary.state === 'ready' ? <Badge tone="ok">✓ Ready</Badge> : summary.state === 'blocked' || summary.state === 'error' ? <Badge tone="bad">Cannot start</Badge> : null}
      </div>
      {ex.run ? <CurrentRun /> : ex.available ? <GoalComposer summary={summary} onDetails={showDetails} onRecheck={recheck} /> : <section aria-label="Start a run"><h2>What do you want Kriya to do?</h2><p className="muted" role="status">This host cannot start a run.</p></section>}
      <RecentRuns />
      <details className="ck-setup" open={detailsOpen} onToggle={(e) => setDetailsOpen((e.currentTarget as HTMLDetailsElement).open)}>
        <summary>Project details</summary>
        {pre ? <ReadinessChecklist pre={pre} probe={probe} /> : ex.available ? preflightBody(ex.preflight) : null}
        {ex.available ? <p><button type="button" className="ck-quiet" onClick={recheck}>Check again</button></p> : null}
        <details className="ck-setup"><summary>Skills{skillCount === null ? '' : ` · ${skillCount}${unverified ? ` (${unverified} unverified)` : ''}`}</summary><SkillsSummary context={context} /></details>
        <details className="ck-setup"><summary>Tools and capabilities{mcp === null ? '' : ` · ${mcp} MCP server${mcp === 1 ? '' : 's'}`}</summary><ToolsSummary context={context} /></details>
        <details className="ck-setup"><summary>Model routing</summary><ModelRouting context={context} probe={probe} /></details>
        {pre ? <details className="ck-setup"><summary>Run configuration details</summary><PreflightSummary data={pre} /></details> : null}
        <Panel title="About this project">
          <KeyValue items={workspaceRows(f, <SnapshotPicker />, <span key="p"><span className="mono">{f.workspace}</span> <CopyText text={f.workspace} what="workspace path" /> <RevealButton /></span>)} />
          {f.configuration.file_in_workspace ? <p className="muted small" role="note">{f.configuration.note}</p> : null}
          <p className="muted small">Opening a folder only reads it. Nothing was written to it and no run was started.</p>
        </Panel>
      </details>
    </div>
  );
}

export const workspaceModule: ModuleDefinition = {
  id: 'workspace',
  placement: { page: 'project', surface: 'page', label: 'Project', order: 10 },
  requiredCapabilities: ['op:workspace.describe'], optionalCapabilities: [], selectionDependencies: [],
  render: ProjectHome,
};
