/** New Project through the real shell over the fixture host: the native parent chooser returns a token, Kriya creates the folder, the ordinary validated open follows.
 * The renderer never names where a folder is created and never reports success on its own: every outcome here is the host's / Kriya's typed answer. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

const dialog = () => screen.findByRole('dialog', { name: 'New Project' });
const begin = async () => { fireEvent.click(await screen.findByRole('button', { name: /^New Project…/ })); return dialog(); };
const typeName = (d: HTMLElement, value: string) => fireEvent.change(within(d).getByRole('textbox', { name: 'Project name' }), { target: { value } });
const create = (d: HTMLElement) => fireEvent.click(within(d).getByRole('button', { name: 'Create project' }));

describe('New Project from the welcome screen', () => {
  it('Welcome states the product promise and offers New Project and Open Folder as the two primary actions', async () => {
    renderWorkspaceShell({ fixture: { workspace: null } });
    const main = await screen.findByRole('main', { name: 'Welcome' });
    expect(within(main).getByText(/Give Kriya an engineering goal\. Watch it plan, execute, validate, recover, and prove what happened\./)).toBeInTheDocument();
    expect(within(main).getAllByRole('button').map((b) => b.textContent)).toEqual(expect.arrayContaining(['New Project…', 'Open Folder…']));
  });

  it('chooses the parent in the native chooser, asks for a name, creates through the host, then opens the new folder through the ordinary validated open', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'] } });
    const d = await begin();
    expect(within(d).getByText('/fixture/parent')).toBeInTheDocument(); // shown for the person; only a token goes back to the host
    expect(within(d).getByRole('button', { name: 'Create project' })).toBeDisabled(); // no name yet
    typeName(d, 'my-app');
    create(d);
    await screen.findByRole('navigation', { name: 'Destinations' }); // the workspace is open
    expect(host.workspaceCalls).toEqual(['chooseProjectParent', 'createProject:my-app', 'remember:/fixture/new/my-app']);
    expect(host.calls.filter((c) => c.operation === 'workspace.describe')).toEqual([{ operation: 'workspace.describe', workspace: '/fixture/new/my-app' }]);
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
    expect(host.calls.some((c) => (c as { operation: string }).operation === 'workspace.create')).toBe(false); // never through the generic query channel
  });

  it('cancelling the native chooser changes nothing and shows no dialog', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: [null] } });
    fireEvent.click(await screen.findByRole('button', { name: /^New Project…/ }));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['chooseProjectParent']));
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
    expect(screen.getByRole('main', { name: 'Welcome' })).toBeInTheDocument();
  });

  it('Cancel and Escape close the dialog without asking the host to create anything', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent', '/fixture/parent'] } });
    let d = await begin();
    fireEvent.click(within(d).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
    d = await begin();
    fireEvent.keyDown(d, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
    expect(host.workspaceCalls.filter((c) => c.startsWith('createProject'))).toEqual([]);
  });

  it('Enter in the name field creates; the field starts focused', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'] } });
    const d = await begin();
    const field = within(d).getByRole('textbox', { name: 'Project name' });
    await waitFor(() => expect(field).toHaveFocus());
    typeName(d, 'enter-app');
    fireEvent.keyDown(field, { key: 'Enter' });
    await waitFor(() => expect(host.workspaceCalls).toContain('createProject:enter-app'));
  });

  it.each([
    ['already_exists', 'ALREADY_EXISTS_CODE', /already exists/, /Choose a different name/],
    ['invalid_name', 'INVALID_REQUEST', /not valid/, /plain folder name/],
    ['permission_denied', 'WORKSPACE_UNAVAILABLE', /denied/, /another parent folder/],
    ['parent_not_found', 'WORKSPACE_UNAVAILABLE', /gone/, /Choose it again/],
    ['create_failed', 'WORKSPACE_UNAVAILABLE', /could not be created/, /another location/],
  ] as const)('a refusal (%s) is shown with Kriya\'s own message and code, keeps the dialog open, and nothing is opened', async (reason, code, message, advice) => {
    const text: Record<string, string> = { already_exists: 'my-app already exists in /fixture/parent', invalid_name: 'the project name is not valid: it must not start with a dot', permission_denied: 'the folder cannot be created: permission denied', parent_not_found: 'the parent folder is gone', create_failed: 'the folder could not be created' };
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'], projectRefusals: [{ code, message: text[reason]!, reason }] } });
    const d = await begin();
    typeName(d, 'my-app');
    create(d);
    const alert = await within(d).findByRole('alert');
    expect(alert).toHaveTextContent('The project was not created');
    expect(alert).toHaveTextContent(message);
    expect(alert).toHaveTextContent(advice);
    expect(alert).toHaveTextContent(`${code}: ${reason}`);
    expect(screen.queryByRole('navigation', { name: 'Destinations' })).toBeNull(); // still on Welcome
    expect(host.workspaceCalls.some((c) => c.startsWith('remember:'))).toBe(false);
    // the person can correct the name and try again in the same dialog
    typeName(d, 'my-app-2');
    create(d);
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(host.workspaceCalls).toContain('remember:/fixture/new/my-app-2');
  });

  it('a double click creates exactly once', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'] } });
    const d = await begin();
    typeName(d, 'once');
    const button = within(d).getByRole('button', { name: 'Create project' });
    fireEvent.click(button);
    fireEvent.click(button);
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(host.workspaceCalls.filter((c) => c === 'createProject:once')).toHaveLength(1);
  });

  it('Unicode and spaces in a name travel verbatim to the host (Kriya validates; the renderer does not rewrite)', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'] } });
    const d = await begin();
    typeName(d, 'プロジェクト one');
    create(d);
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(host.workspaceCalls).toContain('createProject:プロジェクト one');
  });

  it('a created folder that Kriya cannot open is reported as created-but-not-opened, with Kriya\'s code, and no workspace opens', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'] } });
    const original = host.query.bind(host);
    host.query = async (request) => (request.operation === 'workspace.describe' ? { schema_version: 1, operation: 'workspace.describe', request_id: 'x', observed_at: 't', source: null, consistency: null, data: null, error: { code: 'WORKSPACE_UNAVAILABLE', message: 'the folder cannot be read: /fixture/new/x', database_state: null, reason: 'unreadable' } } as never : original(request));
    const d = await begin();
    typeName(d, 'x');
    create(d);
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The project folder was created at /fixture/new/x, but it could not be opened');
    expect(alert).toHaveTextContent('WORKSPACE_UNAVAILABLE');
    expect(screen.queryByRole('navigation', { name: 'Destinations' })).toBeNull();
  });

  it('is refused while a run owns the workspace: the chooser is not shown and the reason is stated', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', projectParents: ['/fixture/parent'], execution: { outcome: 'success' } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Do a thing' } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    await waitFor(() => expect(host.exec!.active()).toBe(true));
    // the toolbar menu is disabled while a run is active; the menu command (Cmd+N) still reaches the provider, which the host refuses
    act(() => host.menu('newProject'));
    const alert = await screen.findByText(/A run is in progress; a project cannot be created until it ends/);
    expect(alert).toBeInTheDocument();
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
  });
});

describe('New Project from the open workspace', () => {
  it('File > New Project (menu command) and the toolbar menu both start the same flow, and the new project replaces the open workspace only after Kriya opened it', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', projectParents: ['/fixture/parent', '/fixture/parent'] } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    act(() => host.menu('newProject'));
    let d = await dialog();
    fireEvent.click(within(d).getByRole('button', { name: 'Cancel' }));
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(within(await screen.findByRole('menu', { name: 'Workspace menu' })).getByRole('menuitem', { name: /^New Project…/ }));
    d = await dialog();
    typeName(d, 'second');
    create(d);
    await waitFor(() => expect(host.workspaceCalls).toContain('remember:/fixture/new/second'));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Switch workspace' })).toHaveTextContent('second'));
  });
});

describe('the provider API itself', () => {
  it('two createProject calls in the SAME tick reach the host once (the DOM button being disabled is not what guarantees this)', async () => {
    const { WorkspaceProvider, useWorkspace } = await import('../core');
    const { HostProvider } = await import('../providers/hostProvider');
    const { FixtureHost } = await import('../providers/fixture');
    const host = new FixtureHost({ workspace: null, projectParents: ['/fixture/parent'] });
    const provider = new HostProvider(host, 'fixture');
    let api!: ReturnType<typeof useWorkspace>;
    const Probe = () => { api = useWorkspace(); return null; };
    const { render } = await import('@testing-library/react');
    render(<WorkspaceProvider provider={provider}><Probe /></WorkspaceProvider>);
    await waitFor(() => expect(api.ready).toBe(true));
    await act(async () => { await api.beginProject(); });
    await waitFor(() => expect(api.project).not.toBeNull());
    await act(async () => { await Promise.all([api.createProject('same-tick'), api.createProject('same-tick')]); });
    expect(host.workspaceCalls.filter((c) => c === 'createProject:same-tick')).toHaveLength(1);
  });
});

describe('races', () => {
  it('a second New Project request while the native chooser is still open is ignored, not queued', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent', '/fixture/parent'] } });
    await screen.findByRole('main', { name: 'Welcome' });
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const original = host.workspace.chooseProjectParent!.bind(host.workspace);
    host.workspace.chooseProjectParent = async () => { await gate; return original(); };
    const button = screen.getByRole('button', { name: /^New Project…/ });
    fireEvent.click(button);
    act(() => host.menu('newProject'));
    fireEvent.click(button);
    release();
    await dialog();
    expect(host.workspaceCalls.filter((c) => c === 'chooseProjectParent')).toHaveLength(1);
  });
  it('a refused open leaves a New Project dialog untouched; a successful open removes it', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, projectParents: ['/fixture/parent'], choices: ['/fixture/missing', '/fixture/empty'] } });
    await begin();
    host.menu('openFolder'); // the chooser returns a folder Kriya refuses
    await waitFor(() => expect(host.workspaceCalls).toContain('choose'));
    await screen.findByRole('alert');
    expect(screen.getByRole('dialog', { name: 'New Project' })).toBeInTheDocument();
    host.menu('openFolder'); // now a folder Kriya accepts
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(screen.queryByRole('dialog', { name: 'New Project' })).toBeNull();
  });
});
