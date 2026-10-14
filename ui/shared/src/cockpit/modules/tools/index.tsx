import { useState } from 'react';
import { Badge, KeyValue, Panel, hasData, toAvailability, useAsync, useProvider, useWorkspacePath, UnavailableConfiguration, UnknownFields, whyNot, type Availability, type ModuleDefinition } from '../../core';
import type { ToolDescriptor, ToolsList } from '../../../model/kup';

type Approval = ToolDescriptor['approval'];

const UNKNOWN_EXPLAIN: Record<string, string> = {
  version: "a tool's version is learned only by starting it",
  scope: 'where this tool was configured from is not recorded',
  declared_capabilities: 'plugins declare no capabilities',
};
const KIND_TEXT: Record<ToolDescriptor['kind'], string> = { mcp_server: 'MCP server', plugin: 'Plugin' };

/** Kriya's reason codes in plain language. The state itself is never softened: there is no "approved" here, and the page says why. */
export function approvalText(a: Approval): string {
  switch (a.reason) {
    case 'NO_RECORDS_FOR_SERVER': return 'No approval is on file for this server, so its tools cannot run until an operator approves them.';
    case 'RECORDS_NEED_LIVE_TOOL_IDENTITY':
      return `${a.records_on_file} approval record${a.records_on_file === 1 ? '' : 's'} on file${a.recorded_tool_names.length ? ` (${a.recorded_tool_names.join(', ')})` : ''}. Whether they still apply depends on each tool's current definition, which is only known by starting the server. Nothing was started.`;
    case 'APPROVAL_STORE_UNREADABLE_OR_INVALID': return 'The approval store could not be read or is invalid, so nothing can be said about approvals.';
    case 'APPROVAL_STORE_PATH_REFUSED': return 'The approval store location was refused because it is inside the workspace.';
    case 'NOT_AN_MCP_SERVER': return 'Plugins are not MCP servers and have no invocation approval.';
    case 'TOOL_NOT_FOUND': return 'Kriya does not list a tool with this id.';
    default: return String(a.reason);
  }
}

export function ApprovalBadge({ a }: { a: Approval }) {
  const tone = a.state === 'NOT_APPROVED' ? 'neutral' : a.state === 'UNKNOWN' ? 'warn' : 'neutral';
  const label = a.state === 'NOT_APPROVED' ? 'not approved' : a.state === 'UNKNOWN' ? 'unknown' : 'not applicable';
  return <Badge tone={tone} title={approvalText(a)}>{label}</Badge>;
}

function networkText(c: NonNullable<ToolDescriptor['declared_capabilities']>): string {
  const bits = [c.workspace_read ? 'reads the workspace' : 'no workspace read', c.workspace_write ? 'writes the workspace' : 'no workspace write'];
  bits.push(c.network === 'denied' ? 'no network' : c.network === 'explicit_destinations' ? `network: ${c.network_hosts.join(', ') || 'listed hosts'}` : `network: ${c.network}`);
  return bits.join(' · ');
}

function ToolDetail({ workspace, id, nonce }: { workspace: string; id: string; nonce: number }) {
  const provider = useProvider();
  const detail = useAsync(`tool:${workspace}:${id}:${nonce}`, async (signal) => toAvailability(await provider.toolsDescribe(workspace, id, signal)), { state: 'loading' } as Availability<never>);
  const approval = useAsync(`approval:${workspace}:${id}:${nonce}`, async (signal) => toAvailability(await provider.toolsApprovalState(workspace, id, signal)), { state: 'loading' } as Availability<never>);
  const note = whyNot(detail, 'this tool');
  if (note || !hasData(detail)) return <p className="muted" role="status">{note}</p>;
  if (!detail.data.available) return <UnavailableConfiguration reason={detail.data.unavailable_reason} />;
  const t = detail.data.tool;
  if (!detail.data.found || !t) return <p className="muted" role="status">Kriya no longer lists a tool {id}. Refresh the list.</p>;
  const ref = t.reference as { executable?: string; argument_count?: number; environment_variable_names?: string[]; plugin?: string };
  const a = hasData(approval) && approval.data.approval ? approval.data.approval : t.approval;
  return (
    <section aria-label={`Tool ${t.name}`}>
      <h4 style={{ margin: '0 0 6px' }}>{t.name} <ApprovalBadge a={a} /></h4>
      <KeyValue items={[
        ['Type', KIND_TEXT[t.kind]],
        ['Provider', t.provider === 'configured_mcp' ? 'Configured MCP server' : 'Enabled plugin'],
        ['Status', t.status === 'CONFIGURED' ? 'Configured' : 'Enabled'],
        ['Health', 'Not checked. A tool is only started by a run or by an operator (kriya mcp inspect); this page never starts one.'],
        ['Version', 'Not known without starting it'],
        ['Configured as', ref.executable !== undefined
          ? `${ref.executable}${ref.argument_count ? ` with ${ref.argument_count} argument${ref.argument_count === 1 ? '' : 's'} (not shown)` : ''}`
          : `plugin ${ref.plugin ?? t.name}`],
        ...(ref.environment_variable_names !== undefined ? [['Environment variables', ref.environment_variable_names.length ? `${ref.environment_variable_names.join(', ')} (names only; values are never shown)` : 'None'] as const] : []),
        ['Declared capabilities', t.declared_capabilities ? networkText(t.declared_capabilities) : 'Plugins declare none'],
        ['Approval', approvalText(a)],
      ]} />
      <UnknownFields names={t.unknown_fields} explain={UNKNOWN_EXPLAIN} />
    </section>
  );
}

