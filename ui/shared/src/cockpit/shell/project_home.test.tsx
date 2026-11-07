/**
 * Project Home (Demo V2 UX correction): a first-time user opens a folder, sees what to do, starts work, always knows what Kriya is doing, answers when Kriya needs them and
 * understands the outcome - without documentation. These tests walk that journey through the real shell over the scripted fixture host; every word about the run still comes
 * from the records the host serves. Layout that needs real pixels (Start Run visible at 1280x800) is measured in the Electron smoke, not here: jsdom does no layout.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { initialSelection } from '../../state/selection';
import { createCockpitRegistry } from '../composition';
import { createSelectionStore } from '../core';
import { FixtureHost } from '../providers/fixture';
import { HostProvider } from '../providers/hostProvider';
import { FAST_POLL, renderWorkspaceShell } from '../testing/harness';
import { AppShell } from './AppShell';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const rail = () => screen.getByRole('navigation', { name: 'Destinations' });
const current = () => screen.findByRole('region', { name: 'Current run' });
const strip = () => screen.findByRole('status', { name: 'Active run' });
const startButton = () => screen.getByRole('button', { name: /^Start Run$|^Starting…$/ });
const GOAL = 'Add a clamp(value, low, high) function with tests.';
const typeGoal = async (text = GOAL) => fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: text } });
const page = () => document.querySelector('.ck-shell')?.getAttribute('data-page');

async function openProject(fixture: Record<string, unknown> = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  await screen.findByText('What do you want Kriya to do?');
  return mounted;
}
async function startRun(execution: Record<string, unknown>) {
  const mounted = await openProject({ execution });
  await typeGoal();
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(startButton());
  return { ...mounted, run: await current() };
}

describe('A. Open Folder lands on Project Home', () => {
  it('the first folder opened from the welcome screen lands on Project, with the goal box and Start Run in view - never on run history', async () => {
    const { host } = renderWorkspaceShell({ fixture: { workspace: null, choices: ['/fixture/workspace'] } });
    fireEvent.click(await screen.findByRole('button', { name: /^Open Folder/ }));
    await screen.findByRole('navigation', { name: 'Destinations' });
    await waitFor(() => expect(host.workspaceCalls).toContain('remember:/fixture/workspace'));
    expect(within(rail()).getByRole('button', { name: 'Project' })).toHaveAttribute('aria-current', 'page');
    expect(page()).toBe('project');
    expect(await screen.findByRole('textbox', { name: 'Engineering goal' })).toBeVisible();
    expect(startButton()).toBeVisible();
    expect(screen.queryByText('No run selected')).toBeNull();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('workspace'); // which project is open
  });

  it('opening ANOTHER folder while on Skills lands on Project again (the previous page choice belongs to the previous folder)', async () => {
    const { host } = await openProject({ choices: ['/fixture/empty'] });
    goTo('Skills');
    await waitFor(() => expect(page()).toBe('skills'));
    host.menu('openFolder');
    await waitFor(() => expect(host.workspaceCalls).toContain('remember:/fixture/empty'));
    await waitFor(() => expect(page()).toBe('project'));
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('empty');
  });
});

describe('B/C/D. The idle Project page: goal, Start Run, compact readiness', () => {
  it('shows the goal box and a Start Run that says why it is off until there is a goal, then turns on', async () => {
    await openProject();
    await screen.findAllByText(/^✓ Ready/);
    expect(startButton()).toBeDisabled();
    expect(screen.getByText('Enter a goal to start.')).toBeInTheDocument();
    await typeGoal();
    await waitFor(() => expect(startButton()).toBeEnabled());
    expect(screen.getByText(/⌘ Enter|Ctrl Enter/)).toBeInTheDocument(); // the shortcut is shown beside the button
  });

  it('Start Run is a first-level control in the MAIN pane: outside every disclosure, the goal box before it, and the Inspector (recent runs, memory, security, readiness) a separate pane beside it', async () => {
    await openProject();
    const start = startButton();
    expect(start.closest('details')).toBeNull();
    const main = screen.getByRole('main', { name: 'Current task' });
    expect(main.contains(start)).toBe(true);
    const inspector = screen.getByTestId('inspector');
    expect(main.contains(inspector)).toBe(false);
    expect(screen.getByRole('region', { name: 'Recent runs' })).toBeInTheDocument();      // the Inspector opens on Runs
    expect(screen.getByRole('textbox', { name: 'Engineering goal' }).compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('readiness is one compact line: Ready, the model, how many Skills and tools - the full checklist is the Inspector\'s Project tab', async () => {
    await openProject();
    const line = (await screen.findAllByText(/^✓ Ready/)).map((e) => e.closest('.ck-contextline')).find(Boolean) as HTMLElement;
    expect(line).toHaveTextContent(/fixture-model:7b/);
    expect(line).toHaveTextContent(/\d+ skills?/);
    expect(line).toHaveTextContent(/\d+ tools?/);
    expect(within(line).getByRole('button', { name: 'View readiness details' })).toBeInTheDocument();
    fireEvent.click(within(line).getByRole('button', { name: 'View readiness details' }));
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute('aria-selected', 'true'));
    expect(screen.getByRole('region', { name: 'Readiness' })).toBeInTheDocument();
  });

  it('a blocking problem is shown prominently above Start Run, which stays off and nothing is sent', async () => {
    const { host } = await openProject({ workspace: '/fixture/blocked' });
    await typeGoal();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Cannot start a run');
    expect(alert).toHaveTextContent('RECOVERY_REQUIRED');
    expect(alert).toHaveTextContent(/kriya runs recover/);
    expect(alert.compareDocumentPosition(startButton()) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(startButton()).toBeDisabled();
    expect(host.exec!.starts).toEqual([]);
    expect(screen.getByRole('button', { name: 'View configuration' })).toBeInTheDocument();
  });

  it('Cmd/Ctrl+Enter in the goal box starts the run; with no goal it does nothing', async () => {
    const { host } = await openProject();
    await screen.findAllByText(/^✓ Ready/);
    const box = await screen.findByRole('textbox', { name: 'Engineering goal' });
    fireEvent.keyDown(box, { key: 'Enter', metaKey: true });
    expect(host.exec!.starts).toEqual([]); // empty goal
    fireEvent.change(box, { target: { value: GOAL } });
    await waitFor(() => expect(startButton()).toBeEnabled());
    fireEvent.keyDown(box, { key: 'Enter', ctrlKey: true });
    await current();
    expect(host.exec!.starts).toEqual([{ workspace: '/fixture/workspace', goal: GOAL }]);
    fireEvent.keyDown(document.body, { key: 'Enter', ctrlKey: true }); // a second press cannot start a second run
    expect(host.exec!.starts).toHaveLength(1);
  });

  it('a plain Enter in the goal box only adds a line: it never starts a run', async () => {
    const { host } = await openProject();
    await typeGoal();
    fireEvent.keyDown(await screen.findByRole('textbox', { name: 'Engineering goal' }), { key: 'Enter' });
    expect(host.exec!.starts).toEqual([]);
  });
});

describe('E/F. Start Run turns the page into the run, with only supported states', () => {
  it('the page becomes the current run at once: no goal box, no second Start Run, the goal restated, the state named', async () => {
    const { run } = await startRun({ outcome: 'success' });
    expect(run).toHaveAttribute('data-state', 'STARTING');
    expect(within(run).getByRole('heading', { name: 'Starting' })).toBeInTheDocument();
    expect(within(run).getByText(GOAL)).toBeInTheDocument();
    expect(screen.queryByRole('textbox', { name: 'Engineering goal' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^Start Run$/ })).toBeNull();
    expect(within(run).getByRole('button', { name: 'View Details' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Stop/ })).toBeNull(); // the host has no stop operation, so none is offered
  });

  it('the state follows the records: Starting, Implementing, Verifying - and says nothing a record does not say', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    const progress = () => within(run).getByRole('list', { name: 'Progress' });
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'STARTING')); // the launch is recorded but no evidence record exists yet
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'EDITING'));
    expect(within(run).getByRole('heading', { name: 'Implementing' })).toBeInTheDocument();
    expect(within(run).getByTestId('current-activity')).toHaveTextContent('Creating or changing files…');
    expect(run.textContent).not.toMatch(/candidate|Attempt \d/); // no mechanism words on the primary surface
    expect(within(progress()).getByText(/Implementing/).closest('li')).toHaveAttribute('aria-current', 'step');
    expect(within(progress()).getByText(/Planning/).closest('li')).toHaveAttribute('data-status', 'done');
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'VALIDATING'));
    expect(within(progress()).getByText(/Verifying/).closest('li')).toHaveAttribute('aria-current', 'step');
    expect(within(progress()).getByText(/Complete/).closest('li')).toHaveAttribute('data-status', 'pending');
    expect(run.textContent).not.toMatch(/\d+\s*%|step \d+ of|of \d+ tests/i); // no invented percentage or counter
  });

  it('the active run is a workspace: stepper first, then the quiet goal, what Kriya is doing now and the newest recorded activity - never a placeholder', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.advance());
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'EDITING'));
    expect(run).toHaveAttribute('data-live', 'true');
    const stepper = within(run).getByTestId('run-stepper');
    const goal = run.querySelector('.ck-goalline')!;
    expect(stepper.compareDocumentPosition(goal) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();       // the stage tracker leads, the goal is a quiet line below it
    expect(within(run.querySelector('.ck-current-head')!).getByRole('button', { name: 'Cancel Run' })).toBeInTheDocument(); // Cancel stays in the title row
    expect(within(run).getByTestId('current-activity')).toHaveTextContent('What Kriya is doing now');
    expect(run.textContent).not.toMatch(/Working…|Working\.\.\./);
    // the recent-activity preview is the SAME stream as the dock's Activity list
    const recent = await within(run).findByTestId('recent-activity');
    const lines = within(recent).getAllByRole('listitem').map((li) => li.textContent!.replace(/^\S*\s*[•✓✕⚠]\s*/, '').trim());
    expect(lines.length).toBeGreaterThan(0);
    expect(lines.length).toBeLessThanOrEqual(5);
    const dock = await screen.findByTestId('activity-list');
    const dockLines = within(dock).getAllByRole('listitem').map((li) => li.textContent!);
    for (const line of lines) expect(dockLines.some((d) => d.includes(line)), line).toBe(true);
  });

  it('the technical status (run id, record tallies, DERIVED liveness) is one disclosure away', async () => {
    const { run } = await startRun({ outcome: 'success' });
    const tech = within(run).getByText('Technical status').closest('details')!;
    expect(tech.open).toBe(false);
    expect(within(run).getByText(/^20\d{6}T\d{6}-[0-9a-f]{8}$/)).toBeInTheDocument();
  });
});

