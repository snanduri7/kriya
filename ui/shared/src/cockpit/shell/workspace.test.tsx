/** Open Folder / workspace lifecycle through the real shell over the fixture host (scripted native chooser). The host returns PATHS; every fact on screen
 * is Kriya's workspace.describe answer; nothing is classified greenfield or brownfield; opening a folder only reads. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

const toolbar = () => screen.getByRole('banner');
const welcome = () => screen.findByRole('main', { name: 'Welcome' });
const openFromWelcome = async () => { fireEvent.click(await screen.findByRole('button', { name: 'Open Folder…' })); };
const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const asked = (host: { calls: Array<{ operation: string }> }) => host.calls.map((c) => c.operation);

describe('welcome', () => {
  it('with no workspace open the person sees Kriya, Open Folder and the remembered folders - and no runs, no snapshot control, no module content', async () => {
    renderWorkspaceShell({ fixture: { workspace: null, recent: ['/fixture/dirty', '/fixture/empty'] } });
    const main = await welcome();
    expect(within(main).getByRole('heading', { name: 'Kriya' })).toBeInTheDocument();
    expect(within(main).getByRole('button', { name: 'Open Folder…' })).toBeInTheDocument();
    const recent = within(main).getByRole('region', { name: 'Recent workspaces' });
    expect(within(recent).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringContaining('dirty'), expect.stringContaining('empty')]);
    expect(screen.queryByRole('navigation', { name: 'Destinations' })).toBeNull();
    expect(document.querySelector('.ck-list')).toBeNull();
    expect(screen.queryByText(/Acquire snapshot/)).toBeNull();
    expect(screen.queryByText(/Greenfield|Create a utility library/)).toBeNull(); // no fixture run is shown
  });

  it('is calm without recents: no empty recent section', async () => {
    renderWorkspaceShell({ fixture: { workspace: null } });
    await welcome();
    expect(screen.queryByRole('region', { name: 'Recent workspaces' })).toBeNull();
  });
});

describe('Open Folder', () => {
  it('chooser -> Kriya describes the picked path -> the host remembers it -> the cockpit shows that workspace\'s runs, and only reads happened', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: ['/fixture/workspace'] } });
    await openFromWelcome();
    expect(await screen.findByRole('navigation', { name: 'Destinations' })).toBeInTheDocument();
    expect(await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(host.workspaceCalls).toEqual(['choose', 'remember:/fixture/workspace']);
    const described = host.calls.filter((c) => c.operation === 'workspace.describe');
    expect(described).toEqual([{ operation: 'workspace.describe', workspace: '/fixture/workspace' }]);
    expect(asked(host)).not.toContain('snapshot.acquire');
    expect(asked(host)).not.toContain('snapshot.prune');
    expect(host.calls.find((c) => c.operation === 'history.list')).toMatchObject({ workspace: '/fixture/workspace' }); // Runs asked for THIS workspace's runs
    expect(await host.getSetting('workspacePath')).toBe('/fixture/workspace');
  });

  it('the toolbar names the workspace and shows no path', async () => {
    renderWorkspaceShell({ fixture: { workspace: null, choices: ['/fixture/dirty'] } });
    await openFromWelcome();
    const switcher = await screen.findByRole('button', { name: 'Switch workspace' });
    expect(switcher.textContent).toContain('dirty');
    expect(toolbar().textContent).not.toMatch(/\/fixture/);
  });

  it('cancelling the chooser is normal: nothing is asked of Kriya, nothing is remembered, the welcome screen stays', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: [null] } });
    await openFromWelcome();
    await waitFor(() => expect(host.workspaceCalls).toEqual(['choose']));
    await welcome();
    expect(asked(host)).not.toContain('workspace.describe');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(await host.getSetting('workspacePath')).toBeNull();
  });

  it('cancelling while a workspace is open leaves it exactly as it was (selection included)', async () => {
    const { host, store } = renderWorkspaceShell({ fixture: { choices: [null] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    act(() => store.dispatch({ type: 'selectRun', runId: 'fx-greenfield-success' }));
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Open Folder…/ }));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['choose']));
    expect(store.getState().runId).toBe('fx-greenfield-success');
    expect(screen.getByRole('button', { name: 'Switch workspace' }).textContent).toContain('workspace');
  });

  it('the application menu command opens the same chooser', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: ['/fixture/empty'] } });
    await welcome();
    act(() => host.menu('openFolder'));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['choose', 'remember:/fixture/empty']));
    expect(await screen.findByRole('navigation', { name: 'Destinations' })).toBeInTheDocument();
  });

  it('the toolbar selector invokes the same action and lists remembered folders other than the open one', async () => {
    const { host } = renderWorkspaceShell({ fixture: { recent: ['/fixture/workspace', '/fixture/dirty'], choices: ['/fixture/empty'] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    const menu = await screen.findByRole('menu', { name: 'Workspace menu' });
    expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent?.replace(/\s+/g, ' ').trim())).toEqual([expect.stringMatching(/^New Project…/), expect.stringMatching(/^Open Folder…/), 'dirty', 'Close workspace']);
    fireEvent.click(within(menu).getByRole('menuitem', { name: /Open Folder…/ }));
    await waitFor(() => expect(host.workspaceCalls).toContain('remember:/fixture/empty'));
  });
});

describe('Kriya, not the UI, says what the folder is - empty, existing, non-Git and dirty use one lifecycle', () => {
  const FACTS = [
    ['/fixture/empty', [/Not a Git repository/, /Name\s*empty/]],
    ['/fixture/plain', [/Not a Git repository/, /Workspace \.kriya directory\s*present/]],
    ['/fixture/workspace', [/Git checkout/, /Branch\s*main/, /b2b5fc411394e307cf9422d8fbb5fed511bba8ce/, /No changes reported by Git/]],
    ['/fixture/dirty', [/Git checkout/, /Branch\s*feature\/clamp/, /3 changed or untracked entries reported by Git/]],
    ['/fixture/nested', [/Inside a Git repository rooted at \/fixture\/workspace/, /Branch\s*Not recorded/, /Working tree\s*Not recorded/]],
    ['/fixture/blocked', [/RECOVERY_AVAILABLE/]],
  ] as const;

  it.each(FACTS)('%s', async (path, patterns) => {
    renderWorkspaceShell({ fixture: { workspace: null, choices: [path] } });
    await openFromWelcome();
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Workspace');
    const panel = (await screen.findByRole('heading', { name: 'Workspace' })).closest('section')!;
    const text = panel.textContent ?? '';
    for (const p of patterns) expect(text).toMatch(p);
    expect(text).toContain(path); // the exact path belongs to this page
    expect(text).not.toMatch(/greenfield|brownfield|new project|existing project|empty folder/i); // the UI classifies nothing
    expect(text).toMatch(/Nothing was written to it and no run was started/);
  });

  it('the same fields are shown for every kind of folder', async () => {
    const labels: string[][] = [];
    for (const path of ['/fixture/empty', '/fixture/dirty', '/fixture/nested']) {
      const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: [path] } });
      void host;
      await openFromWelcome();
      await screen.findByRole('navigation', { name: 'Destinations' });
      goTo('Workspace');
      const panel = (await screen.findByRole('heading', { name: 'Workspace' })).closest('section')!;
      labels.push([...panel.querySelectorAll('dt')].map((d) => d.textContent ?? ''));
      document.body.innerHTML = '';
    }
    expect(labels[1]).toEqual(labels[0]);
    expect(labels[2]).toEqual(labels[0]);
  });
});

describe('a folder Kriya refuses', () => {
  it('a missing folder is a typed notice with the code and Kriya\'s message; nothing is remembered; the welcome screen stays', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: ['/fixture/missing'] } });
    await openFromWelcome();
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Could not open missing');
    expect(alert.textContent).toContain('no such folder: /fixture/missing');
    expect(alert.textContent).toContain('WORKSPACE_UNAVAILABLE');
    expect(host.workspaceCalls).toEqual(['choose']);
    expect(await host.getSetting('workspacePath')).toBeNull();
    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('while a workspace is open, a refused folder leaves it open and unchanged', async () => {
    const { host, store } = renderWorkspaceShell({ fixture: { choices: ['/fixture/missing'] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    act(() => store.dispatch({ type: 'selectRun', runId: 'fx-greenfield-success' }));
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Open Folder…/ }));
    await waitFor(() => expect(asked(host).filter((o) => o === 'workspace.describe')).toHaveLength(2));
    expect(host.workspaceCalls).toEqual(['choose']);
    expect(store.getState().runId).toBe('fx-greenfield-success');
    expect(await host.getSetting('workspacePath')).toBe('/fixture/workspace');
    // and the person is told, in Kriya's words, which folder was refused and that the open one is unchanged
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('Could not open missing; workspace is still open');
    expect(alert.textContent).toContain('no such folder: /fixture/missing');
    expect(alert.textContent).toContain('WORKSPACE_UNAVAILABLE');
    fireEvent.click(within(alert).getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('a stale recent entry fails cleanly through the same validation and can be removed', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, recent: ['/fixture/gone', '/fixture/empty'] } });
    fireEvent.click(await screen.findByRole('button', { name: 'Open gone' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toContain('no such folder: /fixture/gone');
    expect(host.calls.filter((c) => c.operation === 'workspace.describe')).toEqual([{ operation: 'workspace.describe', workspace: '/fixture/gone' }]);
    expect(host.workspaceCalls).toEqual([]);
    fireEvent.click(within(alert).getByRole('button', { name: 'Remove from recent' }));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['forget:/fixture/gone']));
    expect(screen.queryByRole('button', { name: 'Open gone' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Open empty' })).toBeInTheDocument();
  });

  it('a failing "Remove from recent" shows a typed notice and leaves the list untouched - never an unhandled rejection (review P3-7)', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, recent: ['/fixture/gone', '/fixture/empty'] } });
    (host.workspace as { forget: (p: string) => Promise<string[]> }).forget = async () => { throw new Error('the settings file cannot be written'); };
    fireEvent.click(await screen.findByRole('button', { name: 'Open gone' }));
    const alert = await screen.findByRole('alert');
    fireEvent.click(within(alert).getByRole('button', { name: 'Remove from recent' }));
    await waitFor(() => expect(screen.getByRole('alert').textContent).toContain('the settings file cannot be written'));
    expect(screen.getByRole('button', { name: 'Open empty' })).toBeInTheDocument();
  });

  it('a remembered folder is not trusted: opening it asks Kriya every time', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, recent: ['/fixture/empty'] } });
    fireEvent.click(await screen.findByRole('button', { name: 'Open empty' }));
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(asked(host).filter((o) => o === 'workspace.describe')).toHaveLength(1);
  });
});

describe('switching workspace', () => {
  it('A -> B clears the previous workspace\'s selection and runs, B -> A restores A\'s runs from Kriya (nothing carried over), and the pinned snapshot is kept', async () => {
    const { host, store } = renderWorkspaceShell({ fixture: { choices: ['/fixture/empty'], recent: ['/fixture/workspace'] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    act(() => store.dispatch({ type: 'selectRun', runId: 'fx-greenfield-success' }));
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: /Open Folder…/ }));
    expect(await screen.findByText('No run is recorded for this workspace')).toBeInTheDocument();
    expect(store.getState().runId).toBeNull();
    expect(screen.queryByText(/Greenfield: |Create a utility library/)).toBeNull();
    expect(screen.getByRole('button', { name: 'Switch workspace' }).textContent).toContain('empty');
    // back to A through the remembered list
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'workspace' }));
    expect(await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 })).toBeInTheDocument();
    expect(store.getState().runId).toBeNull();
    const lists = host.calls.filter((c) => c.operation === 'history.list').map((c) => (c as { workspace?: string }).workspace);
    expect(lists).toEqual(expect.arrayContaining(['/fixture/workspace', '/fixture/empty']));
    expect(asked(host)).not.toContain('snapshot.acquire');
    expect(asked(host).filter((o) => o === 'snapshot.verify')).toHaveLength(1); // one pin for the whole session
  });

  it('Close workspace returns to the welcome screen and keeps the remembered folders', async () => {
    const { host } = renderWorkspaceShell({ fixture: { recent: ['/fixture/workspace'] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Close workspace' }));
    const main = await welcome();
    expect(within(main).getByRole('button', { name: 'Open workspace' })).toBeInTheDocument();
    expect(await host.getSetting('workspacePath')).toBeNull();
  });
});

describe('the Workspace page lives in the registry like any module', () => {
  it('shows the pinned snapshot beside Kriya\'s facts, and the identity as a prefix with the whole value on hover', async () => {
    renderWorkspaceShell({ fixture: { choices: [] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    goTo('Workspace');
    const panel = (await screen.findByRole('heading', { name: 'Workspace' })).closest('section')!;
    expect(panel.textContent).toMatch(/Pinned snapshot\s*2026/);
    expect(panel.querySelector('[title^="abababab"]')).not.toBeNull();
  });
});

describe('same-named folders are told apart where the name alone would be ambiguous', () => {
  it('the toolbar list adds the parent folder only for a duplicate name', async () => {
    renderWorkspaceShell({ fixture: { recent: ['/fixture/workspace', '/other/dirty', '/fixture/dirty', '/fixture/empty'] } });
    await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 });
    fireEvent.click(screen.getByRole('button', { name: 'Switch workspace' }));
    const menu = await screen.findByRole('menu', { name: 'Workspace menu' });
    expect(within(menu).getAllByRole('menuitem').map((i) => i.textContent?.replace(/\s+/g, ' ').trim())).toEqual([expect.stringMatching(/^New Project…/), expect.stringMatching(/^Open Folder…/), 'dirty — other', 'dirty — fixture', 'empty', 'Close workspace']);
  });
});
