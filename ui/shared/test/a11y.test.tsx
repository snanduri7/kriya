/** Keyboard and accessibility hardening (2026-10-04): settings dialog focus management, tab keyboard pattern, listbox
 * semantics, filter announcements, hidden panels and closed drawers. Behaviour and roles only; no CSS class assertions. */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App } from '../src/App';
import { FakeHost, env, withSnapshots } from './fakeHost';
import type { Availability, RunDetail, RunEvent, RunSummary } from '../src/model/kup';
import serializerFixture from '../../fixtures/serializer/run_events.json';

const EVENTS = (serializerFixture as unknown as { run_events: RunEvent[] }).run_events;
const run = (id: string): RunSummary => ({ run_id: id, timestamp: '2026-09-01 10:00:00', goal: `goal of ${id}`, duration_sec: 3.5, attempts: 2, status: 'failure', failure_category: 'quality_gate_failed', files_modified: 'a.py' });
function detailWith(events: RunEvent[], availability: Availability = 'recorded'): RunDetail {
  const sec = <T,>(data: T) => ({ availability, data: availability === 'recorded' ? data : null, reason: availability === 'recorded' ? null : `fixture ${availability}`, provenance: 'fixture' });
  return { run: run('r1'), fields: {}, run_events: sec(events), evidence_records: sec([]), gate_outcomes: sec([{ attempt: 1, type: 'compile', success: false, output: 'boom' }]), model_hops: sec([]), generation_metrics: sec({}), failure_report: sec([]), context: sec({ items: [], tokens: null }), attribution: sec({ cause: 'c', evidence_ids: [] }), diagnostics: sec({}), comparisons: sec([{ path: 'a.py', before: { text: 'a\n', provenance: 'p', revision: 'r1' }, after: { text: 'b\n', provenance: 'p', revision: 'r2' } }]), output: sec('out') } as RunDetail;
}
function host(detail: RunDetail) {
  return new FakeHost(withSnapshots((req) => {
    switch (req.operation) {
      case 'capabilities': return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
      case 'history.list': return env('history.list', { runs: [run('r1')], next_cursor: null });
      case 'history.detail': return env('history.detail', detail);
      default: return env(req.operation, null);
    }
  }));
}
async function openRun(detail = detailWith(EVENTS)) {
  const h = host(detail);
  render(<App host={h} />);
  await waitFor(() => expect(screen.getByRole('button', { name: 'Acquire new snapshot' })).toBeEnabled());
  fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
  await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
  fireEvent.click(screen.getByText('goal of r1'));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'goal of r1' })).toBeInTheDocument());
  await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled());
  return h;
}
const focusable = (root: HTMLElement) => Array.from(root.querySelectorAll<HTMLElement>('a[href], button, input, select, textarea, [tabindex]')).filter((el) => !el.hasAttribute('disabled') && el.tabIndex >= 0 && !el.closest('[hidden]'));

