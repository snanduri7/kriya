/** Start Run through the real shell over the scripted fixture host (GUI M6). The host starts a PROCESS; every word about the run comes from the records Kriya
 * would have written (launch status + live evidence records), advanced frame by frame by the test. No outcome is ever taken from a process exit. */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { deriveOutcome } from '../core';
import type { LaunchStatus } from '../../model/kup';
import { renderWorkspaceShell } from '../testing/harness';
import { LAUNCH_ID } from '../providers/fixture/fixtureExecution';

const goTo = (name: string) => fireEvent.click(within(screen.getByRole('navigation', { name: 'Destinations' })).getByRole('button', { name }));
const banner = () => screen.findByRole('region', { name: 'Current run' });
const goalBox = () => screen.findByRole('textbox', { name: 'Engineering goal' });
const startButton = () => screen.getByRole('button', { name: /^Start Run$|^Starting…$/ });
/** The Project page's "Project details" disclosure holds the full readiness; the tests that read it open it like a person would. */
const openDetails = async () => { const summary = await screen.findByText('Project details'); if (!summary.closest('details')!.open) fireEvent.click(summary); };
const GOAL = 'Add a clamp(value, low, high) function with tests.';

async function openRunPanel(fixture = {}) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', ...fixture } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  goTo('Project');
  return mounted;
}
async function typeGoal(text = GOAL) { fireEvent.change(await goalBox(), { target: { value: text } }); }
const asked = (host: { calls: Array<{ operation: string }> }, op: string) => host.calls.filter((c) => c.operation === op);

describe('New Run entry', () => {
  it('Runs offers New Run, which takes the person to the goal entry on the Project page - for an empty folder and an existing project alike', async () => {
    for (const folder of ['/fixture/empty', '/fixture/workspace']) {
      document.body.innerHTML = '';
      renderWorkspaceShell({ page: 'runs', fixture: { workspace: folder } });
      await screen.findByRole('navigation', { name: 'Destinations' });
      fireEvent.click(await screen.findByRole('button', { name: 'New Run' }));
      expect(await screen.findByRole('textbox', { name: 'Engineering goal' })).toBeInTheDocument();
    }
  });
  it('the welcome screen has no New Run (no workspace is open)', async () => {
    document.body.innerHTML = '';
    renderWorkspaceShell({ fixture: { workspace: null } });
    await screen.findByRole('main', { name: 'Welcome' });
    expect(screen.queryByRole('button', { name: 'New Run' })).toBeNull();
  });
});

