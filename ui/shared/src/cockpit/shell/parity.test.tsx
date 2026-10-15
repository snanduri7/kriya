/** M1 capabilities that the cockpit must cover before the inspector is retired (GUI M7): open in editor, copy, editor setting, manual refresh,
 * snapshot choice and run filtering - each through the cockpit's own modules over the scripted host. */
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';
import { openGreenfieldFromAllRuns } from './recent_runs_helpers';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const asked = (host: { calls: Array<{ operation: string }> }, op: string) => host.calls.filter((c) => c.operation === op).length;

async function openRun(fixture = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', evidenceContent: true, ...fixture } });
  await openGreenfieldFromAllRuns();
  return mounted;
}

describe('Changes: open in editor and copy', () => {
  it('opens the recorded path in the configured editor through the host and shows the host\'s answer', async () => {
    const { host } = await openRun();
    fireEvent.click(await screen.findByRole('tab', { name: 'Changes' }));
    const files = await screen.findByRole('list', { name: 'Changed files' });
    fireEvent.click(within(files).getAllByRole('button')[0]!);
    fireEvent.click(await screen.findByRole('button', { name: 'Open in editor' }));
    await waitFor(() => expect(host.ideCalls).toHaveLength(1));
    expect(host.ideCalls[0]!.path).toMatch(/\S/);
    expect(await screen.findByText(/Opened: fixture: code --goto/)).toBeInTheDocument();
  });

  it('copies the authorized diff text, and only after Kriya served it', async () => {
    const { host } = await openRun();
    fireEvent.click(await screen.findByRole('tab', { name: 'Changes' }));
    const files = await screen.findByRole('list', { name: 'Changed files' });
    fireEvent.click(within(files).getAllByRole('button')[0]!);
    expect(screen.queryByRole('button', { name: 'Copy diff' })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: 'Show diff' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Copy diff' }));
    await waitFor(() => expect(host.copied).toHaveLength(1));
    expect(host.copied[0]).toContain('@@');
  });
});

describe('Settings: editor', () => {
  it('chooses the editor through the host setting (narrow product setting, not an environment editor)', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Settings');
    fireEvent.change(await screen.findByRole('combobox', { name: 'Editor' }), { target: { value: 'intellij' } });
    await waitFor(async () => expect(await host.getSetting('editor')).toBe('intellij'));
    expect(await screen.findByText('Editor saved.')).toBeInTheDocument();
    expect(screen.queryByLabelText(/path|environment|PATH/i)).toBeNull();
  });
});

describe('Runs: refresh and filter', () => {
  it('Refresh runs is the explicit acquisition request', async () => {
    const { host } = renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/workspace' } });
    fireEvent.click(await screen.findByRole('button', { name: 'Refresh runs' }));
    await waitFor(() => expect(asked(host, 'snapshot.acquire')).toBe(1));
  });
  it('a long run list can be filtered by goal or status text, and clearing restores it', async () => {
    renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/workspace' } });
    const box = await screen.findByRole('searchbox', { name: 'Filter runs' }, { timeout: 3000 });
    const all = document.querySelectorAll('.ck-list button').length;
    fireEvent.change(box, { target: { value: 'greenfield' } });
    await waitFor(() => expect(document.querySelectorAll('.ck-list button').length).toBeLessThan(all));
    fireEvent.change(box, { target: { value: '' } });
    await waitFor(() => expect(document.querySelectorAll('.ck-list button').length).toBe(all));
  });
});

describe('Workspace: snapshot choice', () => {
  it('lists the published snapshots; choosing one verifies THAT id before anything is read from it (a refused one is never pinned)', async () => {
    const other = '20261009T090000000000Z-f1c70000';
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', extraSnapshotIds: [other] } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    const picker = await screen.findByRole('combobox', { name: 'Pinned snapshot' });
    expect(within(picker).getAllByRole('option')).toHaveLength(2);
    fireEvent.change(picker, { target: { value: other } });
    await waitFor(() => expect(host.calls.some((c) => c.operation === 'snapshot.verify' && (c as { snapshot_id?: string }).snapshot_id === other)).toBe(true));
    const readsAfter = host.calls.filter((c) => c.operation === 'history.list' && (c as { snapshot_id?: string }).snapshot_id === other);
    expect(readsAfter).toHaveLength(0);
  });
});
