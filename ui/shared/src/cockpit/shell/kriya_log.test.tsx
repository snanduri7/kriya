/**
 * KRIYA LOG (owner ruling 2026-11-07): the bottom dock's real application log - Activity stays the default, the Kriya Log is a separate tab that renders what the backend projection returned (fixture data
 * here), follows new lines only while the person is at the bottom, offers Jump to latest, filters by level, copies only what is shown, and is one click from a failed run.
 */
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { logLineText } from '../core/KriyaLogView';
import { renderWorkspaceShell } from '../testing/harness';

const banner = () => screen.findByRole('region', { name: 'Current run' });

async function running(outcome: 'success' | 'failure' = 'success', frames = 3) {
  const mounted = renderWorkspaceShell({ fixture: { workspace: '/fixture/workspace', execution: { outcome } } });
  await screen.findByRole('navigation', { name: 'Destinations' });
  fireEvent.change(await screen.findByRole('textbox', { name: 'Engineering goal' }), { target: { value: 'Create hello.py that prints hello world' } });
  await screen.findAllByText(/^✓ Ready/);
  fireEvent.click(screen.getByRole('button', { name: /^Start Run$/ }));
  const bar = await banner();
  for (let i = 0; i < frames; i += 1) act(() => mounted.host.exec!.advance());
  return { mounted, bar };
}

describe('the Kriya Log dock tab', () => {
  it('Activity is the default view; the Kriya Log is a separate tab next to it, then Build and Tests', async () => {
    await running();
    const dock = await screen.findByTestId('activity-dock');
    const tabs = within(dock).getAllByRole('tab').map((t) => t.textContent);
    expect(tabs).toEqual(['Activity', 'Kriya Log', 'Build', 'Tests']);
    expect(within(dock).getByRole('tab', { name: 'Activity' })).toHaveAttribute('aria-selected', 'true');
    expect(within(dock).queryByTestId('kriya-log')).toBeNull();
  });

  it('shows the real log lines the backend returned (time, level, logger, message), never mixed into Activity, and follows the live run', async () => {
    const { mounted } = await running();
    const dock = await screen.findByTestId('activity-dock');
    fireEvent.click(within(dock).getByRole('tab', { name: 'Kriya Log' }));
    const view = await within(dock).findByTestId('kriya-log');
    await waitFor(() => expect(within(view).getByTestId('kriya-log-count').textContent).toMatch(/^\d+ lines?$/));
    expect(within(view).getByText('Starting Kriya Kernel...')).toBeInTheDocument();
    expect(view.textContent).toContain('core.kernel');
    expect(within(dock).queryByTestId('activity-list')).toBeNull();                              // the Activity list is not the log
    for (let i = 0; i < 6; i += 1) act(() => mounted.host.exec!.advance());
    await waitFor(() => expect(within(view).getByTestId('kriya-log-count').textContent).toMatch(/^8 lines$/), { timeout: 6000 });   // live: the new lines arrived by polling
  });

  it('filters by level and copies only the lines that are shown, exactly as the log writes them', async () => {
    await running("success", 4);
    const dock = await screen.findByTestId('activity-dock');
    fireEvent.click(within(dock).getByRole('tab', { name: 'Kriya Log' }));
    const view = await within(dock).findByTestId('kriya-log');
    await waitFor(() => expect(within(view).getByTestId('kriya-log-count').textContent).toMatch(/^[1-9]\d* lines?$/));
    fireEvent.change(within(view).getByRole('combobox', { name: 'Log level' }), { target: { value: 'warnings' } });
    await waitFor(() => expect(within(view).getByTestId('kriya-log-count').textContent).toMatch(/of \d+ lines$/));
    const rows = within(view).getByTestId('kriya-log-scroll').querySelectorAll('.ck-logline');
    expect(rows.length).toBeGreaterThan(0);
    expect(Array.from(rows).every((r) => ['WARNING', 'ERROR', 'CRITICAL'].includes(r.getAttribute('data-level') ?? ''))).toBe(true);
    expect(logLineText({ time: '2026-10-10 10:10:12,001', level: 'INFO', logger: 'kriya.core.kernel', message: 'hello' })).toBe('2026-10-10 10:10:12,001 INFO kriya.core.kernel: hello');
  });

  it('stops following when the person scrolls up and offers Jump to latest, which brings them back', async () => {
    const { mounted } = await running("success", 4);
    const dock = await screen.findByTestId('activity-dock');
    fireEvent.click(within(dock).getByRole('tab', { name: 'Kriya Log' }));
    const view = await within(dock).findByTestId('kriya-log');
    const scroll = within(view).getByTestId('kriya-log-scroll');
    await waitFor(() => expect(within(view).getByTestId('kriya-log-count').textContent).toMatch(/^[1-9]/));
    Object.defineProperty(scroll, 'scrollHeight', { configurable: true, value: 1000 });
    Object.defineProperty(scroll, 'clientHeight', { configurable: true, value: 200 });
    scroll.scrollTop = 0;
    fireEvent.scroll(scroll);                                                                      // the person scrolled up
    const jump = await within(view).findByTestId('jump-to-latest');
    act(() => mounted.host.exec!.advance());
    expect(scroll.scrollTop).toBe(0);                                                              // new lines do not drag them back down
    fireEvent.click(jump);
    expect(scroll.scrollTop).toBe(1000);
    expect(within(view).queryByTestId('jump-to-latest')).toBeNull();
  });

  it('a failed run offers View Kriya Log, which opens the dock on the log', async () => {
    const { bar, mounted } = await running('failure', 4);
    for (let i = 0; i < 3; i += 1) act(() => mounted.host.exec!.advance());
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'failure'));
    fireEvent.click(await within(bar).findByTestId('view-kriya-log'));
    const dock = await screen.findByTestId('activity-dock');
    await waitFor(() => expect(within(dock).getByRole('tab', { name: 'Kriya Log' })).toHaveAttribute('aria-selected', 'true'));
    expect(await within(dock).findByTestId('kriya-log')).toBeInTheDocument();
  });

  it('a successful result does not show View Kriya Log as a failure action', async () => {
    const { bar } = await running('success', 6);
    await waitFor(() => expect(bar).toHaveAttribute('data-outcome', 'success'));
    expect(within(bar).queryByTestId('view-kriya-log')).toBeNull();
  });
});