describe('goal and preflight', () => {
  it('shows Kriya\'s preflight in its own words (under Project details) and keeps Start Run off until there is a goal', async () => {
    await openRunPanel();
    await screen.findAllByText(/^✓ Ready/);
    await openDetails();
    const panel = (await screen.findByRole('heading', { level: 1 })).closest('.ck-project') as HTMLElement;
    expect((await within(panel).findAllByText('Packaged defaults (this folder has no kriya.yaml)')).length).toBeGreaterThan(0); // checklist + details
    expect(within(panel).getAllByText(/No findings: this folder's configuration sets nothing only an operator may set/).length).toBeGreaterThan(0);
    expect(within(panel).getAllByText('/fixture/state').length).toBeGreaterThan(0);
    expect(within(panel).getAllByText('fixture-model:7b').length).toBeGreaterThan(0); // the run-configuration details, the routing table and the compact line name it
    expect(within(panel).getByText(/fallback fixture-fallback:3b/)).toBeInTheDocument();
    expect(within(panel).getAllByText(/held for your review in this window and applied only if you approve it/i).length).toBeGreaterThan(0);
    expect(startButton()).toBeDisabled();
    expect(screen.getByText('Enter a goal to start.')).toBeInTheDocument(); // a disabled Start Run says why
    await typeGoal();
    await waitFor(() => expect(startButton()).toBeEnabled());
  });

  it('a whitespace-only goal cannot be started', async () => {
    await openRunPanel();
    await typeGoal('   \n  ');
    await screen.findAllByText(/^✓ Ready/);
    expect(startButton()).toBeDisabled();
  });

  it('an empty folder and an existing project use the same panel: same fields, same Start Run, no greenfield/brownfield screen', async () => {
    const texts: string[][] = [];
    for (const folder of ['/fixture/empty', '/fixture/workspace']) {
      const { unmount } = (() => { const m = renderWorkspaceShell({ fixture: { workspace: folder } }); return { unmount: () => m }; })();
      await screen.findByRole('navigation', { name: 'Destinations' });
      goTo('Project');
      const panel = (await screen.findByRole('heading', { level: 1 })).closest('.ck-project') as HTMLElement;
      await within(panel).findAllByText(/^✓ Ready/);
      await openDetails();
      texts.push(within(panel).getAllByRole('term').map((t) => t.textContent ?? ''));
      expect(within(panel).getByRole('button', { name: 'Start Run' })).toBeInTheDocument();
      expect(screen.queryByText(/greenfield|brownfield/i)).toBeNull();
      void unmount;
      document.body.innerHTML = '';
    }
    expect(texts[0]).toEqual(texts[1]);
  });

  it('a blocker from Kriya is shown with its code and Start Run stays off; nothing is sent to the host', async () => {
    const { host } = await openRunPanel({ workspace: '/fixture/blocked' });
    await typeGoal();
    expect(await screen.findByText(/Cannot start a run/)).toBeInTheDocument();
    expect(screen.getByText(/RECOVERY_REQUIRED/)).toBeInTheDocument();
    expect(startButton()).toBeDisabled();
    expect(host.exec!.starts).toEqual([]);
  });

  it('a host that cannot execute says so and offers no Start Run', async () => {
    await openRunPanel({ execution: false });
    expect(await screen.findByText('This host cannot start a run.')).toBeInTheDocument(); // said once, not repeated in the details
    expect(screen.queryByRole('button', { name: 'Start Run' })).toBeNull();
  });
});

describe('Start Run', () => {
  it('sends exactly {workspace, goal} - no options, no command text', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    await banner();
    expect(host.exec!.starts).toEqual([{ workspace: '/fixture/workspace', goal: GOAL }]);
  });

  it('suppresses a double submission: two clicks in the same tick reach the host once', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    const button = startButton();
    await act(async () => { fireEvent.click(button); fireEvent.click(button); });
    await banner();
    expect(host.exec!.starts).toHaveLength(1);
  });

  it('while the launch is in progress the page becomes the run (no goal box, no second Start Run) and says Kriya has not recorded it yet', async () => {
    await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    expect(bar).toHaveAttribute('data-stage', 'starting');
    expect(within(bar).getByText(/Waiting for Kriya to record the launch/)).toBeInTheDocument();
    // the goal composer is replaced by the run itself: no goal box, no second Start Run, and no control Kriya has no host operation for
    expect(screen.queryByRole('textbox', { name: 'Engineering goal' })).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Start Run|Starting…)$/ })).toBeNull();
    expect(screen.queryByRole('button', { name: /^(Stop|Pause|Resume|Retry|Rerun|Cancel)/ })).toBeNull();
  });

  it('shows only what Kriya\'s records say as they appear: counts by recorded kind, the latest record, DERIVED liveness - and a coarse phase that follows the records, with no invented stage or number', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.advance());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'starting'));
    await waitFor(() => expect(within(bar).getByText(/liveness starting/)).toBeInTheDocument());
    act(() => host.exec!.advance());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'running'));
    await waitFor(() => expect(within(bar).getByText(/3 evidence records/)).toBeInTheDocument());
    expect(bar).toHaveAttribute('data-state', 'EDITING');
    expect(within(bar).getByText(/Units opened 1/)).toBeInTheDocument();
    expect(within(bar).getByText(/Attempts opened 1/)).toBeInTheDocument();
    expect(within(bar).getByText(/latest: attempt\.opened/)).toBeInTheDocument();
    expect(within(bar).getByText(/liveness live/)).toBeInTheDocument();
    expect(within(bar).getByText('(DERIVED)')).toBeInTheDocument();
    act(() => host.exec!.advance());
    await waitFor(() => expect(within(bar).getByText(/7 evidence records/)).toBeInTheDocument());
    expect(within(bar).getByText(/Model requests 1/)).toBeInTheDocument();
    // the coarse phase follows the records: attempt 1 open and the Developer working is Editing; a recorded gate is Validating
    expect(bar).toHaveAttribute('data-state', 'VALIDATING');
    expect(within(bar).getByRole('list', { name: 'Progress' }).textContent).toMatch(/Planning.*Editing.*Validating.*Finished/);
    expect(within(bar).queryByText(/\d+\s*%|of \d+ tests|step \d+ of/i)).toBeNull(); // no invented percentage, count or step number
    // Records are read incrementally from the last seq, never from the top again.
    const reads = asked(host, 'evidence.records') as Array<{ operation: string; from_seq?: number }>;
    expect(reads.map((r) => r.from_seq)).toEqual(expect.arrayContaining([1, 4]));
  });

  it('the durable success: the outcome is Kriya\'s RunRecord, polling stops, the cockpit refreshes and shows the finished run in Runs', async () => {
    const { host, store } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    expect(within(bar).getByText('Run succeeded')).toBeInTheDocument();
    expect(within(bar).getByText(/Kriya RunRecord/)).toBeInTheDocument();
    expect(bar).toHaveAttribute('data-outcome', 'success');
    await waitFor(() => expect(store.getState().runId).toBe('fx-launched'));
    goTo('Runs');
    expect(await screen.findAllByText(GOAL)).not.toHaveLength(0); // the new run is in the workspace's run list
    // Polling stopped: no further launch.status reads once the outcome is durable.
    const before = asked(host, 'launch.status').length;
    await new Promise((r) => setTimeout(r, 120));
    expect(asked(host, 'launch.status').length).toBe(before);
    // The record carries timings for the measurements, and nothing else changed the shell.
    const timing = JSON.parse(bar.getAttribute('data-timing') ?? '{}') as Record<string, number | null>;
    expect(timing.acknowledgedAt).not.toBeNull();
    expect(timing.terminalAt).not.toBeNull();
  });

  it('the finished run is selected and the refresh is recorded even when the snapshot acquisition takes a while (the polling effect has ended by then)', async () => {
    const { host, provider, store } = await openRunPanel();
    const realAcquire = provider.acquireSnapshot.bind(provider);
    (provider as unknown as { acquireSnapshot: typeof realAcquire }).acquireSnapshot = async (w) => { await new Promise((r) => setTimeout(r, 150)); return realAcquire(w); };
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    await waitFor(() => expect(store.getState().runId).toBe('fx-launched'), { timeout: 3000 });
    await waitFor(() => expect(JSON.parse(bar.getAttribute('data-timing') ?? '{}').refreshedAt).not.toBeNull());
  });

  it('when a run ends the open folder is described again, so the Workspace facts are not the pre-run ones (and the folder stays open)', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    const before = asked(host, 'workspace.describe').length;
    fireEvent.click(startButton());
    const bar = await banner();
    expect(asked(host, 'workspace.describe').length).toBe(before);
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    await waitFor(() => expect(asked(host, 'workspace.describe').length).toBe(before + 1));
    expect(screen.getByRole('button', { name: 'Switch workspace' }).textContent).toContain('workspace');
  });

  it('when a run ends the preflight is asked of Kriya again (the folder may no longer be startable)', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    const before = asked(host, 'execution.preflight').length;
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    await waitFor(() => expect(asked(host, 'execution.preflight').length).toBeGreaterThan(before));
  });

  it('a durable failure is shown as a failure, with Kriya\'s failure category', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'failure' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'failure'));
    expect(within(bar).getByRole('heading', { name: 'Run failed' })).toBeInTheDocument();
    expect(within(bar).getByText(/failure category quality_gate_failed/)).toBeInTheDocument();
    expect(within(bar).getByText(/Kriya reported:/)).toBeInTheDocument(); // Kriya's own recorded error text, not an inferred cause
    expect(within(bar).getByText(/Failed to create an isolated generation sandbox \(fixture\)/)).toBeInTheDocument();
  });

  it('a process that dies without sealing is NOT a success: the host reports the exit, Kriya\'s records say the run did not finish', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'died' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => { host.exec!.advance(); host.exec!.advance(); });
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'running'));
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'interrupted'));
    expect(within(bar).getByText('The run did not finish')).toBeInTheDocument();
    expect(within(bar).getByText(/signal SIGKILL/)).toBeInTheDocument();
    expect(within(bar).queryByText(/succeeded/i)).toBeNull();
  });

  it('a change review Kriya could not hold open is shown as "Approval required" with Kriya\'s recorded reason and details, says nothing was approved, and offers no Approve button', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'approval_required' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'approval_required'));
    expect(within(bar).getByText('Approval required')).toBeInTheDocument();
    expect(within(bar).getByText(/a proposed change needs approval/)).toBeInTheDocument();
    expect(within(bar).getByText(/the proposed change touches a sensitive path/)).toBeInTheDocument();
    expect(within(bar).getByText(/files: \.github\/workflows\/ci\.yml/)).toBeInTheDocument();
    expect(within(bar).getByText(/nothing was approved on your behalf/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /approve|accept|allow|yes/i })).toBeNull();
    expect(within(bar).queryByText(/Run failed/)).toBeNull();
  });

  it('shows which tools a run can see, without claiming a goal needs them', async () => {
    await openRunPanel();
    const line = await screen.findByText('Tools a run can see');
    const row = line.nextElementSibling as HTMLElement;
    expect(row.textContent).toContain('python3, git');
    expect(row.textContent).toContain('unavailable: java, mvn');
    expect(row.querySelector('[title]')?.getAttribute('title')).toMatch(/only known while the workflow runs/);
  });

  it('a run that SUCCEEDED is a success even if a decision was noted earlier; declined decisions are shown as informational, nothing approved (reviewer P1-1)', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'success_with_declined' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    expect(bar).toHaveAttribute('data-outcome', 'success');
    expect(within(bar).getByText('Run succeeded')).toBeInTheDocument();
    expect(within(bar).queryByText(/Approval required/)).toBeNull();
    expect(within(bar).getByText(/Decisions declined, nothing approved.*skill_gap_decision: no verified skill for lib-x/)).toBeInTheDocument();
  });

  it('a launch stuck in STARTING after its process died ends as "did not finish" (no endless polling, workspace unlocked)', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'stuck_starting' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => { host.exec!.advance(); });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'interrupted'));
    expect(within(bar).getByText(/before the run started/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Switch workspace' })).toBeEnabled());
  });

  it('an "interrupted" reading that straddled the process\'s own final write is never concluded: the next poll shows the sealed success (stale-read race)', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'success_after_stale_read' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => { host.exec!.advance(); host.exec!.advance(); });
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    expect(bar).toHaveAttribute('data-outcome', 'success');
    expect(within(bar).queryByText(/did not finish/)).toBeNull();
  });

  it('a launch Kriya refused is "Refused before a run started" with Kriya\'s reason', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'refused' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'refused'));
    expect(within(bar).getByText(/another Kriya run already owns this folder/)).toBeInTheDocument();
  });

  it('a process that exits before Kriya recorded any launch is "No run was recorded" - never a success, never a hang', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'never_recorded' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'not_recorded'));
    expect(within(bar).getByText('No run was recorded')).toBeInTheDocument();
    expect(within(bar).getByText(/exit code 1/)).toBeInTheDocument();
  });

  it('a conclusion awaiting its second confirming poll belongs to ONE launch: the next launch ending the same way is confirmed by ITS OWN two polls (review P3-2)', async () => {
    const { host } = await openRunPanel({ execution: { outcome: 'never_recorded' } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    const polls = () => host.calls.filter((c) => c.operation === 'launch.status').length;
    const endOnce = async () => {
      fireEvent.click(startButton());
      const bar = await banner();
      await new Promise((r) => setTimeout(r, 150)); // let the in-flight poll of the live process settle, so only post-exit polls are counted
      const before = polls();
      act(() => host.exec!.finish());
      await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'not_recorded'));
      return polls() - before;
    };
    expect(await endOnce()).toBeGreaterThanOrEqual(2); // silence is confirmed by two consecutive polls
    fireEvent.click(within(await banner()).getByRole('button', { name: 'New Goal' }));
    expect(await endOnce()).toBeGreaterThanOrEqual(2); // the second launch is not "pre-confirmed" by the first one's leftover signature
  });

  it('a refusal by the host is shown with its typed code and starts nothing', async () => {
    await openRunPanel({ execution: { refuseStart: { code: 'PREFLIGHT_BLOCKED', message: 'another Kriya run already owns this folder' } } });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    expect(await screen.findByText(/The run was not started/)).toBeInTheDocument();
    expect(screen.getByText(/PREFLIGHT_BLOCKED/)).toBeInTheDocument();
    expect(screen.queryByRole('region', { name: 'Current run' })).toBeNull();
    expect(startButton()).toBeEnabled();
  });

  it('the finished banner can be dismissed and a second run can then be started', async () => {
    const { host } = await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    const bar = await banner();
    act(() => host.exec!.finish());
    await waitFor(() => expect(bar).toHaveAttribute('data-stage', 'finished'));
    fireEvent.click(within(bar).getByRole('button', { name: 'New Goal' }));
    expect(screen.queryByRole('region', { name: 'Current run' })).toBeNull();
    expect(startButton()).toBeEnabled();
  });
});

