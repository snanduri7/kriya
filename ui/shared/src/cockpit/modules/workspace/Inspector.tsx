import { INSPECTOR_TABS, hasData, useViewportWidth, useWorkspace, useWorkspaceUi, INSPECTOR_DRAWER_BELOW, type InspectorTab } from '../../core';
import type { Availability } from '../../core';
import type { ExecutionContext, ExecutionModelProbe, ExecutionPreflight } from '../../../model/kup';
import { KeyValue, Panel, CopyText } from '../../core';
import { ProjectMemoryCard } from './ProjectMemory';
import { PreflightSummary, ProjectFacts, ReadinessChecklist } from './Readiness';
import { RecentRuns } from './RecentRuns';
import { SecurityCard } from './SecurityCard';
import { ModelRouting, SkillsSummary, ToolsSummary } from './SetupSections';
import { RevealButton, SnapshotPicker, workspaceRows } from './WorkspaceFacts';

export interface InspectorProps {
  pre: ExecutionPreflight | null;
  probe: Availability<ExecutionModelProbe>;
  context: Availability<ExecutionContext>;
  /** The preflight answer in words when it is not (yet) a result: loading, error. */
  preflightFallback: React.ReactNode;
  onRecheck(): void;
  recheckLabel?: string;
}

/**
 * The right INSPECTOR: everything that is not the current task, one click away in tabs - Runs, Memory, Security, Skills, Project. It moves information; it adds no authority: every panel is the
 * same component, over the same Kriya answers, that used to sit further down a long page. Only the selected tab is mounted.
 */
export function Inspector(props: InspectorProps) {
  const ui = useWorkspaceUi();
  const width = useViewportWidth();
  const drawer = width < INSPECTOR_DRAWER_BELOW;
  return (
    <aside className="ck-inspector-pane" aria-label="Inspector" data-testid="inspector" data-mode={drawer ? 'drawer' : 'side'}>
      <div role="tablist" aria-label="Inspector tabs" className="ck-inspector-tabs">
        {INSPECTOR_TABS.map((t) => (
          <button key={t.id} type="button" role="tab" id={`ck-itab-${t.id}`} aria-selected={ui.tab === t.id} aria-controls="ck-inspector-panel" className={ui.tab === t.id ? 'ck-primary' : ''} onClick={() => ui.setTab(t.id)}>{t.label}</button>
        ))}
        <span className="grow" />
        <button type="button" className="ck-quiet" aria-label="Close Inspector" title="Close the Inspector" onClick={() => ui.setInspectorOpen(false)}>✕</button>
      </div>
      <div id="ck-inspector-panel" role="tabpanel" aria-labelledby={`ck-itab-${ui.tab}`} className="ck-inspector-body"><InspectorPanel tab={ui.tab} {...props} /></div>
    </aside>
  );
}

function InspectorPanel({ tab, pre, probe, context, preflightFallback, onRecheck }: InspectorProps & { tab: InspectorTab }) {
  const ws = useWorkspace();
  const open = ws.current;
  if (!open) return null;
  const f = open.facts;
  const skillCount = hasData(context) && context.data.skills ? context.data.skills.total : null;
  const unverified = hasData(context) && context.data.skills ? context.data.skills.items.filter((k) => !k.verified).length : 0;
  const mcp = hasData(context) && context.data.tools ? context.data.tools.mcp_servers.length : null;
  switch (tab) {
    case 'runs': return <RecentRuns />;
    case 'memory': return pre?.memory ? <ProjectMemoryCard memory={pre.memory} onRecheck={onRecheck} /> : <p className="muted">{pre ? 'This version of Kriya reports no Project Memory for this folder.' : 'Project Memory is read together with the project\'s readiness.'}</p>;
    case 'security': return pre?.security ? <SecurityCard rows={pre.health ?? []} demonstrations={pre.security.demonstrations} /> : <p className="muted">{pre ? 'This version of Kriya reports no security checks for this folder.' : 'Security checks are read together with the project\'s readiness.'}</p>;
    case 'skills': return (
      <div className="ck-inspector-stack">
        <details className="ck-setup" open><summary>Skills{skillCount === null ? '' : ` · ${skillCount}${unverified ? ` (${unverified} unverified)` : ''}`}</summary><SkillsSummary context={context} /></details>
        <details className="ck-setup" open><summary>Tools and capabilities{mcp === null ? '' : ` · ${mcp} MCP server${mcp === 1 ? '' : 's'}`}</summary><ToolsSummary context={context} /></details>
      </div>
    );
    case 'project': return (
      <div className="ck-inspector-stack" data-testid="project-details">
        <h3>Readiness</h3>
        {pre ? <ProjectFacts pre={pre} probe={probe} /> : preflightFallback}
        <p><button type="button" className="ck-quiet" onClick={onRecheck}>Check again</button></p>
        {pre ? <details className="ck-setup" data-testid="technical-project-details"><summary>Technical details</summary><ReadinessChecklist pre={pre} probe={probe} /></details> : null}
        <details className="ck-setup"><summary>Model routing</summary><ModelRouting context={context} probe={probe} /></details>
        {pre ? <details className="ck-setup"><summary>Run configuration details</summary><PreflightSummary data={pre} /></details> : null}
        <Panel title="About this project">
          <KeyValue items={workspaceRows(f, <SnapshotPicker />, <span key="p"><span className="mono">{f.workspace}</span> <CopyText text={f.workspace} what="workspace path" /> <RevealButton /></span>)} />
          {f.configuration.file_in_workspace ? <p className="muted small" role="note">{f.configuration.note}</p> : null}
          <p className="muted small">Opening a folder only reads it. Nothing was written to it and no run was started.</p>
        </Panel>
      </div>
    );
    default: return null;
  }
}
