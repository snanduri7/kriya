/**
 * T2: the Project Workspace. A project bar (where am I, Open in Finder / Open in Editor, four readiness chips, Capabilities, Settings), ONE main pane that shows exactly one primary state, an Inspector with
 * five tabs one click apart (only the selected one mounted), and an Activity dock. The width decides the treatment (the shell's measured width, passed to the tree): jsdom does no layout, so the
 * tests render the shell at 1500, 1000 and 800 px. The panels are the same components over the same Kriya answers; these tests pin the arrangement, not new facts.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExecutionPreflight, ProjectMemory } from '../../model/kup';
import { localClock, shouldStickToBottom } from '../core';
import { capabilitySummary } from '../modules/workspace/CapabilityStrip';
import { renderWorkspaceShell } from '../testing/harness';

type Row = NonNullable<ExecutionPreflight['health']>[number];
const row = (id: string, state: Row['state']): Row => ({ id, label: id, state, reason_code: `${id.toUpperCase()}_CODE`, detail: `${id} detail`, evidence: ['autonomy.x'] });
const memory = (): ProjectMemory => ({
  schema_version: 1, repository: 'r', store_state: 'OK', store_detail: null, rejected: [], runs: [],
  counts: { current: 1, stale: 0, rejected_total: 0, learned_last_run: 1, reused_last_run: 0, rejected_last_run: 0 },
  facts: [{ id: 'build_system', kind: 'build_system', label: 'Build system', value: 'Maven', status: 'CURRENT', stale_reason: null, evidence: [{ kind: 'file', path: 'pom.xml', sha256: 'a'.repeat(64) }], learned_run_id: 'run-1', learned_at: '2026-11-06T10:10:10Z', confirmed_runs: 1, reused_runs: 0, why: 'Read from pom.xml.' }] });
const HEALTH = [row('security', 'AVAILABLE'), row('lsp', 'UNAVAILABLE'), row('lookup', 'DISABLED'), row('verification', 'AVAILABLE'), row('recovery', 'AVAILABLE')];
const GOAL = 'Add a clamp(value, low, high) function with tests.';

async function open(over: { width?: number; execution?: Record<string, unknown> } = {}) {
  const mounted = renderWorkspaceShell({ width: over.width ?? 1500, fixture: { workspace: '/fixture/workspace', execution: { outcome: 'success', health: HEALTH, memory: memory(), security: { demonstrations: [] }, ...over.execution } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  await screen.findByText('What do you want Kriya to do?');
  return mounted;
}
const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const tabs = () => within(screen.getByRole('tablist', { name: 'Inspector tabs' })).getAllByRole('tab');

describe('the project bar', () => {
  it('names the project and its path, offers Open in Finder, Open in Editor and Settings, and shows four readiness chips from Kriya\'s own answers', async () => {
    await open();
    const bar = screen.getByRole('group', { name: 'Project' });
    expect(within(bar).getByRole('heading', { level: 1 })).toHaveTextContent('workspace');
    expect(within(bar).getByText('/fixture/workspace')).toBeInTheDocument();
    for (const name of ['Open in Finder', 'Open in Editor', /Settings/]) expect(within(bar).getByRole('button', { name })).toBeInTheDocument();
    const chips = await within(bar).findByTestId('readiness-chips');
    expect([...chips.querySelectorAll('[data-chip]')].map((c) => c.getAttribute('data-chip'))).toEqual(['git', 'configuration', 'model', 'security']);   // a healthy folder needs no folder chip
    expect(chips.querySelector('[data-chip="security"]')).toHaveTextContent('Security Available');
  });

  it('Settings goes to the Settings page; a chip opens the Inspector on Project', async () => {
    await open();
    fireEvent.click(within(screen.getByRole('group', { name: 'Project' })).getByRole('button', { name: /Settings/ }));
    await waitFor(() => expect(document.querySelector('.ck-shell')?.getAttribute('data-page')).toBe('settings'));
    goTo('Project');
    await screen.findByText('What do you want Kriya to do?');
    fireEvent.click(await screen.findByRole('button', { name: /Git/ }));
    expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute('aria-selected', 'true');
  });
});

describe('Capabilities', () => {
  it('is ONE collapsed control with counted words, not eleven cells; expanding lists every cell with its reason and evidence; Escape and an outside click close it', async () => {
    await open();
    expect(screen.queryByTestId('capability-strip')).toBeNull();
    const toggle = await screen.findByTestId('capabilities-toggle');
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('capabilities-summary').textContent).toMatch(/\d+ ready( · \d+ degraded)? · 1 unavailable · Lookup disabled/);
    fireEvent.click(toggle);
    const panel = await screen.findByRole('dialog', { name: 'Capabilities' });
    expect(within(panel).getByTestId('capability-strip')).toBeInTheDocument();
    expect(within(panel).getByText(/lsp: lsp detail \(LSP_CODE; evidence: autonomy\.x\)/)).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Capabilities' })).toBeNull());
    fireEvent.click(toggle);
    await screen.findByRole('dialog', { name: 'Capabilities' });
    fireEvent.mouseDown(document.body);
    await waitFor(() => expect(screen.queryByRole('dialog', { name: 'Capabilities' })).toBeNull());
  });

  it('the summary only COUNTS the cells\' own tones - ok = ready, warn = degraded, bad = unavailable - and names the lookup state in its own word', () => {
    expect(capabilitySummary([{ id: 'a', tone: 'ok', status: 'Available' }, { id: 'b', tone: 'ok', status: 'Available' }, { id: 'c', tone: 'warn', status: 'Degraded' }, { id: 'd', tone: 'bad', status: 'Unavailable' }, { id: 'lookup', tone: 'neutral', status: 'Disabled' }])).toBe('2 ready · 1 degraded · 1 unavailable · Lookup disabled');
    expect(capabilitySummary([{ id: 'a', tone: 'ok', status: 'Available' }])).toBe('1 ready');
  });
});

describe('the Inspector', () => {
  it('has five tabs in order, opens on Runs, mounts only the selected one, and every panel is one click away', async () => {
    await open();
    expect(tabs().map((t) => t.textContent)).toEqual(['Runs', 'Memory', 'Security', 'Skills', 'Project']);
    expect(screen.getByRole('tab', { name: 'Runs' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('region', { name: 'Recent runs' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Verified Project Memory' })).toBeNull();      // not mounted while another tab is selected
    fireEvent.click(screen.getByRole('tab', { name: 'Memory' }));
    expect(await screen.findByRole('region', { name: 'Verified Project Memory' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Recent runs' })).toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Security' }));
    expect(await screen.findByTestId('security-card')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Skills' }));
    expect(await screen.findByText(/^Skills/, { selector: 'summary' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: 'Project' }));
    expect(await screen.findByTestId('project-facts')).toBeInTheDocument();
  });

  it('keeps the selected tab and the open/closed state for the session while the person visits another page', async () => {
    await open();
    fireEvent.click(screen.getByRole('tab', { name: 'Memory' }));
    goTo('Skills');
    await waitFor(() => expect(document.querySelector('.ck-shell')?.getAttribute('data-page')).toBe('skills'));
    goTo('Project');
    expect(await screen.findByRole('tab', { name: 'Memory' })).toHaveAttribute('aria-selected', 'true');
    fireEvent.click(screen.getByRole('button', { name: 'Hide Inspector' }));
    expect(screen.queryByTestId('inspector')).toBeNull();
    goTo('Skills');
    goTo('Project');
    await screen.findByText('What do you want Kriya to do?');
    expect(screen.queryByTestId('inspector')).toBeNull();                                         // the choice stuck
  });
});

describe('responsive treatment (driven by the shell\'s measured width)', () => {
  it.each([[1500, 'wide', 'side'], [1000, 'medium', 'side'], [800, 'drawer', 'closed']] as const)('at %i px the workspace is %s and the Inspector is %s', async (width, mode, inspector) => {
    await open({ width });
    const ws = screen.getByTestId('project-workspace');
    expect(ws).toHaveAttribute('data-mode', mode);
    if (inspector === 'closed') expect(screen.queryByTestId('inspector')).toBeNull();
    else expect(screen.getByTestId('inspector')).toHaveAttribute('data-mode', 'side');
    expect(screen.getByRole('main', { name: 'Current task' })).toBeInTheDocument();             // the main pane is always there
  });

  it('on a narrow window the Inspector opens as an overlay drawer from the bar and closes again', async () => {
    await open({ width: 800 });
    fireEvent.click(screen.getByRole('button', { name: 'Inspector' }));
    expect(await screen.findByTestId('inspector')).toHaveAttribute('data-mode', 'drawer');
    expect(document.querySelector('.ck-ws-inspector.overlay')).not.toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Hide Inspector' }));
    expect(screen.queryByTestId('inspector')).toBeNull();
  });
});

describe('the main pane shows ONE primary state', () => {
  it('ready -> the goal composer; running -> the current run (the composer is gone); finished -> the result; New Goal -> the composer again', async () => {
    const { host } = await open();
    const main = screen.getByRole('main', { name: 'Current task' });
    expect(within(main).getByRole('heading', { name: 'Enter a goal' })).toBeInTheDocument();
    expect(within(main).queryByRole('region', { name: 'Current run' })).toBeNull();
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    const run = await within(main).findByRole('region', { name: 'Current run' });
    expect(within(main).queryByRole('textbox', { name: 'Engineering goal' })).toBeNull();
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    expect(within(run).getByRole('heading', { level: 2 })).toHaveTextContent('Completed');
    fireEvent.click(within(run).getByRole('button', { name: 'New Goal' }));
    expect(await within(main).findByRole('heading', { name: 'Enter a goal' })).toBeInTheDocument();
    expect(within(main).queryByRole('region', { name: 'Current run' })).toBeNull();
  });

  it('Escape never cancels a running run', async () => {
    const { host } = await open();
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    const run = await screen.findByRole('region', { name: 'Current run' });
    fireEvent.keyDown(run, { key: 'Escape' });
    fireEvent.keyDown(document, { key: 'Escape' });
    await new Promise((r) => setTimeout(r, 40));
    expect(host.exec!.cancelCount).toBe(0);
    expect(within(run).getByRole('button', { name: 'Cancel Run' })).toBeEnabled();
  });
});

describe('the Activity dock', () => {
  it('is collapsed with a hint when nothing runs, opens by itself while a run is active, and stays as the person last set it', async () => {
    const { host } = await open();
    const dock = screen.getByTestId('activity-dock');
    expect(dock).toHaveAttribute('data-open', 'false');
    expect(within(dock).getByTestId('activity-latest')).toHaveTextContent(/Nothing running/);
    fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
    await screen.findByRole('region', { name: 'Current run' });
    await waitFor(() => expect(dock).toHaveAttribute('data-open', 'true'));
    expect(within(dock).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Activity', 'Kriya Log', 'Build', 'Tests']);
    fireEvent.click(within(dock).getByRole('button', { name: /ACTIVITY/ }));          // the person collapses it
    expect(dock).toHaveAttribute('data-open', 'false');
    act(() => host.exec!.finish());
    await screen.findByRole('heading', { name: 'Completed' });
    expect(dock).toHaveAttribute('data-open', 'false');                               // an explicit choice is kept
  });
});

describe('Activity dock pure rules', () => {
  it('shows each record\'s time in the person\'s LOCAL clock (the records carry UTC)', () => {
    expect(localClock('2026-11-07T04:27:46.283Z', 'UTC')).toBe('04:27:46');
    expect(localClock('2026-11-07T04:27:46.283Z', 'Asia/Kolkata')).toBe('09:57:46');
    expect(localClock('2026-11-07T04:27:46+00:00', 'America/Los_Angeles')).toBe('20:27:46');
    expect(localClock(null)).toBe('');
    expect(localClock('not a time')).toBe('');
  });
  it('follows new lines only while the person is at (or within a few pixels of) the bottom, never after scrolling up', () => {
    expect(shouldStickToBottom({ scrollTop: 600, clientHeight: 200, scrollHeight: 800 })).toBe(true);
    expect(shouldStickToBottom({ scrollTop: 590, clientHeight: 200, scrollHeight: 800 })).toBe(true);     // within 24 px
    expect(shouldStickToBottom({ scrollTop: 200, clientHeight: 200, scrollHeight: 800 })).toBe(false);    // scrolled up to read older lines
    expect(shouldStickToBottom({ scrollTop: 0, clientHeight: 200, scrollHeight: 150 })).toBe(true);       // nothing to scroll
  });
});
