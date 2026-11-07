import { useState } from 'react';
import { INSPECTOR_DRAWER_BELOW, INSPECTOR_NARROW_BELOW, hasData, useExecution, useViewportWidth, useWorkspace, useWorkspaceUi, type ModuleDefinition } from '../../core';
import { CONFIG_APPROVAL_BLOCKER } from './ConfigApproval';
import { CurrentRun } from './CurrentRun';
import { ProjectSetupCard } from './ProjectSetup';
import { type ComposedCell } from './CapabilityStrip';
import { GoalComposer } from './GoalComposer';
import { ActivityDock } from './ActivityDock';
import { Inspector } from './Inspector';
import { ProjectBar } from './ProjectBar';
import { checklist, preflightBody, summarizeReadiness } from './Readiness';
import { useRunSetup } from './runSetup';

/**
 * The PROJECT WORKSPACE: a developer's workspace, not a stacked dashboard. A project bar on top (where am I, what can I do with the folder, is it ready, capabilities, settings); the MAIN pane
 * showing exactly ONE primary state - the goal composer, the running task, the approval, the result or the failure - and growing with the window; the Inspector on the right (Runs, Memory, Security,
 * Skills, Project: one click each); the Activity dock along the bottom. Below 900 px the Inspector is an overlay drawer. Every panel is the same component over the same Kriya answers as before:
 * this arranges, it decides nothing.
 */
function ProjectHome() {
  const ws = useWorkspace();
  const ex = useExecution();
  const ui = useWorkspaceUi();
  const width = useViewportWidth();
  const [nonce, setNonce] = useState(0);
  const { context, probe } = useRunSetup(ws.current?.path ?? null, nonce, ex.locked);
  const open = ws.current;
  if (!open) return <div className="ck-state"><strong>No project is open</strong>Open a folder from the toolbar or File &gt; Open Folder.</div>;
  const f = open.facts;
  const pre = ex.preflight.state === 'ready' ? ex.preflight.data : null;
  const summary = summarizeReadiness(ex, probe, context);
  const recheck = () => { setNonce((n) => n + 1); ex.refreshPreflight(); };
  const drawer = width < INSPECTOR_DRAWER_BELOW;
  const inspectorOpen = ui.inspectorOpen ?? !drawer;
  // "View readiness details" must be SEEN to respond: it selects Inspector > Project and opens the Inspector (a drawer on a narrow window).
  const showDetails = () => { ui.setTab('project'); ui.setInspectorOpen(true); };
  const skillCount = hasData(context) && context.data.skills ? context.data.skills.total : null;
  const unverified = hasData(context) && context.data.skills ? context.data.skills.items.filter((k) => !k.verified).length : 0;
  const mcp = hasData(context) && context.data.tools ? context.data.tools.mcp_servers.length : null;
  // The strip's model / Skills / Tools cells are this page's own backend answers (the model probe and the catalog), passed through in their own words.
  const modelCell = pre ? checklist(pre, probe).find((r) => r.label === 'Model server') ?? null : null;
  const composed: Record<string, ComposedCell | null> = {
    model: modelCell ? { label: 'Model server', tone: modelCell.tone, status: modelCell.status, detail: modelCell.detail } : null,
    skills: skillCount === null ? null : { label: 'Skills', tone: unverified ? 'warn' : 'ok', status: `${skillCount}${unverified ? ` · ${unverified} unverified` : ''}` },
    tools: mcp === null ? null : { label: 'Tools', tone: 'info', status: `${mcp} MCP server${mcp === 1 ? '' : 's'}` },
  };
  // State A: a folder with no configuration file. Setup is recommended, never required (the folder is already runnable on the defaults).
  const unconfigured = !ex.run && ex.canInitializeProject && pre !== null && pre.configuration.file === null && pre.configuration.status !== 'failed' && f.name !== undefined;
  // The only thing between the person and Start Run is an approval they can give here.
  const actionOnly = ex.canApproveConfiguration && summary.state === 'blocked' && summary.blockers.length > 0 && summary.blockers.every((b) => b.code === CONFIG_APPROVAL_BLOCKER);
  const verdict = ex.run ? null : summary.state === 'ready' ? 'ready' : actionOnly ? 'action' : summary.state === 'blocked' || summary.state === 'error' ? 'blocked' : null;
  const mode = drawer ? 'drawer' : width < INSPECTOR_NARROW_BELOW ? 'medium' : 'wide';
  return (
    <div className="ck-ws" data-testid="project-workspace" data-mode={mode} data-inspector={inspectorOpen ? 'open' : 'closed'}>
      <ProjectBar name={f.name} path={f.workspace} pre={pre} probe={probe} composed={composed} inspectorOpen={inspectorOpen} verdict={verdict} />
      <div className="ck-ws-body">
        <main className="ck-ws-main" aria-label="Current task">
          {ex.run ? <CurrentRun /> : ex.available ? <GoalComposer summary={summary} onDetails={showDetails} onRecheck={recheck} /> : <section aria-label="Start a run"><h2>Enter a goal</h2><p className="muted" role="status">This host cannot start a run.</p></section>}
          {unconfigured ? <ProjectSetupCard model={pre.model?.model ?? null} fallbacks={pre.model?.fallback_chain ?? []} onCreated={recheck} /> : null}
        </main>
        {inspectorOpen ? (
          <div className={`ck-ws-inspector${drawer ? ' overlay' : ''}`}>
            <Inspector pre={pre} probe={probe} context={context} preflightFallback={ex.available ? preflightBody(ex.preflight) : null} onRecheck={recheck} />
          </div>
        ) : null}
      </div>
      <ActivityDock />
    </div>
  );
}

export const workspaceModule: ModuleDefinition = {
  id: 'workspace',
  placement: { page: 'project', surface: 'page', label: 'Project', order: 10 },
  requiredCapabilities: ['op:workspace.describe'], optionalCapabilities: [], selectionDependencies: [],
  render: ProjectHome,
};