describe('K. The outcome is understood without opening diagnostics', () => {
  it('a success says "Run completed", lists what changed and the recorded verification, and offers View Changes / View Verification / New Goal', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    expect(within(run).getByRole('heading', { name: 'Run completed' })).toBeInTheDocument();
    const changed = await within(run).findByRole('list', { name: 'Changed files' }, { timeout: 4000 });
    expect(within(changed).getAllByRole('listitem').length).toBeGreaterThan(0);
    expect(changed.textContent).toMatch(/\+\d+ −\d+/);
    const verification = await within(run).findByLabelText('Verification', undefined, { timeout: 4000 });
    expect(verification.textContent).toMatch(/passed/);
    // Demo V2 (owner decisions on the requirement ledger): passed build/tests are not closure of the whole request, and "Requested behavior verified" is never claimed without a recorded positive verification.
    // The ledger fact is real but it is not a peer warning beside Build / Tests: it is under its own disclosure.
    expect(verification.textContent).not.toMatch(/Overall request|ledger|Requested behavior verified/);
    const formal = within(run).getByTestId('formal-requirements');
    expect(formal.tagName).toBe('DETAILS');
    expect((formal as HTMLDetailsElement).open).toBe(false);
    expect(within(formal).getByText('Formal requirement verification')).toBeInTheDocument();
    expect(formal.textContent).toMatch(/Requirement ledger: not fully verified/);
    expect(formal.textContent).not.toMatch(/Requested behavior verified/);
    for (const name of ['View Changes', 'View Verification', 'New Goal']) expect(within(run).getByRole('button', { name })).toBeInTheDocument();
    expect(within(run).getByText('✓ Your change was applied successfully.')).toBeInTheDocument(); // said in plain words, from the RunRecord's COMMITTED
    expect(run.querySelector('.ck-outcome')!.textContent).not.toMatch(/NO_COMMIT|commit COMMITTED|RunRecord/); // no raw code or internal source name on the outcome card itself (they are under Technical status)
    expect(within(run).getByText(/Kriya RunRecord/).closest('details')).toBe(within(run).getByText('Technical status').closest('details')); // where the outcome was read from is one disclosure away
  });

  it('a success whose commit result is NOT COMMITTED never claims the change was applied; it quotes Kriya\'s record and says to check the folder', async () => {
    const { host, run } = await startRun({ outcome: 'success', commitResult: 'NO_COMMIT' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    expect(within(run).queryByText('✓ Your change was applied successfully.')).toBeNull();
    expect(run.querySelector('.ck-applied')!.textContent).toMatch(/did not record that the change was applied.*Check your folder/);
    expect(run.querySelector('.ck-applied')!.textContent).not.toContain('NO_COMMIT'); // the raw record is one disclosure down
    expect(within(within(run).getByText('Technical status').closest('details')!).getByText(/commit result NO_COMMIT/)).toBeInTheDocument();
  });

  it('a failure says "Run failed" with Kriya\'s recorded reason and the last stage reached - no raw events needed', async () => {
    const { host, run } = await startRun({ outcome: 'failure' });
    act(() => { host.exec!.advance(); host.exec!.advance(); host.exec!.advance(); });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'failure'));
    expect(within(run).getByRole('heading', { name: 'Run failed' })).toBeInTheDocument();
    expect(within(run).getByText('What happened')).toBeInTheDocument();
    expect(within(run).getByText(/Failed to create an isolated generation sandbox \(fixture\)/)).toBeInTheDocument(); // Kriya's recorded reason (its raw text sits under Technical reason)
    expect(within(run).getByTestId('what-happened')).toHaveTextContent(/did not pass Kriya's checks/);
    expect(within(run).getByTestId('what-happened').textContent).not.toMatch(/failure category|exit code|NOT_COMMITTED|quality_gate/);
    expect(within(run).getByText('How far it got')).toBeInTheDocument();
    expect(within(run).getByRole('button', { name: 'View Details' })).toBeInTheDocument();
    expect(within(run).getByRole('button', { name: 'Copy failure details' })).toBeInTheDocument();
    expect(within(run).getByRole('button', { name: 'New Goal' })).toBeInTheDocument();
    expect(within(run).queryByRole('list', { name: 'Changed files' })).toBeNull(); // nothing is claimed as changed for a failed run
  });

  it('New Goal returns the page to the goal box, empty-state ready for another run', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    fireEvent.click(within(run).getByRole('button', { name: 'New Goal' }));
    expect(await screen.findByRole('textbox', { name: 'Engineering goal' })).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Current run' })).toBeNull();
  });

  it('A1: a finished run offers "Open in Finder" under its own name - there is no ambiguous "Open Project" that secretly reveals - and says why when the host cannot', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    expect(within(run).queryByRole('button', { name: 'Open Project' })).toBeNull();
    fireEvent.click(within(run).getByRole('button', { name: 'Open in Finder' }));
    await waitFor(() => expect(host.workspaceCalls).toContain('reveal'));
    host.workspace.revealWorkspace = async () => ({ revealed: false, reason: 'the folder is no longer there' });
    fireEvent.click(within(run).getByRole('button', { name: 'Open in Finder' }));
    expect(await within(run).findByText('Could not open: the folder is no longer there')).toBeInTheDocument();
  });
});

