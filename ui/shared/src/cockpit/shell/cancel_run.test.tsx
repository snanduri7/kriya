/**
 * T2: Cancel Run, Recover Project and the words about the folder. The host stops a process; KRIYA's recovery status, read after the process ended, decides what the cockpit says: Cancelled
 * (CLEAN), Cancelled - recovery required (any other status, with Recover Project), or Cancelled - folder state not confirmed (the read failed / still "run active"). It never says clean otherwise.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { FixtureExecution } from '../providers/fixture/fixtureExecution';
import { renderWorkspaceShell } from '../testing/harness';

const GOAL = 'Add a clamp(value, low, high) function with tests.';
const typeGoal = async () => fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: GOAL } });
const startButton = () => screen.getByRole('button', { name: /^Start Run$|^Starting…$/ });
const current = () => screen.findByRole('region', { name: 'Current run' });

async function startRun(setup: (exec: FixtureExecution) => void = () => undefined) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome: 'success' } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  await screen.findByText('What do you want Kriya to do?');
  setup(mounted.host.exec!);
  await typeGoal();
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(startButton());
  return { ...mounted, run: await current() };
}

describe('Cancel Run', () => {
  it('is offered while the run is active, and a cancellation Kriya confirms CLEAN reads "Cancelled" with the folder clean', async () => {
    const { host, run } = await startRun();
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')).toHaveTextContent('Cancelled'));
    expect(host.exec!.cancelCount).toBe(1);
    expect(run.querySelector('h2')!.textContent).toBe('Cancelled');
    expect(within(run).getByTestId('cancelled')).toHaveAttribute('data-folder', 'clean');
    expect(run).toHaveTextContent('cancelled by you');          // the badge says so - it never reads "Run failed" for a run the person cancelled
    expect(run).not.toHaveTextContent('Run failed');
    expect(run).toHaveTextContent(/Kriya reports the project folder is clean/);
    expect(within(run).queryByRole('button', { name: 'Recover Project' })).toBeNull();
    expect(within(run).queryByRole('button', { name: 'Cancel Run' })).toBeNull();      // nothing left to cancel
    expect(within(run).getByRole('button', { name: 'Run again' })).toBeInTheDocument();
    expect(within(run).getByRole('button', { name: 'Edit goal and retry' })).toBeInTheDocument();
  });

  it('while the host is stopping the process the button says Cancelling… and no second run can be started', async () => {
    const { host, run } = await startRun();
    let release: () => void = () => undefined;
    const real = host.exec!.port.cancelRun!;
    host.exec!.port.cancelRun = async () => { await new Promise<void>((r) => { release = r; }); return real(); };
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    expect(await within(run).findByRole('button', { name: 'Cancelling…' })).toBeDisabled();
    expect(run.querySelector('h2')).toHaveTextContent('Cancelling…');
    release();
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled'));
  });

  it('a status other than CLEAN is "Cancelled — recovery required" with Recover Project - and the folder is NOT called clean', async () => {
    const { host, run } = await startRun((exec) => { exec.recoveryStatus = 'RECOVERY_AVAILABLE'; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required'));
    expect(within(run).getByTestId('cancelled')).toHaveAttribute('data-folder', 'recovery');
    expect(run).toHaveTextContent('RECOVERY_AVAILABLE');
    expect(run).not.toHaveTextContent(/folder is clean/);
    expect(within(run).queryByRole('button', { name: 'Run again' })).toBeNull();      // not offered until the folder is settled
    expect(host.exec!.recoverCount).toBe(0);
  });

  it('Recover Project asks the host (which confirms natively and runs Kriya), re-reads Kriya\'s status, and only a CLEAN answer turns it into "Cancelled"', async () => {
    const { host, run } = await startRun((exec) => { exec.recoveryStatus = 'RECOVERY_AVAILABLE'; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required'));
    fireEvent.click(within(run).getByRole('button', { name: 'Recover Project' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled'));
    expect(host.exec!.recoverCount).toBe(1);
    expect(run).toHaveTextContent(/Kriya reports the project folder is clean/);
  });

  it('a recovery that leaves the folder not clean stays "recovery required" (no false clean)', async () => {
    const { host, run } = await startRun((exec) => { exec.recoveryStatus = 'RECOVERY_AVAILABLE'; exec.recoverLeavesStatus = 'MANUAL_ACTION_REQUIRED'; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required'));
    fireEvent.click(within(run).getByRole('button', { name: 'Recover Project' }));
    await waitFor(() => expect(host.exec!.recoverCount).toBe(1));
    await waitFor(() => expect(run).toHaveTextContent('MANUAL_ACTION_REQUIRED'));
    expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required');
  });

  it('a refused or declined recovery says so and changes nothing', async () => {
    const { host, run } = await startRun((exec) => { exec.recoveryStatus = 'RECOVERY_AVAILABLE'; exec.recoverRefusal = { code: 'CONFIRMATION_DECLINED', message: 'recovery was not confirmed; nothing was changed' }; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required'));
    fireEvent.click(within(run).getByRole('button', { name: 'Recover Project' }));
    expect(await within(run).findByTestId('recover-note')).toHaveTextContent('Recovery was not confirmed; nothing was changed.');
    expect(run.querySelector('h2')!.textContent).toBe('Cancelled — recovery required');
    expect(host.exec!.recoverCount).toBe(1);
  });

  it('when Kriya\'s status cannot be read the folder is "not confirmed" - never clean', async () => {
    const { run } = await startRun((exec) => { exec.statusReadFails = true; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — folder state not confirmed'));
    expect(within(run).getByTestId('cancelled')).toHaveAttribute('data-folder', 'unconfirmed');
    expect(run).not.toHaveTextContent(/folder is clean/);
    expect(within(run).getByRole('button', { name: 'Recover Project' })).toBeInTheDocument();
  });

  it('a status that still says RUN_ACTIVE after the bounded wait is "not confirmed", not clean', async () => {
    const { run } = await startRun((exec) => { exec.recoveryStatus = 'RUN_ACTIVE'; });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled — folder state not confirmed'), { timeout: 4000 });
    expect(run).toHaveTextContent(/still reports a run in progress/);
  });

  it('a cancel that stopped nothing (the run had just ended) claims no cancellation and the ordinary outcome follows', async () => {
    const { host, run } = await startRun();
    host.exec!.port.cancelRun = async () => ({ cancelled: false, reason: 'no run is in progress' });
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await new Promise((r) => setTimeout(r, 50));
    expect(run.querySelector('h2')!.textContent).not.toMatch(/Cancelled/);
    act(() => host.exec!.finish());
    await waitFor(() => expect(run).toHaveAttribute('data-outcome', 'success'));
  });

  it('New Goal after a cancellation starts blank and a new run can then be started', async () => {
    const { host, run } = await startRun();
    fireEvent.click(within(run).getByRole('button', { name: 'Cancel Run' }));
    await waitFor(() => expect(run.querySelector('h2')!.textContent).toBe('Cancelled'));
    fireEvent.click(within(run).getByRole('button', { name: 'New Goal' }));
    expect(await screen.findByRole('textbox', { name: 'Engineering goal' })).toHaveValue('');
    await typeGoal();
    await waitFor(() => expect(startButton()).toBeEnabled());
    fireEvent.click(startButton());
    await waitFor(() => expect(host.exec!.starts).toHaveLength(2));
  });
});
