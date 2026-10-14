/**
 * Configuration approval, host side. Pure: no Electron import, no `child_process` import - the Kriya call and the open-workspace check are injected, so every rule
 * is tested with controlled executors.
 *
 * What this module IS: a narrow proxy. It takes ONE typed request {workspace, fingerprint}, checks it against its closed grammar and against the folder currently
 * open in this host, asks KRIYA (`configuration.approve`) and reports Kriya's own answer.
 *
 * The human commitment is HERE, in the main process: after the request is validated and before Kriya is asked, `confirm` shows a native confirmation whose text is
 * built from host state only (the open folder's name) and carries no setting, value, fingerprint or renderer-supplied string. The renderer review explains what is
 * being approved; this confirmation is the commitment. Declined (or an error showing it) means Kriya is never asked.
 *
 * What it is NOT: an authority. It never writes an approval, a file or a record; it cannot approve "whatever is current" (the fingerprint is the one Kriya gave
 * with the status the person reviewed, and Kriya re-resolves the configuration and refuses with APPROVAL_STALE when it differs); it carries no setting, value,
 * path or digest to trust; it has no auto-approval. The decision to ask is the person's click in the renderer; the decision to record is Kriya's.
 */
import { basename } from 'node:path';
import { validateApproveConfiguration } from './ipc_contract';

export interface ApproveConfigurationResult { approved: boolean; outcome: string | null; code: string | null; message: string | null; reason: string | null }

/** The native confirmation, as `dialog.showMessageBox` options. Cancel is first, the default and the Escape action; only `Approve Configuration` (index 1) commits. */
export const CONFIRM_BUTTONS = ['Cancel', 'Approve Configuration'] as const;
export const CONFIRM_APPROVE_INDEX = 1;
export interface ConfirmationDialog { type: 'question'; title: string; message: string; detail: string; buttons: string[]; defaultId: number; cancelId: number; noLink: true }
/** A folder name can hold anything; the native text shows it without control characters and at a bounded length. */
export function projectLabel(workspace: string): string {
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is being removed
  const name = basename(workspace).replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  return name.length > 80 ? `${name.slice(0, 79)}…` : name || 'this project';
}
/** The whole text of the native confirmation. It is a function of the folder name alone: no setting, value, path, fingerprint or renderer-supplied text can enter it. */
export function confirmationDialog(workspace: string): ConfirmationDialog {
  return {
    type: 'question', title: 'Approve Configuration', message: 'Approve this project\'s protected configuration?',
    detail: `Kriya will be allowed to use the operator-controlled settings you just reviewed for ${projectLabel(workspace)}. If those settings change, approval will be required again.`,
    buttons: [...CONFIRM_BUTTONS], defaultId: 0, cancelId: 0, noLink: true,
  };
}
/** True only for the explicit `Approve Configuration` button. */
export const isApproveResponse = (response: number): boolean => response === CONFIRM_APPROVE_INDEX;

export interface ConfigApprovalDeps {
  /** Kriya's `configuration.approve` envelope for {workspace, fingerprint}. */
  approve(workspace: string, fingerprint: string): Promise<unknown>;
  /** The native confirmation (main process). Resolves true only when the person pressed `Approve Configuration`; it is given the OPEN folder, never anything the renderer sent. */
  confirm(workspace: string): Promise<boolean>;
  currentWorkspace(): string | null;
  log?(line: string): void;
}

const refused = (code: string, message: string, reason: string | null = null): ApproveConfigurationResult => ({ approved: false, outcome: null, code, message, reason });
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null);
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

export class ConfigApprovalHost {
  private inFlight = false;

  constructor(private readonly deps: ConfigApprovalDeps) {}

  async approve(raw: unknown): Promise<ApproveConfigurationResult> {
    const request = validateApproveConfiguration(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    // One approval at a time: a double click is refused here, before Kriya is asked; Kriya treats a repeat as already_approved or stale on its own state.
    if (this.inFlight) return refused('APPROVAL_IN_FLIGHT', 'an approval is already being recorded');
    const { workspace, fingerprint } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; a configuration can only be approved for the open folder');
    this.inFlight = true; // before the confirmation is awaited: a second request while the native dialog is up is refused
    try {
      let confirmed: boolean;
      try {
        confirmed = await this.deps.confirm(workspace);
      } catch (error) {
        this.deps.log?.(`configuration.approve confirmation: ${error instanceof Error ? error.message : String(error)}`);
        return refused('HOST_ERROR', 'The confirmation could not be shown; nothing was approved');
      }
      // The person chose not to approve: a normal decision, not a failure. Kriya is not asked and nothing is written.
      if (!confirmed) return { approved: false, outcome: 'declined', code: null, message: null, reason: null };
      // The sheet was up while other requests flowed: approve only if the confirmed folder is still the open one.
      if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'the open folder changed while the confirmation was shown; nothing was approved');
      let envelope: unknown;
      try {
        envelope = await this.deps.approve(workspace, fingerprint);
      } catch (error) {
        this.deps.log?.(`configuration.approve: ${error instanceof Error ? error.message : String(error)}`);
        return refused('HOST_ERROR', 'Kriya could not be asked to record the approval; nothing is known to be approved - check the configuration status before trying again');
      }
      if (!isObject(envelope) || !('error' in envelope) || !('data' in envelope)) return refused('INVALID_RESPONSE', 'Kriya returned something that is not a KUP envelope');
      if (isObject(envelope.error)) {
        const e = envelope.error;
        return refused(text(e.code) ?? 'HOST_ERROR', text(e.message) ?? 'Kriya did not approve the configuration', text(e.reason));
      }
      const data = envelope.data;
      const outcome = isObject(data) ? text(data.outcome) : null;
      if (outcome !== 'approved' && outcome !== 'already_approved') return refused('INVALID_RESPONSE', 'Kriya did not report an approval outcome');
      return { approved: true, outcome, code: null, message: null, reason: null };
    } finally {
      this.inFlight = false;
    }
  }
}