describe('B2. the terminal result appears in the same card, on the same page, with no refresh or navigation', () => {
  it.each(['success', 'failure'] as const)('a %s replaces the live view in place: the page is still Project, the live activity line is gone and the result is on screen', async (outcome) => {
    const { host, run } = await startRun({ outcome });
    expect(within(run).getByTestId('current-activity')).toBeInTheDocument();
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', outcome));
    expect(page()).toBe('project');                                           // no navigation was needed
    expect(within(run).queryByTestId('current-activity')).toBeNull();
    expect(within(run).getByRole('heading', { level: 2 })).toHaveTextContent(outcome === 'success' ? 'Run completed' : 'Run failed');
  });
});

describe('B10. a failed run says what happened and what the person can do', () => {
  it('shows "What you can do" with Run again (a NEW run, same goal), Edit goal and retry, Open in Finder and View Failure Details - and Run again really starts another run', async () => {
    const { host, run } = await startRun({ outcome: 'failure' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'failure'));
    expect(within(run).getByTestId('what-you-can-do')).toHaveTextContent('What you can do');
    for (const name of ['Run again', 'Edit goal and retry', 'Open in Finder', 'View Details', 'New Goal']) expect(within(run).getByRole('button', { name })).toBeInTheDocument();
    expect(within(run).getByTestId('failure-summary').textContent).toMatch(/Kriya could not complete|did not pass/);   // words, not a reason code
    fireEvent.click(within(run).getByRole('button', { name: 'Run again' }));
    const next = await current();
    await waitFor(() => expect(next).toHaveAttribute('data-stage', expect.not.stringMatching(/^finished$/)));   // a second run is in progress with the goal that was already entered
  });
  it('a successful run has no "What you can do" block (nothing went wrong) and no Run again', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    expect(within(run).queryByTestId('what-you-can-do')).toBeNull();
    expect(within(run).queryByRole('button', { name: 'Run again' })).toBeNull();
  });
});