function ToolsTable({ list, selected, onSelect }: { list: ToolsList; selected: string | null; onSelect(id: string): void }) {
  return (
    <table className="ck-table" aria-label="Tools">
      <thead><tr><th>Tool</th><th>Type</th><th>Capabilities</th><th>Approval</th></tr></thead>
      <tbody>
        {list.items.map((t) => (
          <tr key={t.id} aria-selected={t.id === selected}>
            <td><button type="button" className="ck-quiet" aria-pressed={t.id === selected} aria-label={`Show ${t.name}`} onClick={() => onSelect(t.id)}><strong>{t.name}</strong></button></td>
            <td className="small">{KIND_TEXT[t.kind]}</td>
            <td className="small">{t.declared_capabilities ? networkText(t.declared_capabilities) : '—'}</td>
            <td><ApprovalBadge a={t.approval} /></td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Tools (configured MCP servers, enabled plugins) for the open folder. Read-only: nothing is started, no secret is shown, nothing can be added, removed or enabled here. */
function Tools() {
  const provider = useProvider();
  const workspace = useWorkspacePath();
  const [nonce, setNonce] = useState(0);
  const [chosen, setChosen] = useState<string | null>(null);
  const list = useAsync(workspace === null ? null : `tools:${workspace}:${nonce}`, async (signal) => toAvailability(await provider.toolsList(workspace!, signal)), { state: 'loading' } as Availability<never>);
  if (workspace === null) return <Panel title="Tools"><div className="ck-state"><strong>No workspace is open</strong>Open a folder to see the tools configured for it.</div></Panel>;
  const note = whyNot(list, 'tools');
  let body;
  if (note || !hasData(list)) body = <p className="muted" role="status">{note}</p>;
  else if (!list.data.available) body = <UnavailableConfiguration reason={list.data.unavailable_reason} />;
  else if (list.data.items.length === 0) body = <p className="muted" role="status">No MCP servers or plugins are configured for this folder.</p>;
  else {
    const selected = chosen !== null && list.data.items.some((t) => t.id === chosen) ? chosen : null;
    body = (
      <>
        <ToolsTable list={list.data} selected={selected} onSelect={setChosen} />
        {selected ? <ToolDetail workspace={workspace} id={selected} nonce={nonce} /> : <p className="muted small">Choose a tool to see how it is configured.</p>}
      </>
    );
  }
  return (
    <Panel title="Tools">
      <p className="muted">What Kriya has configured for this folder. Opening this page reads configuration and the approval records on file; it starts nothing.
        {' '}<button type="button" className="ck-quiet" onClick={() => setNonce((n) => n + 1)}>Refresh</button></p>
      {body}
      <p className="muted small">Read-only. Tools, MCP servers and their invocation approvals are managed by the operator (configuration and <span className="mono">kriya mcp</span>); this application shows them and changes nothing.</p>
    </Panel>
  );
}

export const toolsModule: ModuleDefinition = {
  id: 'tools',
  placement: { page: 'tools', surface: 'page', label: 'Tools', order: 10 },
  requiredCapabilities: ['op:tools.list', 'op:tools.describe', 'op:tools.approval_state'], optionalCapabilities: [], selectionDependencies: [],
  render: Tools,
};
