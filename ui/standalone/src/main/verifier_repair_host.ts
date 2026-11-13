/**
 * Verifier-defect repair authorization, host side (owner rulings 2026-11-13, Scenario-A run 9). Pure: no Electron import, no `child_process` import - the Kriya query, the open-workspace
 * check and the NATIVE confirmation are injected, so every rule is tested with controlled executors.
 *
 * What this module IS: a narrow proxy for ONE subject - the person's SEMANTIC decision that the exact verifier Kriya recorded for a pending review conflicts with the canonical contract,
 * which authorizes one bounded repair of that verifier. It takes ONE typed request {workspace, launch_id, review_id, authorization_fingerprint, decision}, checks it against its closed
 * grammar and against the folder currently open in this host, and - for an APPROVE - re-proves the pending subject through Kriya (`launch.status`) before anything else happens: the same
 * review and fingerprint, of THIS kind, still PENDING, and VERIFIED by Kriya (the recorded verifier source and contract text are the bytes the fingerprint binds). If Kriya cannot prove it,
 * there is NO confirmation and NOTHING is sent.
 *
 * The human commitment is HERE, in the main process: after the proof and before Kriya is asked, `confirm` shows a native confirmation whose text is Kriya's own question and the verifier
 * file path(s) as KRIYA recorded them - no renderer-supplied string.
 *   - Native CANCEL is "no decision yet": nothing is sent, the review stays PENDING in Kriya (it waits for its own expiry), and the renderer returns to the review.
 *   - The renderer's REJECT is the explicit "no": Kriya is told, repairs nothing, and the run continues on its existing routing.
 *
 * What it is NOT: an authority, a source approval or a grant. It never writes a decision, a record or a file. The waiting Kriya run re-proves the fingerprint against its live state
 * immediately before the repair (a changed binding makes the authorization stale), asks for the repaired verifier's source approval separately, and applies it only after its own gates.
 * There is no field for a file, a source text, a contract or an outcome, so the renderer cannot authorize anything Kriya did not itself propose.
 */
import { validateDecideVerifierRepair } from './ipc_contract';
import type { Prepared } from './execution_host';
import type { DecideApprovalResult } from './approval_host';
import { decisionAnswer, type ChannelAnswer, type ChannelDecisionRequest } from './review_channel';

export type DecideVerifierRepairResult = DecideApprovalResult;
export const VERIFIER_REPAIR_KIND = 'verifier_defect_repair_authorization' as const;

/** The native confirmation, as `dialog.showMessageBox` options. Cancel is first, the default and the Escape action; only `Authorize One Repair` (index 1) commits. */
export const CONFIRM_BUTTONS = ['Cancel', 'Authorize One Repair'] as const;
export const CONFIRM_AUTHORIZE_INDEX = 1;
export interface ConfirmationDialog { type: 'question'; title: string; message: string; detail: string; buttons: string[]; defaultId: number; cancelId: number; noLink: true }
export const DIALOG_FILES_MAX = 3;
const DIALOG_LINE_CHARS = 120;
export const CONFIRM_MESSAGE = 'Does this verifier expectation conflict with the canonical contract, authorizing one verifier repair?';

/** One recorded path for the native sheet: control and bidi characters removed, bounded. */
export function dialogLine(text: string): string {
  // eslint-disable-next-line no-control-regex -- control and bidi-override characters are exactly what is removed
  const line = text.replace(/[\u0000-\u001f\u007f​-‏‪-‮⁦-⁩]/g, ' ').replace(/\s+/g, ' ').trim();
  return line.length > DIALOG_LINE_CHARS ? `${line.slice(0, DIALOG_LINE_CHARS - 1)}…` : line;
}

/** The text of the native confirmation: the fixed question and consequences, then the verifier file path(s) exactly as KRIYA recorded and re-proved them (never anything the renderer sent). */
export function confirmationDialog(paths: readonly string[] = []): ConfirmationDialog {
  const shown = paths.slice(0, DIALOG_FILES_MAX).map((p, i) => `${i + 1}. ${dialogLine(p)}`);
  const more = paths.length > DIALOG_FILES_MAX ? [`…and ${paths.length - DIALOG_FILES_MAX} more`] : [];
  return {
    type: 'question', title: 'Authorize Verifier Repair', message: CONFIRM_MESSAGE,
    detail: [
      'Kriya never repairs a verifier on its own. Your answer authorizes ONE repair of exactly the verifier you reviewed, in this run.',
      'The repaired verifier still needs your separate source approval, must fail against the pre-repair code and must pass against the repaired code, or Kriya refuses it and stops the run for review.',
      ...(shown.length ? ['', ...shown, ...more] : []),
    ].join('\n'),
    buttons: [...CONFIRM_BUTTONS], defaultId: 0, cancelId: 0, noLink: true,
  };
}
/** True only for the explicit `Authorize One Repair` button. */
export const isAuthorizeResponse = (response: number): boolean => response === CONFIRM_AUTHORIZE_INDEX;

