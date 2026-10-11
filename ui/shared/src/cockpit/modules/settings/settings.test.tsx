import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule, renderWorkspaceShell } from '../../testing/harness';
import { settingsModule } from '.';

describe('Settings', () => {
  it('shows the host-owned values read-only, offers no way to edit the executable or configuration directory, and no content-visibility control (Kriya owns it)', async () => {
    const { provider } = renderModule(settingsModule);
    await screen.findByText('Settings');
    for (const label of ['Configuration directory', 'Kriya executable']) {
      expect(screen.queryByRole('textbox', { name: label })).toBeNull();
      expect(screen.getByLabelText(label)).toHaveTextContent(/Managed by the host/);
    }
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryAllByRole('textbox')).toHaveLength(0); // no free-text field of any name in production Settings
    expect(screen.queryByLabelText('Workspace')).toBeNull(); // the workspace is chosen with Open Folder, never typed
    expect(screen.queryByText(/content visibility/i)).toBeNull();
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Editor' })).toBeInTheDocument()); // the harmless preference stays
    expect(await provider.getSetting('configDirectory')).toBeNull();
  });
  it('the module never calls setSetting for a host-owned key (source guard)', async () => {
    const calls: string[] = [];
    const { provider } = renderModule(settingsModule);
    const original = provider.setSetting.bind(provider);
    provider.setSetting = (async (key: never, value: never) => { calls.push(String(key)); return original(key, value); }) as typeof provider.setSetting;
    await screen.findByText('Settings');
    expect(calls).toEqual([]);
  });
});

describe('Settings: recents and content note', () => {
  it('lists remembered folders other than the open one, Forget removes only that entry from the list, Forget all clears them, and nothing is deleted', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', recent: ['/fixture/workspace', '/fixture/dirty', '/fixture/empty'] } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name: 'Settings' }));
    const recents = await screen.findByRole('region', { name: 'Recent workspaces' });
    expect(within(recents).getAllByRole('listitem').map((li) => li.textContent)).toEqual([expect.stringContaining('/fixture/dirty'), expect.stringContaining('/fixture/empty')]);
    fireEvent.click(within(recents).getByRole('button', { name: 'Forget /fixture/dirty' }));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['forget:/fixture/dirty']));
    await waitFor(() => expect(within(recents).queryByText('/fixture/dirty')).toBeNull());
    fireEvent.click(within(recents).getByRole('button', { name: 'Forget all' }));
    await waitFor(() => expect(host.workspaceCalls).toEqual(['forget:/fixture/dirty', 'forget:/fixture/empty']));
    expect(await within(recents).findByText(/None besides the open workspace/)).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Destinations' })).toBeInTheDocument(); // the open workspace is untouched
  });
  it('states whether stored diffs are shown and that the operator, not this app, decides', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', evidenceContent: false } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name: 'Settings' }));
    const row = (await screen.findByText('Stored diffs and prompts')).closest('div') as HTMLElement;
    expect(row).toHaveTextContent('Hidden');
    expect(row).toHaveTextContent('this application cannot change it');
    expect(within(row).queryAllByRole('button')).toHaveLength(0);
  });
});