describe('A6. "View readiness details" is never a dead control', () => {
  it('selects the Inspector\'s Project tab, which shows the readiness (one click, no scrolling)', async () => {
    await openProject();
    await screen.findAllByText(/^✓ Ready/);
    expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute('aria-selected', 'false');
    fireEvent.click(screen.getByRole('button', { name: 'View readiness details' }));
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute('aria-selected', 'true'));
    expect(await screen.findByTestId('project-facts')).toBeInTheDocument();
  });

  it('on a narrow window the Inspector is a closed drawer; View readiness details opens it on the Project tab', async () => {
    const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace' }, width: 800 });
    await screen.findByRole('navigation', { name: 'Destinations' });
    await screen.findByText('What do you want Kriya to do?');
    expect(screen.queryByTestId('inspector')).toBeNull();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(screen.getByRole('button', { name: 'View readiness details' }));
    const inspector = await screen.findByTestId('inspector');
    expect(inspector).toHaveAttribute('data-mode', 'drawer');
    expect(screen.getByRole('tab', { name: 'Project' })).toHaveAttribute('aria-selected', 'true');
    expect(mounted.host).toBeDefined();
  });
});

describe('L/O. Runs is history; the existing diagnostics are one level deeper, not removed', () => {
  it('before any history snapshot exists, Runs does not claim nothing was recorded: it says the history has not been read and offers the refresh (MEASURED against a real backend run: a CLI run was invisible until Refresh)', async () => {
    const host = new FixtureHost({ workspace: '/fixture/empty' });
    const everything = host.query.bind(host);
    host.query = async (request) => { // the store has no published snapshot yet: the real backend's snapshot.list answers with an empty list
      const envelope = await everything(request);
      return request.operation === 'snapshot.list' && envelope.data ? { ...envelope, data: { ...(envelope.data as object), snapshots: [] } } : envelope;
    };
    render(<AppShell provider={new HostProvider(host, 'fixture')} registry={createCockpitRegistry()} selection={createSelectionStore({ ...initialSelection })} viewportWidth={1440} pollIntervals={FAST_POLL} initialPage="runs" />);
    const note = await screen.findByTestId('no-history');
    expect(note).toHaveTextContent(/history has not been read in this window yet/);
    expect(note).toHaveTextContent(/no published history snapshot exists/);
    expect(note).toHaveTextContent(/another process/);
    expect(note).not.toHaveTextContent(/Nothing has been recorded/);
    expect(screen.getByRole('button', { name: 'Check for history again' })).toBeInTheDocument();
  });
  it('with no recorded run, Runs says "No runs yet." and offers Go to Project, which goes there', async () => {
    renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/empty' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument();
    expect(screen.queryByText('No run selected')).toBeNull();
    const buttons = screen.getAllByRole('button', { name: 'Go to Project' });
    expect(buttons.length).toBeGreaterThan(0);
    fireEvent.click(buttons[0]!);
    await waitFor(() => expect(page()).toBe('project'));
  });

  it('with nothing selected the history says what to do and does not show a diagnostic layout', async () => {
    renderWorkspaceShell({ page: 'runs', fixture: { workspace: '/fixture/workspace' } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    expect(await screen.findByText('Run history')).toBeInTheDocument();
    expect(screen.getByText('Select a run')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Go to Project' })).toBeInTheDocument();
  });

  it('View Changes from a finished run opens its documents: Overview / Changes / Verification first, Plan, Attempts, Context and Evidence under Details, Timeline and WHY beside', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
    fireEvent.click(within(run).getByRole('button', { name: 'View Changes' }));
    await waitFor(() => expect(page()).toBe('runs'));
    const tabs = await screen.findByRole('tablist', { name: 'Run documents' });
    expect(within(tabs).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Overview', 'Changes', 'Verification', 'Details']);
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Details' }));
    const deeper = await screen.findByRole('tablist', { name: 'Details' });
    for (const name of ['Plan', 'Attempts', 'Context', 'Forensic Evidence']) expect(within(deeper).getByRole('tab', { name })).toBeInTheDocument();
    expect(await screen.findByRole('tree', { name: 'Execution timeline' })).toBeInTheDocument();
    fireEvent.click(within(deeper).getByRole('tab', { name: 'Attempts' }));
    expect(await screen.findByRole('table', { name: 'Attempts' })).toBeInTheDocument();
  });
});

describe('M/N and accessible names', () => {
  it('no navigation control carries a play or stop glyph; the five destinations have visible text in the order Project, Runs, Skills, Tools, Settings', async () => {
    await openProject();
    const buttons = within(rail()).getAllByRole('button');
    expect(buttons.map((b) => b.getAttribute('aria-label'))).toEqual(['Project', 'Runs', 'Skills', 'Tools', 'Settings']);
    for (const b of buttons) {
      expect(b.textContent).not.toMatch(/[▶■▣►◼]/);
      expect(b.textContent).toContain(b.getAttribute('aria-label')!);
    }
    expect(document.querySelector('.ck-start')!.textContent).toBe('Start Run'); // the play glyph exists only as the Start Run action's decoration (CSS)
  });

  it('every primary control has a text accessible name', async () => {
    await openProject();
    for (const name of ['Project', 'Runs', 'Skills', 'Tools', 'Settings']) expect(within(rail()).getByRole('button', { name })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Start Run' })).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Engineering goal' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Switch workspace' })).toBeInTheDocument();
  });

  it('the welcome screen names Open Folder in text', async () => {
    renderWorkspaceShell({ fixture: { workspace: null } });
    expect(await screen.findByRole('button', { name: /^Open Folder/ })).toBeInTheDocument();
  });

  it('while a run is active the strip, Return to Run, Approve & Continue, Reject and View Details all have text names', async () => {
    const { host, run } = await startRun({ outcome: 'review' });
    expect(within(run).getByRole('button', { name: 'View Details' })).toBeInTheDocument();
    for (let i = 0; i < 3; i += 1) act(() => host.exec!.advance());
    const review = await within(run).findByRole('region', { name: 'Change review' });
    expect(within(review).getByText('ACTION REQUIRED')).toBeInTheDocument();
    expect(within(review).getByRole('button', { name: 'Reject' })).toBeInTheDocument();
    await within(review).findByTestId('proposed-change'); // Approve & Continue exists only once the exact change is on screen
    expect(within(review).getByRole('button', { name: 'Approve & Continue' })).toBeInTheDocument();
    goTo('Skills');
    const s = await strip();
    expect(s).toHaveTextContent('ACTION REQUIRED');
    expect(within(s).getByRole('button', { name: 'Review' })).toBeInTheDocument();
  });
});


describe('Project details and Recent runs are compact and in plain words (Demo V2)', () => {
  it('Project details lead with four plain lines; the exact authority diagnostics are one level down under Technical details', async () => {
    await openProject();
    fireEvent.click(await screen.findByRole('tab', { name: 'Project' }));
    const facts = await screen.findByTestId('project-facts');
    expect(Array.from(facts.querySelectorAll('li')).map((li) => (li.textContent ?? '').replace(/\s+/g, ' ').trim().split(' Protected')[0])).toEqual(
      expect.arrayContaining(['✓ Project folder Ready', '✓ Git Ready']));
    expect(facts.textContent).toMatch(/Configuration/);
    expect(facts.textContent).toMatch(/Model/);
    expect(facts.textContent).not.toMatch(/Operator-only|durable approval|fingerprint|sha256/i);
    const technical = screen.getByTestId('technical-project-details') as HTMLDetailsElement;
    expect(technical.open).toBe(false);
    expect(within(technical).getByRole('region', { name: 'Readiness' })).toBeInTheDocument();
  });

  it('Recent runs shows at most three compact entries and "View all runs"', async () => {
    await openProject();
    const region = await screen.findByRole('region', { name: 'Recent runs' });
    await waitFor(() => expect(within(region).queryAllByRole('listitem').length).toBeGreaterThan(0));
    expect(within(region).getAllByRole('listitem').length).toBeLessThanOrEqual(3);
    expect(within(region).getByRole('button', { name: 'View all runs' })).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: /^All runs$/ })).toBeNull();
  });
});

describe('L. A reopened folder whose history is not in this window\'s state directory says so (REOPENED-PROJECT-RUN-HISTORY-001)', () => {
  it('a folder with a .kriya directory and no run in the history store names the state directory this window reads - never a bare "No runs yet"', async () => {
    await openProject({ workspace: '/fixture/plain' });
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument();
    const note = await screen.findByTestId('history-elsewhere');
    expect(note.textContent).toMatch(/has a \.kriya directory \(Kriya has been started here\)/);
    expect(note.textContent).toMatch(/reads run history from .*\(KRIYA_STATE_DIR \(operator environment\)\)/);
    expect(note.textContent).toMatch(/last history snapshot/);
    expect(note.textContent).toMatch(/different Kriya state directory, or after that snapshot, does not appear here/);
    expect(note.textContent).not.toMatch(/lost|deleted|corrupt/i);   // it states where this window reads; it does not guess what happened to the runs
  });
  it('a folder Kriya has not worked in says only "No runs yet."; a folder with runs lists them and adds no note', async () => {
    await openProject({ workspace: '/fixture/empty' });
    expect(await screen.findByText('No runs yet.')).toBeInTheDocument();
    expect(screen.queryByTestId('history-elsewhere')).toBeNull();
  });
  it('a folder with recorded runs shows them and no note', async () => {
    await openProject();
    await screen.findByText(/A run that never closed its store/, undefined, { timeout: 3000 });
    expect(screen.queryByTestId('history-elsewhere')).toBeNull();
  });
});