export interface VerifierRepairDeps {
  /** The run's private review channel (D2 M2): relays the decision with the capability the waiter drew for this review. */
  channel: { decide(request: ChannelDecisionRequest): Promise<ChannelAnswer> };
  now?(): Date;
  prepare(): Prepared | null;
  /** Kriya's `launch.status` envelope for the launch: its pending review as Kriya recorded it, with Kriya's own `verified` re-proof. */
  status(workspace: string, launchId: string): Promise<unknown>;
  /** The native confirmation (main process). Resolves true only when the person pressed `Authorize One Repair`. It is given the verifier paths Kriya's own envelope proved (never the renderer's). */
  confirm(paths: readonly string[]): Promise<boolean>;
  currentWorkspace(): string | null;
  log?(line: string): void;
}

const refused = (code: string, message: string): DecideVerifierRepairResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

function pendingOf(envelope: unknown): Record<string, unknown> | null {
  if (!isObject(envelope) || envelope.error !== null || !isObject(envelope.data) || !isObject(envelope.data.launch)) return null;
  const pending = envelope.data.launch.pending_approval;
  return isObject(pending) ? pending : null;
}

/** The verifier file paths of an envelope that `authorizationIsReviewable` accepted (empty otherwise). */
export function reviewedVerifierPaths(envelope: unknown): readonly string[] {
  const pending = pendingOf(envelope);
  const files = pending && Array.isArray(pending.verifier_files) ? pending.verifier_files : [];
  return files.filter(isObject).map((f) => String(f.path));
}

/** True only for an answer that proves THIS authorization review: no error, a pending review of this kind, the same review id and fingerprint, still PENDING, verified by Kriya, and a
 * non-empty well-formed verifier file list with source. */
export function authorizationIsReviewable(envelope: unknown, reviewId: string, fingerprint: string): boolean {
  const p = pendingOf(envelope);
  if (p === null) return false;
  return p.kind === VERIFIER_REPAIR_KIND && p.review_id === reviewId && p.candidate_digest === fingerprint && p.authorization_fingerprint === fingerprint
    && p.status === 'PENDING' && p.verified === true
    && Array.isArray(p.verifier_files) && p.verifier_files.length > 0
    && p.verifier_files.every((f) => isObject(f) && typeof f.path === 'string' && f.path.length > 0 && typeof f.sha256 === 'string' && typeof f.source === 'string' && f.source.length > 0)
    && typeof p.contract_text === 'string' && p.contract_text.length > 0;
}

export class VerifierRepairHost {
  private inFlight = false;

  constructor(private readonly deps: VerifierRepairDeps) {}

  async decide(raw: unknown): Promise<DecideVerifierRepairResult> {
    const request = validateDecideVerifierRepair(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    // One decision at a time: a double click is refused here, before any dialog exists; Kriya refuses a replay again on its own durable state.
    if (this.inFlight) return refused('DECISION_IN_FLIGHT', 'a decision is already being recorded');
    const prepared = this.deps.prepare();
    if (prepared === null) return refused('EXECUTION_UNAVAILABLE', 'this build has no Kriya execution backend (fixture mode)');
    if (!prepared.ok) return refused('HOST_ERROR', `kriya not launched: ${prepared.message}`);
    const { workspace } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; a decision can only be recorded for the open folder');
    this.inFlight = true; // before the confirmation is awaited: a second request while the native dialog is up is refused
    try {
      if (request.value.decision === 'approve') {
        let proved = false;
        let paths: readonly string[] = [];
        try {
          const envelope = await this.deps.status(workspace, request.value.launch_id);
          proved = authorizationIsReviewable(envelope, request.value.review_id, request.value.authorization_fingerprint);
          if (proved) paths = reviewedVerifierPaths(envelope);
        } catch (error) {
          this.deps.log?.(`verifier-repair launch.status: ${error instanceof Error ? error.message : String(error)}`);
        }
        if (!proved) return refused('AUTHORIZATION_NOT_REVIEWABLE', 'Kriya could not confirm the exact verifier and contract that are waiting for your decision, so nothing was sent. Review the request again, or reject it.');
        let confirmed: boolean;
        try {
          confirmed = await this.deps.confirm(paths);
        } catch (error) {
          this.deps.log?.(`verifier-repair confirmation: ${error instanceof Error ? error.message : String(error)}`);
          return refused('HOST_ERROR', 'The confirmation could not be shown; nothing was sent');
        }
        // The person pressed Cancel: NO decision. Kriya is not asked, the review stays pending, and the renderer returns to it.
        if (!confirmed) return refused('CONFIRMATION_DECLINED', 'Nothing was authorized. The request is still waiting for your decision.');
        // The sheet was up while other requests flowed: send only if the confirmed folder is still the open one.
        if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'the open folder changed while the confirmation was shown; nothing was sent');
      }
      const relay: ChannelDecisionRequest = { launch_id: request.value.launch_id, review_id: request.value.review_id, kind: VERIFIER_REPAIR_KIND, candidate_digest: request.value.authorization_fingerprint, decision: request.value.decision };
      const answer = await this.deps.channel.decide(relay);
      if (!answer.accepted) this.deps.log?.(`verifier-repair decision not accepted: ${answer.code}`);
      return decisionAnswer(relay, answer, this.deps.now?.() ?? new Date());
    } finally {
      this.inFlight = false;
    }
  }
}
