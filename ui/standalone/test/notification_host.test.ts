/** Native notifications, host side: three closed kinds, fixed text built by the host, current launch only, once, never while the window is focused. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { APPROVAL_REPEAT_GAP_MS, APPROVAL_REPEAT_MAX, NOTIFY_KINDS, NotificationHost, folderLabel, notificationText } from '../src/main/notification_host';

const LID = '20261011T101010-abcdef01';
function make(over: Partial<{ supported: boolean; focused: boolean; launch: string | null; path: string | null }> = {}) {
  let clock = 1_000_000;
  const state = { supported: true, focused: false, launch: LID as string | null, path: '/Users/p/work/my-app' as string | null, ...over };
  const shown: Array<{ title: string; body: string }> = [];
  const clicks: Array<() => void> = [];
  let activations = 0;
  const host = new NotificationHost({
    supported: () => state.supported, windowFocused: () => state.focused, currentLaunch: () => state.launch, workspacePath: () => state.path,
    show: (spec, onClick) => { shown.push(spec); clicks.push(onClick); }, onActivate: () => { activations += 1; }, now: () => clock,
  });
  return { host, shown, clicks, state, activations: () => activations, tick: (ms: number) => { clock += ms; } };
}

describe('what is accepted', () => {
  it('exactly {kind, launch_id}: anything else is invalid and shows nothing', () => {
    const { host, shown } = make();
    for (const raw of [null, 'x', 7, [], {}, { kind: 'approval_required' }, { launch_id: LID }, { kind: 'approval_required', launch_id: LID, body: 'my own text' }, { kind: 'approval_required', launch_id: LID, title: 'x' },
      { kind: 'gate_passed', launch_id: LID }, { kind: 7, launch_id: LID }, { kind: 'approval_required', launch_id: 'not-a-launch' }, { kind: 'approval_required', launch_id: 7 }, { kind: '__proto__', launch_id: LID }]) {
      expect(host.notify(raw), JSON.stringify(raw)).toEqual({ shown: false, reason: 'invalid' });
    }
    expect(shown).toEqual([]);
  });
  it('only for the launch this host started', () => {
    const { host, shown } = make({ launch: '20261011T101010-00000000' });
    expect(host.notify({ kind: 'run_succeeded', launch_id: LID })).toEqual({ shown: false, reason: 'unknown_launch' });
    expect(make({ launch: null }).host.notify({ kind: 'run_succeeded', launch_id: LID })).toEqual({ shown: false, reason: 'unknown_launch' });
    expect(shown).toEqual([]);
  });
});

describe('what is shown', () => {
  it.each(NOTIFY_KINDS)('%s is built from fixed wording and the folder name only', (kind) => {
    const { host, shown } = make();
    expect(host.notify({ kind, launch_id: LID })).toEqual({ shown: true, reason: 'shown' });
    expect(shown).toEqual([notificationText(kind, 'my-app')]);
    expect(shown[0]!.body).toContain('in my-app');
    expect(JSON.stringify(shown)).not.toMatch(/\/Users|diff|source|reviewer/i); // no path, diff, source or reviewer text can reach a notification
  });
  it('hostile folder names are cleaned (control and bidi characters removed, bounded) and an empty name is omitted', () => {
    expect(folderLabel('/a/b‮evil\u0007\n')).toBe('bevil');
    expect(folderLabel('/a/' + 'x'.repeat(500))).toHaveLength(60);
    expect(folderLabel('/a/​‮')).toBeNull();
    expect(folderLabel(null)).toBeNull();
    expect(notificationText('approval_required', null).body).not.toContain(' in ');
  });
  it('the wording states what is recorded, never more: success says "success recorded by Kriya", failure says "without a success recorded"', () => {
    expect(notificationText('run_succeeded', 'x').body).toContain('success recorded by Kriya');
    expect(notificationText('run_unsuccessful', 'x').body).toContain('without a success recorded by Kriya');
    expect(notificationText('approval_required', 'x').body).toContain('Nothing is applied until you decide');
  });
});

describe('when it is shown', () => {
  it('not while the window is focused, and the event is consumed (a later blur does not replay it)', () => {
    const { host, shown, state } = make({ focused: true });
    expect(host.notify({ kind: 'run_succeeded', launch_id: LID })).toEqual({ shown: false, reason: 'focused' });
    state.focused = false;
    expect(host.notify({ kind: 'run_succeeded', launch_id: LID })).toEqual({ shown: false, reason: 'duplicate' });
    expect(shown).toEqual([]);
  });
  it('not when the OS does not support notifications (and nothing is attempted)', () => {
    const { host, shown } = make({ supported: false });
    expect(host.notify({ kind: 'run_succeeded', launch_id: LID })).toEqual({ shown: false, reason: 'unsupported' });
    expect(shown).toEqual([]);
  });
  it('a finished/unsuccessful notification is sent once per launch', () => {
    const { host, shown } = make();
    expect(host.notify({ kind: 'run_unsuccessful', launch_id: LID }).shown).toBe(true);
    expect(host.notify({ kind: 'run_unsuccessful', launch_id: LID })).toEqual({ shown: false, reason: 'duplicate' });
    expect(host.notify({ kind: 'run_succeeded', launch_id: LID }).shown).toBe(true); // a different kind is a different event
    expect(shown).toHaveLength(2);
  });
  it('approval notifications may repeat for a retry, but not closer than the gap and not more than the cap', () => {
    const { host, shown, tick } = make();
    expect(host.notify({ kind: 'approval_required', launch_id: LID }).shown).toBe(true);
    expect(host.notify({ kind: 'approval_required', launch_id: LID })).toEqual({ shown: false, reason: 'duplicate' });
    tick(APPROVAL_REPEAT_GAP_MS - 1);
    expect(host.notify({ kind: 'approval_required', launch_id: LID }).shown).toBe(false);
    for (let i = 1; i < APPROVAL_REPEAT_MAX; i += 1) { tick(APPROVAL_REPEAT_GAP_MS); expect(host.notify({ kind: 'approval_required', launch_id: LID }).shown, `repeat ${i}`).toBe(true); }
    tick(APPROVAL_REPEAT_GAP_MS);
    expect(host.notify({ kind: 'approval_required', launch_id: LID })).toEqual({ shown: false, reason: 'duplicate' });
    expect(shown).toHaveLength(APPROVAL_REPEAT_MAX);
  });
  it('clicking a notification activates the window (the host sends the fixed showRun command); the click carries no data', () => {
    const { host, clicks, activations } = make();
    host.notify({ kind: 'approval_required', launch_id: LID });
    expect(activations()).toBe(0);
    clicks[0]!();
    expect(activations()).toBe(1);
  });
});

describe('wiring', () => {
  const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
  it('the notify channel checks the sender first and passes only the raw request to the host; an evidence/dev build never raises a real notification', () => {
    const at = main.indexOf('IPC_CHANNELS.notifyRun');
    const body = main.slice(at, at + 300);
    expect(body.indexOf('fromOwnedRenderer(event)')).toBeGreaterThan(-1);
    expect(body.indexOf('fromOwnedRenderer(event)')).toBeLessThan(body.indexOf('notifications.notify(raw)'));
    expect(main).toContain('supported: () => dev === null && Notification.isSupported()');
  });
  it('a click sends only the fixed showRun command through the menu channel', () => {
    expect(main).toContain("ownedWindow.webContents.send(MENU_CHANNEL, 'showRun' satisfies MenuCommand)");
  });
});