describe('workspace during a run', () => {
  it('the switcher is disabled while the run is in progress and the menu\'s Open Folder is refused without showing the chooser', async () => {
    const { host } = await openRunPanel({ choices: ['/fixture/dirty'] });
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    await banner();
    const switcher = screen.getByRole('button', { name: 'Switch workspace' });
    expect(switcher).toBeDisabled();
    await act(async () => { host.menu('openFolder'); });
    expect(await screen.findByText(/A run is in progress; the workspace cannot change until it ends/)).toBeInTheDocument();
    expect(host.workspaceCalls.filter((c) => c.startsWith('remember'))).toEqual([]);
    expect(host.workspaceCalls.filter((c) => c === 'choose').length).toBe(1); // the host was asked and refused; the scripted folder was not consumed
    act(() => host.exec!.finish());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Switch workspace' })).toBeEnabled());
  });

  it('historical runs stay inspectable during a run (reads are not blocked)', async () => {
    await openRunPanel();
    await typeGoal();
    await screen.findAllByText(/^✓ Ready/);
    fireEvent.click(startButton());
    await banner();
    goTo('Runs');
    expect(await screen.findAllByText(/Create a utility library/)).not.toHaveLength(0);
  });
});

describe('deriveOutcome (durable records only)', () => {
  const status = (over: { phase: 'STARTING' | 'RUNNING' | 'ENDED'; liveness: 'starting' | 'live' | 'interrupted' | 'ended'; terminal?: string | null; run?: boolean; exit?: number; workflow?: string | null; approval?: { kind: string; reason: string; details: Record<string, unknown> } }): LaunchStatus => ({
    launch: { launch_id: LAUNCH_ID, workspace: { path: '/w', identity: 'i' }, requested_at: 't', goal: { sha256: 'x', chars: 1 }, options: {}, phase: over.phase,
      run: over.run === false ? null : { run_id: LAUNCH_ID, started_at: 't', base_revision: null },
      ended: over.phase === 'ENDED' ? { at: 't', exit_code: over.exit ?? 0, workflow_status: over.workflow ?? 'success', quality_gates_passed: true, failure_category: null, trace_run_id: 'tr', error: null, note: '' } : null, history: [], process: { pid: 1 }, approval_required: over.approval ? { ...over.approval, noted_at: 't' } : null },
    run_record: { found: over.terminal !== undefined, unreadable: null, ...(over.terminal !== undefined ? { lifecycle_state: over.terminal ?? 'RUNNING', terminal_status: over.terminal, commit_result: null, revision: 1 } : {}) },
    evidence: { state: 'sealed', line_count: 1, sealed: true }, workspace_lock: { held: false, held_by_this_launch: false }, liveness: over.liveness, liveness_note: '',
  } as LaunchStatus);
  const exited = { phase: 'exited', launch_id: LAUNCH_ID, workspace: '/w', started_at: 't', ended_at: 't', exit: { code: 0, signal: null }, diagnostic: null } as const;
  it('RunRecord SUCCESS beats an approval note; a note on a run that did not succeed still means approval required', () => {
    const approval = { kind: 'change_approval', reason: 'r', details: {} };
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'SUCCESS', approval }), false, exited)?.kind).toBe('success');
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'FAILURE', exit: 1, approval }), false, exited)?.kind).toBe('approval_required');
  });
  it('STARTING + process exited + lock free is interrupted; STARTING while the process still runs is not over', () => {
    expect(deriveOutcome(status({ phase: 'STARTING', liveness: 'starting', run: false }), false, exited)?.kind).toBe('interrupted');
    expect(deriveOutcome(status({ phase: 'STARTING', liveness: 'starting', run: false }), false, { ...exited, phase: 'running', exit: null, ended_at: null } as const)).toBeNull();
  });

  it('ENDED + RunRecord SUCCESS = success; FAILURE = failure; anything else is needs_review', () => {
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'SUCCESS' }), false, exited)?.kind).toBe('success');
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'FAILURE', exit: 1, workflow: 'failure' }), false, exited)?.kind).toBe('failure');
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'NEEDS_REVIEW' }), false, exited)?.kind).toBe('needs_review');
  });
  it('a process exit code of 0 never makes a run a success when the run record says FAILURE', () => {
    const out = deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'FAILURE', exit: 0, workflow: 'success' }), false, exited);
    expect(out?.kind).toBe('failure');
  });
  it('a workflow result of "success" without a terminal RunRecord is not a success', () => {
    const out = deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: null, workflow: 'success' }), false, exited);
    expect(out?.kind).toBe('interrupted');
  });
  it('a process that has not exited and has not sealed is not over (no outcome from silence)', () => {
    const running = { ...exited, phase: 'running', ended_at: null, exit: null } as const;
    expect(deriveOutcome(status({ phase: 'RUNNING', liveness: 'interrupted', terminal: undefined }), false, running)).toBeNull();
    expect(deriveOutcome(status({ phase: 'RUNNING', liveness: 'live', terminal: undefined }), false, exited)).toBeNull();
  });
  it('not recorded + the process still running is not over; not recorded + exited is "not_recorded"', () => {
    expect(deriveOutcome(null, true, { ...exited, phase: 'running', exit: null, ended_at: null } as const)).toBeNull();
    expect(deriveOutcome(null, true, exited)?.kind).toBe('not_recorded');
    expect(deriveOutcome(null, false, exited)).toBeNull(); // a read failure is not an outcome
  });
  it('a recorded approval requirement is "approval_required" - not a generic failure - whatever the run record says', () => {
    const approval = { kind: 'change_approval', reason: 'the change touches a sensitive path', details: { files: ['.github/workflows/ci.yml'] } };
    const out = deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', terminal: 'FAILURE', exit: 1, workflow: null, approval }), false, exited);
    expect(out).toMatchObject({ kind: 'approval_required', label: 'Approval required', approval });
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', run: false, exit: 1, approval }), false, exited)?.kind).toBe('approval_required');
  });
  it('a sealed launch with no run is "refused"', () => {
    expect(deriveOutcome(status({ phase: 'ENDED', liveness: 'ended', run: false, exit: 2, workflow: 'refused' }), false, exited)?.kind).toBe('refused');
  });
});
