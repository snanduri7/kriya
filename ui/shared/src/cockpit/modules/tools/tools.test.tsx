/** The Tools page (Demo V2): read-only. It must never start a tool, never show a secret, and never call a tool "approved" when Kriya could not know. */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { FixtureHostOptions } from '../../providers/fixture';
import { renderWorkspaceShell } from '../../testing/harness';
import { approvalText } from '.';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
async function openTools(fixture: FixtureHostOptions = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Tools');
  await screen.findByRole('heading', { name: 'Tools' });
  return mounted;
}
const MUTATIONS = /add|install|remove|delete|enable|disable|edit|approve|revoke|test connection|start|connect|configure/i;

describe('Tools page', () => {
  it('lists MCP servers and plugins with type, declared capabilities and approval', async () => {
    await openTools();
    const table = await screen.findByRole('table', { name: 'Tools' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => within(r).getAllByRole('cell')[0]!.textContent)).toEqual(['filesystem', 'issues', 'core_tools']);
    expect(rows[0]).toHaveTextContent('MCP server');
    expect(rows[0]).toHaveTextContent('reads the workspace');
    expect(rows[1]).toHaveTextContent('network: issues.example.org');
    expect(rows[2]).toHaveTextContent('Plugin');
  });

  it('never shows "approved": a server with records is UNKNOWN, one without is not approved, a plugin is not applicable', async () => {
    await openTools();
    const table = await screen.findByRole('table', { name: 'Tools' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('unknown');
    expect(rows[1]).toHaveTextContent('not approved');
    expect(rows[2]).toHaveTextContent('not applicable');
    // no cell may claim approval: any text that says "approved" must be negated ("not approved")
    expect(within(table).queryAllByText(/approved/i).filter((el) => !/not approved/i.test(el.textContent ?? ''))).toEqual([]);
  });

  it('offers no way to start, add, remove, enable, test or approve anything', async () => {
    await openTools();
    await screen.findByRole('table', { name: 'Tools' });
    for (const button of screen.getAllByRole('button')) {
      const name = button.getAttribute('aria-label') ?? button.textContent ?? '';
      if (/^Show /.test(name)) continue; // selecting a row only reads
      expect(name).not.toMatch(MUTATIONS);
    }
    expect(screen.queryAllByRole('textbox')).toHaveLength(0);
  });

  it('the detail explains approval in words, lists environment variable NAMES only, and says health and version are not known', async () => {
    await openTools();
    fireEvent.click(await screen.findByRole('button', { name: 'Show filesystem' }));
    const detail = await screen.findByRole('region', { name: 'Tool filesystem' });
    expect(detail).toHaveTextContent(/2 approval records on file \(list_directory, read_file\)/);
    expect(detail).toHaveTextContent(/only known by starting the server\. Nothing was started\./);
    expect(detail).toHaveTextContent(/Health.*Not checked/);
    expect(detail).toHaveTextContent(/Version.*Not known without starting it/);
    expect(detail).toHaveTextContent('filesystem-server with 2 arguments (not shown)');
    fireEvent.click(screen.getByRole('button', { name: 'Show issues' }));
    const issues = await screen.findByRole('region', { name: 'Tool issues' });
    expect(issues).toHaveTextContent('ISSUES_API_TOKEN (names only; values are never shown)');
    expect(issues).toHaveTextContent(/No approval record is on file for this server/);
  });

  it('the detail asks for the approval state with the tool id, and reads it only (describe + approval_state, never a path)', async () => {
    const { host } = await openTools();
    fireEvent.click(await screen.findByRole('button', { name: 'Show issues' }));
    await screen.findByRole('region', { name: 'Tool issues' });
    await waitFor(() => expect(host.calls.some((c) => c.operation === 'tools.approval_state')).toBe(true));
    expect(host.calls.filter((c) => c.operation.startsWith('tools.') && c.operation !== 'tools.list')).toEqual([
      { operation: 'tools.describe', workspace: '/fixture/workspace', tool_id: 'mcp:issues' },
      { operation: 'tools.approval_state', workspace: '/fixture/workspace', tool_id: 'mcp:issues' },
    ]);
  });

  it('no tools, and an unresolvable configuration, are told apart', async () => {
    await openTools({ contextMode: 'empty' });
    expect(await screen.findByText('No MCP servers or plugins are configured for this folder.')).toBeInTheDocument();
  });

  it('an unresolvable configuration is shown as that', async () => {
    await openTools({ contextMode: 'unavailable' });
    expect(await screen.findByText(/Kriya could not resolve this folder's configuration/)).toBeInTheDocument();
    expect(screen.queryByText('No MCP servers or plugins are configured for this folder.')).toBeNull();
  });
});

describe('approvalText', () => {
  it('has a plain-language sentence for every reason code and never claims approval', () => {
    const base = { records_on_file: null, recorded_tool_names: [] as string[] };
    for (const [state, reason] of [['NOT_APPROVED', 'NO_RECORDS_FOR_SERVER'], ['UNKNOWN', 'RECORDS_NEED_LIVE_TOOL_IDENTITY'], ['UNKNOWN', 'APPROVAL_STORE_UNREADABLE_OR_INVALID'],
      ['UNKNOWN', 'APPROVAL_STORE_PATH_REFUSED'], ['NOT_APPLICABLE', 'NOT_AN_MCP_SERVER'], ['UNKNOWN', 'TOOL_NOT_FOUND']] as const) {
      const text = approvalText({ state, reason, ...base, ...(reason === 'RECORDS_NEED_LIVE_TOOL_IDENTITY' ? { records_on_file: 1 } : {}) });
      expect(text.length).toBeGreaterThan(20);
      expect(text).not.toBe(reason);
      expect(text).not.toMatch(/\bis approved\b|\bare approved\b/i);
    }
  });
});
