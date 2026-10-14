/**
 * Project Home (Demo V2 UX correction): a first-time user opens a folder, sees what to do, starts work, always knows what Kriya is doing, answers when Kriya needs them and
 * understands the outcome - without documentation. These tests walk that journey through the real shell over the scripted fixture host; every word about the run still comes
 * from the records the host serves. Layout that needs real pixels (Start Run visible at 1280x800) is measured in the Electron smoke, not here: jsdom does no layout.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderWorkspaceShell } from '../testing/harness';

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

  it('Start Run is a first-level control: outside every disclosure and before the readiness detail, the Skills/Tools/routing sections and the recent runs in reading order', async () => {
    await openProject();
    const start = startButton();
    expect(start.closest('details')).toBeNull();
    const detail = screen.getByText('Project details').closest('details')!;
    expect(detail.open).toBe(false); // the ten-row readiness is a click away, not above the button
    const recent = screen.getByRole('region', { name: 'Recent runs' });
    for (const later of [detail, recent]) expect(start.compareDocumentPosition(later) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByRole('textbox', { name: 'Engineering goal' }).compareDocumentPosition(start) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('readiness is one compact line: Ready, the model, how many Skills and tools - the full checklist is under Project details', async () => {
    await openProject();
    const line = (await screen.findAllByText(/^✓ Ready/)).map((e) => e.closest('.ck-contextline')).find(Boolean) as HTMLElement;
    expect(line).toHaveTextContent(/fixture-model:7b/);
    expect(line).toHaveTextContent(/\d+ skills?/);
    expect(line).toHaveTextContent(/\d+ tools?/);
    expect(within(line).getByRole('button', { name: 'View readiness details' })).toBeInTheDocument();
    fireEvent.click(within(line).getByRole('button', { name: 'View readiness details' }));
    await waitFor(() => expect(screen.getByText('Project details').closest('details')!.open).toBe(true));
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

  it('the state follows the records: Starting, Editing, Validating - and says nothing a record does not say', async () => {
    const { host, run } = await startRun({ outcome: 'success' });
    const progress = () => within(run).getByRole('list', { name: 'Progress' });
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'STARTING')); // the launch is recorded but no evidence record exists yet
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'EDITING'));
    expect(within(run).getByRole('heading', { name: 'Editing' })).toBeInTheDocument();
    expect(within(run).getByTestId('current-activity')).toHaveTextContent('Creating or changing files…');
    expect(run.textContent).not.toMatch(/candidate|Attempt \d/); // no mechanism words on the primary surface
    expect(within(progress()).getByText(/Editing/).closest('li')).toHaveAttribute('aria-current', 'step');
    expect(within(progress()).getByText(/Planning/).closest('li')).toHaveAttribute('data-status', 'done');
    act(() => host.exec!.advance());
    await waitFor(() => expect(run).toHaveAttribute('data-state', 'VALIDATING'));
    expect(within(progress()).getByText(/Validating/).closest('li')).toHaveAttribute('aria-current', 'step');
    expect(within(progress()).getByText(/Finished/).closest('li')).toHaveAttribute('data-status', 'pending');
    expect(run.textContent).not.toMatch(/\d+\s*%|step \d+ of|of \d+ tests/i); // no invented percentage or counter
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
    expect(within(run).getByText('NO_COMMIT')).toBeInTheDocument();
    expect(run.querySelector('.ck-applied')!.textContent).toMatch(/Check your folder/);
  });

  it('a failure says "Run failed" with Kriya\'s recorded reason and the last stage reached - no raw events needed', async () => {
    const { host, run } = await startRun({ outcome: 'failure' });
    act(() => { host.exec!.advance(); host.exec!.advance(); host.exec!.advance(); });
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'failure'));
    expect(within(run).getByRole('heading', { name: 'Run failed' })).toBeInTheDocument();
    expect(within(run).getByText('What happened')).toBeInTheDocument();
    expect(within(run).getByText(/Failed to create an isolated generation sandbox \(fixture\)/)).toBeInTheDocument(); // Kriya's recorded reason (its raw text sits under Technical reason)
    expect(within(run).getByText('How far it got')).toBeInTheDocument();
    expect(within(run).getByRole('button', { name: 'View Failure Details' })).toBeInTheDocument();
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
});

describe('L/O. Runs is history; the existing diagnostics are one level deeper, not removed', () => {
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
    for (const name of ['Plan', 'Attempts', 'Context', 'Evidence']) expect(within(deeper).getByRole('tab', { name })).toBeInTheDocument();
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
