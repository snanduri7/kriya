/** Small productivity actions: Copy run ID / workspace path / failure details, and Reveal in Finder. They put text on the clipboard through the host, name no path to the host, and never run a command. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));

describe('Copy', () => {
  it('copies the workspace path from the Workspace page and says it worked', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.click(await screen.findByRole('button', { name: 'Copy workspace path' }));
    await waitFor(() => expect(host.copied).toEqual(['/fixture/workspace']));
    expect(await screen.findByText('Copied')).toBeInTheDocument();
  });
  it('copies the CANONICAL workspace path Kriya reported, not the spelling that was chosen', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, recent: ['/fixture/workspace'] } });
    const original = host.query.bind(host);
    host.query = async (request) => { const env = await original(request); return request.operation === 'workspace.describe' && env.data ? ({ ...env, data: { ...(env.data as object), requested: '/fixture/link-to-workspace' } } as typeof env) : env; };
    fireEvent.click(await screen.findByRole('button', { name: 'Open workspace' }));
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.click(await screen.findByRole('button', { name: 'Copy workspace path' }));
    await waitFor(() => expect(host.copied).toEqual(['/fixture/workspace']));
  });
  it('copies a run\'s ID from Overview', async () => {
    const { host } = renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    fireEvent.click((await screen.findAllByRole('button', { name: /Succeeded/ }))[0]!);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy run ID' }));
    await waitFor(() => expect(host.copied).toHaveLength(1));
    expect(host.copied[0]).toMatch(/^fx-/);
  });
  it('a failing clipboard is reported, not hidden', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    host.copyToClipboard = async () => { throw new Error('denied'); };
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.click(await screen.findByRole('button', { name: 'Copy workspace path' }));
    expect(await screen.findByText('Could not copy')).toBeInTheDocument();
  });
  const finishRun = async (outcome: 'failure' | 'success') => {
    const m = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Do it' } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    const bar = await screen.findByRole('region', { name: 'Current run' });
    act(() => m.host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', outcome));
    return { ...m, bar };
  };
  it('a run that did not succeed offers "Copy details" with the label, the detail, Kriya\'s report and the source', async () => {
    const failed = await finishRun('failure');
    fireEvent.click(within(failed.bar).getByRole('button', { name: 'Copy failure details' }));
    await waitFor(() => expect(failed.host.copied).toHaveLength(1));
    expect(failed.host.copied[0]).toMatch(/^Run 2026\d{4}T\d{6}-[0-9a-f]{8}: Run failed\n/);
    expect(failed.host.copied[0]).toContain('Source:');
  });
  it('a successful run offers no failure details to copy', async () => {
    const ok = await finishRun('success');
    expect(within(ok.bar).queryByRole('button', { name: 'Copy failure details' })).toBeNull();
    expect(within(ok.bar).getByRole('button', { name: 'Copy run ID' })).toBeInTheDocument();
  });
});

describe('Reveal in Finder', () => {
  it('asks the host to reveal the folder IT has open (no path is sent) and reports a refusal', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    fireEvent.click(await screen.findByRole('button', { name: 'Reveal in Finder' }));
    await waitFor(() => expect(host.workspaceCalls).toContain('reveal'));
    host.workspace.revealWorkspace = async () => ({ revealed: false, reason: 'the folder is no longer there' });
    fireEvent.click(screen.getByRole('button', { name: 'Reveal in Finder' }));
    expect(await screen.findByText('Could not reveal: the folder is no longer there')).toBeInTheDocument();
  });
  it('is absent on a host that cannot reveal', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' } });
    (host.workspace as { revealWorkspace?: unknown }).revealWorkspace = undefined;
    await screen.findByRole('navigation', { name: 'Destinations' });
    goTo('Project');
    await screen.findByRole('button', { name: 'Copy workspace path' });
    expect(screen.queryByRole('button', { name: 'Reveal in Finder' })).toBeNull();
  });
});