describe('settings dialog: focus management', () => {
  it('receives initial focus on its title, traps Tab and Shift+Tab, closes with Escape and restores focus to the Settings button', async () => {
    await openRun();
    const opener = screen.getByRole('button', { name: 'Settings' });
    opener.focus();
    fireEvent.click(opener);
    const dialog = await screen.findByRole('dialog', { name: 'Settings' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Settings' })).toHaveFocus());
    await screen.findByLabelText('Configuration directory');
    // the background is inert while the dialog is open: nothing outside it is reachable
    expect(opener.closest('[inert]')).not.toBeNull();
    const controls = focusable(dialog);
    const first = controls[0]!, last = controls[controls.length - 1]!;
    expect(first).toHaveTextContent('Close settings'); expect(last).toHaveTextContent('Save preferred editor');
    last.focus(); fireEvent.keyDown(dialog, { key: 'Tab' });
    expect(first).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Tab', shiftKey: true });
    expect(last).toHaveFocus();
    fireEvent.keyDown(dialog, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toHaveFocus());
    expect(screen.getByRole('button', { name: 'Settings' }).closest('[inert]')).toBeNull();
  });
  it('a rejected value marks exactly that field invalid and describes it with the error; the error is an alert, the save result a status', async () => {
    await openRun();
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    const field = await screen.findByLabelText('Configuration directory');
    fireEvent.change(field, { target: { value: 'relative/path' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    const alert = screen.getByRole('alert');
    expect(alert).toHaveAttribute('id', 'settings-error');
    expect(field).toHaveAttribute('aria-invalid', 'true');
    expect(field.getAttribute('aria-describedby')!.split(' ')).toEqual(expect.arrayContaining(['help-configDirectory', 'settings-error']));
    expect(screen.getByLabelText('Recovery workspace')).not.toHaveAttribute('aria-invalid');
    fireEvent.change(field, { target: { value: '/abs/config' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    await screen.findByText('Saved. No snapshot was acquired.');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByRole('status', { name: '' }).textContent ?? '').not.toBe(''); // one status region carries the save result
    expect(screen.getAllByRole('status').filter((s) => s.closest('[role="dialog"]'))).toHaveLength(1);
  });
});

describe('inspector tabs: standard tab keyboard pattern', () => {
  it('ArrowRight/ArrowLeft/Home/End move selection and focus with a roving tabindex; inactive panels are hidden and hold no focusable control', async () => {
    await openRun();
    const tablist = screen.getByRole('tablist', { name: 'Inspector sections' });
    const tabs = within(tablist).getAllByRole('tab');
    expect(tabs.map((t) => t.getAttribute('tabindex'))).toEqual(['0', '-1', '-1', '-1', '-1']);
    tabs[0]!.focus();
    fireEvent.keyDown(tablist, { key: 'ArrowRight' });
    expect(tabs[1]).toHaveAttribute('aria-selected', 'true'); expect(tabs[1]).toHaveFocus(); expect(tabs[0]).toHaveAttribute('tabindex', '-1');
    fireEvent.keyDown(tablist, { key: 'End' });
    expect(tabs[4]).toHaveAttribute('aria-selected', 'true'); expect(tabs[4]).toHaveFocus();
    fireEvent.keyDown(tablist, { key: 'ArrowRight' }); // wraps
    expect(tabs[0]).toHaveFocus();
    fireEvent.keyDown(tablist, { key: 'ArrowLeft' });
    expect(tabs[4]).toHaveFocus();
    fireEvent.keyDown(tablist, { key: 'Home' });
    expect(tabs[0]).toHaveFocus();
    for (const tab of tabs) {
      const panel = document.getElementById(tab.getAttribute('aria-controls')!)!;
      expect(panel).toHaveAttribute('role', 'tabpanel'); expect(panel).toHaveAttribute('aria-labelledby', tab.id);
      if (tab.getAttribute('aria-selected') !== 'true') { expect(panel).toHaveAttribute('hidden'); expect(focusable(panel)).toEqual([]); }
    }
  });
});

describe('timeline: listbox semantics, selection under filtering, announcements', () => {
  const list = () => screen.getByRole('listbox', { name: /recorded events/i });
  const options = () => within(list()).getAllByRole('option');
  const search = (q: string) => fireEvent.change(screen.getByRole('searchbox', { name: 'Search recorded events' }), { target: { value: q } });
  it('arrow keys move the single selection; the active option is exposed through aria-activedescendant on the focused list', async () => {
    await openRun();
    list().focus();
    expect(list()).not.toHaveAttribute('aria-activedescendant');
    fireEvent.keyDown(list(), { key: 'ArrowDown' });
    await waitFor(() => expect(options()[0]).toHaveAttribute('aria-selected', 'true'));
    expect(list()).toHaveAttribute('aria-activedescendant', options()[0]!.id);
    expect(list()).toHaveFocus();
    fireEvent.keyDown(list(), { key: 'ArrowDown' });
    await waitFor(() => expect(options()[1]).toHaveAttribute('aria-selected', 'true'));
    expect(list()).toHaveAttribute('aria-activedescendant', options()[1]!.id);
    expect(options().filter((o) => o.getAttribute('aria-selected') === 'true')).toHaveLength(1);
    fireEvent.keyDown(list(), { key: 'End' });
    await waitFor(() => expect(options()[3]).toHaveAttribute('aria-selected', 'true'));
    fireEvent.keyDown(list(), { key: 'Home' });
    await waitFor(() => expect(options()[0]).toHaveAttribute('aria-selected', 'true'));
  });
  it('"Clear filter to show the selected event" is a button; activating it keeps the selection and moves focus to the list, never to <body>', async () => {
    await openRun();
    fireEvent.click(options()[3]!);
    search('context');
    const clear = screen.getByRole('button', { name: 'Clear filter to show the selected event' });
    clear.focus(); expect(clear).toHaveFocus();
    fireEvent.click(clear);
    await waitFor(() => expect(list()).toHaveFocus());
    expect(document.activeElement).not.toBe(document.body);
    const selected = options().filter((o) => o.getAttribute('aria-selected') === 'true');
    expect(selected).toHaveLength(1); expect(selected[0]).toHaveTextContent('#4');
    expect(list()).toHaveAttribute('aria-activedescendant', selected[0]!.id);
  });
  it('the filter count is visible at once, but the live region announces once typing pauses; the search box is named by its label only', async () => {
    await openRun();
    const status = screen.getByRole('status', { name: 'Event filter result' });
    await waitFor(() => expect(status).toHaveTextContent('4 of 4 recorded events shown')); // the run's count, announced once after it loaded
    const box = screen.getByRole('searchbox', { name: 'Search recorded events' });
    expect(box).not.toHaveAttribute('aria-label'); // one name source (the label), not two
    search('mo'); search('mod'); search('model');
    expect(screen.getByText('2 of 4 recorded events shown (filtered; recorded order kept)')).toBeInTheDocument(); // visible count: immediate
    expect(status).toHaveTextContent('4 of 4 recorded events shown'); // live region: not yet (no announcement per keystroke)
    await waitFor(() => expect(status).toHaveTextContent('2 of 4 recorded events shown (filtered; recorded order kept)'));
    expect(screen.getAllByRole('status').filter((s) => s.getAttribute('aria-label') === 'Event filter result')).toHaveLength(1);
  });
});

describe('drawer and availability statements', () => {
  it('a closed drawer exposes only its bar controls; its tabs still control existing, hidden, empty panels; opening reveals content', async () => {
    await openRun();
    const drawer = screen.getByRole('region', { name: /Drawer/ }).closest('section') ?? screen.getByLabelText('Drawer: diff and why');
    // the tablist is one Tab stop (roving tabindex): the bar exposes the toggle button and the active tab only
    const stops = focusable(drawer); expect(stops).toHaveLength(2); expect(stops[0]).toHaveTextContent('Show drawer'); expect(stops[1]).toHaveAttribute('role', 'tab'); expect(stops[1]).toHaveAttribute('aria-selected', 'true');
    for (const tab of within(drawer).getAllByRole('tab')) { const panel = document.getElementById(tab.getAttribute('aria-controls')!)!; expect(panel).toHaveAttribute('hidden'); expect(panel.textContent).toBe(''); }
    fireEvent.click(within(drawer).getByRole('tab', { name: 'Why' }));
    expect(within(drawer).getByRole('button', { name: 'Hide drawer' })).toHaveAttribute('aria-expanded', 'true');
    expect(within(drawer).getByRole('tabpanel')).toHaveTextContent('Recorded failure category');
    fireEvent.click(within(drawer).getByRole('button', { name: 'Hide drawer' }));
    expect(within(drawer).queryByRole('tabpanel')).not.toBeInTheDocument();
    expect(focusable(drawer)).toHaveLength(2);
  });
  it('unavailable data is stated in words as a note (not a live region) and unresolved references are stated in words', async () => {
    await openRun(detailWith(EVENTS, 'unreadable'));
    const notes = screen.getAllByRole('note');
    expect(notes.some((n) => /Run events:\s*unreadable/.test(n.textContent ?? ''))).toBe(true);
    expect(screen.queryAllByRole('status').filter((s) => /unreadable/.test(s.textContent ?? ''))).toHaveLength(0);
  });
});
