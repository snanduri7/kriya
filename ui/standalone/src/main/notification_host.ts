/**
 * Native notifications (end-user experience), host side. Pure: Electron's Notification, window focus and the clock are injected.
 *
 * Three events only - a change is waiting for approval, a run finished with success, a run ended without success - because those are the three moments a person
 * who left Kriya in the background must come back for. No gate, event or progress notifications.
 *
 * What may cross: the renderer asks with a CLOSED kind and the launch id, and only AFTER Kriya's durable records produced that state (the cockpit derives the outcome;
 * this host never reads a record). The host then builds the text ITSELF from fixed wording plus the open folder's name: nothing the renderer sends is displayed,
 * so a notification can never carry a diff, a file path, source, a reviewer's words or any model text. A request is honoured only for the launch this host
 * started, once per (kind, launch), only when the window is not focused (a person looking at Kriya needs no banner), and only if the OS supports notifications.
 * Clicking a notification focuses the window and sends the fixed `showRun` menu command; no payload travels back.
 */
import { LAUNCH_ID_RE } from './ipc_contract';

export const NOTIFY_KINDS = ['approval_required', 'runtime_approval_required', 'run_succeeded', 'run_unsuccessful'] as const;
export type NotifyKind = (typeof NOTIFY_KINDS)[number];
export interface NotifyRequest { kind: NotifyKind; launch_id: string }
export type NotifyReason = 'shown' | 'invalid' | 'unknown_launch' | 'duplicate' | 'focused' | 'unsupported';
export interface NotifyResult { shown: boolean; reason: NotifyReason }

export interface NotificationDeps {
  supported(): boolean;
  windowFocused(): boolean;
  /** The launch id of the run this host started (running or last), or null. */
  currentLaunch(): string | null;
  /** The open folder's path (only its last segment is used, sanitised). */
  workspacePath(): string | null;
  show(spec: { title: string; body: string }, onClick: () => void): void;
  onActivate(): void;
  now(): number;
}

const NAME_MAX = 60;
/** A retry inside one launch can raise a second review (the renderer asks once per review): approval notifications may repeat per launch, at most this many. */
export const APPROVAL_REPEAT_MAX = 4;

/** The folder's last path segment with control and bidi characters removed, bounded; null when nothing printable remains. */
export function folderLabel(path: string | null): string | null {
  if (path === null) return null;
  const last = path.replace(/\/+$/, '').split('/').pop() ?? '';
  // eslint-disable-next-line no-control-regex -- control and bidi-override characters are exactly what is removed
  const clean = last.replace(/[\u0000-\u001f\u007f​-‏‪-‮⁦-⁩]/g, '').trim().slice(0, NAME_MAX);
  return clean.length ? clean : null;
}

export function notificationText(kind: NotifyKind, folder: string | null): { title: string; body: string } {
  const where = folder ? ` in ${folder}` : '';
  switch (kind) {
    case 'runtime_approval_required': return { title: 'Kriya needs your permission', body: `Kriya wants to run verification commands${where}. Nothing further is run until you decide.` };
    case 'approval_required': return { title: 'Kriya needs your approval', body: `A proposed change${where} is waiting for your review. Nothing is applied until you decide.` };
    case 'run_succeeded': return { title: 'Kriya run finished', body: `The run${where} ended with success recorded by Kriya. Open Kriya to see what changed.` };
    case 'run_unsuccessful': return { title: 'Kriya run did not finish successfully', body: `The run${where} ended without a success recorded by Kriya. Open Kriya for the details.` };
  }
}

export class NotificationHost {
  private readonly sent = new Set<string>();
  private readonly approvals = new Map<string, { count: number; at: number }>();

  constructor(private readonly deps: NotificationDeps) {}

  notify(raw: unknown): NotifyResult {
    if (typeof raw !== 'object' || raw === null || Array.isArray(raw) || Object.keys(raw).sort().join() !== 'kind,launch_id') return { shown: false, reason: 'invalid' };
    const { kind, launch_id: launch } = raw as Record<string, unknown>;
    if (typeof kind !== 'string' || !(NOTIFY_KINDS as readonly string[]).includes(kind)) return { shown: false, reason: 'invalid' };
    if (typeof launch !== 'string' || !LAUNCH_ID_RE.test(launch)) return { shown: false, reason: 'invalid' };
    if (this.deps.currentLaunch() !== launch) return { shown: false, reason: 'unknown_launch' };
    if (kind === 'approval_required' || kind === 'runtime_approval_required') {
      const counter = `${kind}:${launch}`;   // counted per SUBJECT: a run with several source reviews must not use up the allowance of its one runtime review
      const seen = this.approvals.get(counter);
      if (seen && seen.count >= APPROVAL_REPEAT_MAX) return { shown: false, reason: 'duplicate' };
      this.approvals.set(counter, { count: (seen?.count ?? 0) + 1, at: this.deps.now() }); // consumed even when not shown: a later focus change must not replay an old event
      while (this.approvals.size > 20) this.approvals.delete(this.approvals.keys().next().value as string);
    } else {
      const key = `${kind}:${launch}`;
      if (this.sent.has(key)) return { shown: false, reason: 'duplicate' };
      this.sent.add(key);
      while (this.sent.size > 50) this.sent.delete(this.sent.values().next().value as string);
    }
    if (!this.deps.supported()) return { shown: false, reason: 'unsupported' };
    if (this.deps.windowFocused()) return { shown: false, reason: 'focused' };
    this.deps.show(notificationText(kind as NotifyKind, folderLabel(this.deps.workspacePath())), () => this.deps.onActivate());
    return { shown: true, reason: 'shown' };
  }
}
