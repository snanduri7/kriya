/**
 * Verifier semantic review, host side (VERIFIER-SEMANTIC-REVIEW-001, owner ruling 2026-11-14). Pure: no Electron import, no `child_process` import - the Kriya query, the open-workspace
 * check and the NATIVE confirmation are injected, so every rule is tested with controlled executors.
 *
 * What this module IS: a narrow proxy for ONE subject - the person's SEMANTIC decision whether the expectation of the exact INITIAL generated verifier Kriya recorded for a pending review
 * is correct under the canonical contract. It is a FOURTH subject, beside (never inside) the replay-specific verifier-repair authorization, and it has its own three decision words:
 * `expectation_valid`, `expectation_invalid`, `cannot_decide`. `approve` / `reject` do not exist here (on the repair card "approve" means the verifier is WRONG: the opposite polarity).
 *
 * It takes ONE typed request {workspace, launch_id, review_id, semantic_fingerprint, decision}, checks it against its closed grammar and the folder currently open, and - for the two
 * words that carry authority (`expectation_valid`, `expectation_invalid`) - re-proves the pending subject through Kriya (`launch.status`): the same review and fingerprint, of THIS kind,
 * still PENDING, VERIFIED by Kriya. Then the human commitment happens HERE, in the main process: a native confirmation that names the chosen decision in words and the verifier path(s) as
 * KRIYA recorded them. Native CANCEL is "no decision yet": nothing is sent and the review stays pending. `cannot_decide` carries no authority (nothing is changed; the run goes to
 * review) and is relayed without the confirmation, like a rejection on the other cards.
 *
 * What it is NOT: an authority, a source approval or a grant. It never writes a decision, a record or a file. The waiting Kriya run re-proves the fingerprint against its live state
 * before acting. There is no field for a file, a source text, a contract, a criterion or an outcome.
 */
import { validateDecideVerifierSemantic } from './ipc_contract';
import type { Prepared } from './execution_host';
import type { DecideApprovalResult } from './approval_host';
import { decisionAnswer, type ChannelAnswer, type ChannelDecisionRequest, type VerifierSemanticDecisionWord } from './review_channel';
import { dialogLine, DIALOG_FILES_MAX } from './verifier_repair_host';

export type DecideVerifierSemanticResult = DecideApprovalResult;
export const VERIFIER_SEMANTIC_KIND = 'verifier_semantic_review' as const;
/** The words that give the expectation (or its regeneration) authority: these, and only these, need the native confirmation. */
export const AUTHORITY_DECISIONS: readonly VerifierSemanticDecisionWord[] = ['expectation_valid', 'expectation_invalid'];

export const SEMANTIC_CONFIRM_MESSAGE = 'Is what this verifier expects correct under the canonical contract?';
/** The commit button of each authority decision. Polarity is in the words: never a generic Approve / Reject. */
export const SEMANTIC_CONFIRM_BUTTON: Readonly<Record<'expectation_valid' | 'expectation_invalid', string>> = {
  expectation_valid: 'Record: Expectation Is Valid', expectation_invalid: 'Record: Expectation Is Invalid',
};
const SEMANTIC_CONFIRM_DETAIL: Readonly<Record<'expectation_valid' | 'expectation_invalid', string>> = {
  expectation_valid: 'You are recording that the verifier EXPECTS THE RIGHT THING. This accepts its expectation for exactly these verifier bytes in this run. It does not say which code is wrong and authorizes no change.',
  expectation_invalid: 'You are recording that the verifier EXPECTS THE WRONG THING. This permits ONE regeneration of exactly this verifier in this run; the new verifier is reviewed and gated as source. There is no second regeneration.',
};
export const SEMANTIC_CONFIRM_COMMIT_INDEX = 1;
export interface SemanticConfirmationDialog { type: 'question'; title: string; message: string; detail: string; buttons: string[]; defaultId: number; cancelId: number; noLink: true }

/** The native confirmation for one authority decision: the fixed question, what THIS decision means, then the verifier path(s) exactly as KRIYA recorded and re-proved them. Cancel is
 * first, the default and the Escape action; only the decision-named button (index 1) commits. */
export function semanticConfirmationDialog(decision: 'expectation_valid' | 'expectation_invalid', paths: readonly string[] = []): SemanticConfirmationDialog {
  const shown = paths.slice(0, DIALOG_FILES_MAX).map((p, i) => `${i + 1}. ${dialogLine(p)}`);
  const more = paths.length > DIALOG_FILES_MAX ? [`…and ${paths.length - DIALOG_FILES_MAX} more`] : [];
  return {
    type: 'question', title: 'Verifier Semantic Review', message: SEMANTIC_CONFIRM_MESSAGE,
    detail: [SEMANTIC_CONFIRM_DETAIL[decision], ...(shown.length ? ['', ...shown, ...more] : [])].join('\n'),
    buttons: ['Cancel', SEMANTIC_CONFIRM_BUTTON[decision]], defaultId: 0, cancelId: 0, noLink: true,
  };
}
/** True only for the explicit decision-named button. */
export const isSemanticCommitResponse = (response: number): boolean => response === SEMANTIC_CONFIRM_COMMIT_INDEX;

export interface VerifierSemanticDeps {
  /** The run's private review channel (D2 M2): relays the decision with the capability the waiter drew for this review. */
  channel: { decide(request: ChannelDecisionRequest): Promise<ChannelAnswer> };
  now?(): Date;
  prepare(): Prepared | null;
  /** Kriya's `launch.status` envelope for the launch: its pending review as Kriya recorded it, with Kriya's own `verified` re-proof. */
  status(workspace: string, launchId: string): Promise<unknown>;
  /** The native confirmation (main process). Resolves true only when the person pressed the decision-named button. Given the decision and the verifier paths Kriya's own envelope proved. */
  confirm(decision: 'expectation_valid' | 'expectation_invalid', paths: readonly string[]): Promise<boolean>;
  currentWorkspace(): string | null;
  log?(line: string): void;
}

const refused = (code: string, message: string): DecideVerifierSemanticResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

function pendingOf(envelope: unknown): Record<string, unknown> | null {
  if (!isObject(envelope) || envelope.error !== null || !isObject(envelope.data) || !isObject(envelope.data.launch)) return null;
  const pending = envelope.data.launch.pending_approval;
  return isObject(pending) ? pending : null;
}

/** The verifier file paths of an envelope that `semanticReviewIsReviewable` accepted (empty otherwise). */
export function semanticVerifierPaths(envelope: unknown): readonly string[] {
  const pending = pendingOf(envelope);
  const files = pending && Array.isArray(pending.verifier_files) ? pending.verifier_files : [];
  return files.filter(isObject).map((f) => String(f.path));
}

/** True only for an answer that proves THIS semantic review: no error, a pending review of this kind, the same review id and fingerprint, still PENDING, verified by Kriya, and a
 * non-empty well-formed verifier file list with source and the contract text. */
export function semanticReviewIsReviewable(envelope: unknown, reviewId: string, fingerprint: string): boolean {
  const p = pendingOf(envelope);
  if (p === null) return false;
  return p.kind === VERIFIER_SEMANTIC_KIND && p.review_id === reviewId && p.candidate_digest === fingerprint && p.semantic_fingerprint === fingerprint
    && p.status === 'PENDING' && p.verified === true
    && Array.isArray(p.verifier_files) && p.verifier_files.length > 0
    && p.verifier_files.every((f) => isObject(f) && typeof f.path === 'string' && f.path.length > 0 && typeof f.sha256 === 'string' && typeof f.source === 'string' && f.source.length > 0)
    && typeof p.contract_text === 'string' && p.contract_text.length > 0;
}

export class VerifierSemanticHost {
  private inFlight = false;

  constructor(private readonly deps: VerifierSemanticDeps) {}

  async decide(raw: unknown): Promise<DecideVerifierSemanticResult> {
    const request = validateDecideVerifierSemantic(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    if (this.inFlight) return refused('DECISION_IN_FLIGHT', 'a decision is already being recorded');
    const prepared = this.deps.prepare();
    if (prepared === null) return refused('EXECUTION_UNAVAILABLE', 'this build has no Kriya execution backend (fixture mode)');
    if (!prepared.ok) return refused('HOST_ERROR', `kriya not launched: ${prepared.message}`);
    const { workspace, decision } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; a decision can only be recorded for the open folder');
    this.inFlight = true; // before the confirmation is awaited: a second request while the native dialog is up is refused
    try {
      if (decision === 'expectation_valid' || decision === 'expectation_invalid') {
        let proved = false;
        let paths: readonly string[] = [];
        try {
          const envelope = await this.deps.status(workspace, request.value.launch_id);
          proved = semanticReviewIsReviewable(envelope, request.value.review_id, request.value.semantic_fingerprint);
          if (proved) paths = semanticVerifierPaths(envelope);
        } catch (error) {
          this.deps.log?.(`verifier-semantic launch.status: ${error instanceof Error ? error.message : String(error)}`);
        }
        if (!proved) return refused('SEMANTIC_REVIEW_NOT_REVIEWABLE', 'Kriya could not confirm the exact verifier and contract that are waiting for your decision, so nothing was sent. Review the request again, or choose Cannot decide.');
        let confirmed: boolean;
        try {
          confirmed = await this.deps.confirm(decision, paths);
        } catch (error) {
          this.deps.log?.(`verifier-semantic confirmation: ${error instanceof Error ? error.message : String(error)}`);
          return refused('HOST_ERROR', 'The confirmation could not be shown; nothing was sent');
        }
        // The person pressed Cancel: NO decision (never `cannot_decide`). Kriya is not asked, the review stays pending.
        if (!confirmed) return refused('CONFIRMATION_DECLINED', 'Nothing was recorded. The request is still waiting for your decision.');
        if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'the open folder changed while the confirmation was shown; nothing was sent');
      }
      const relay: ChannelDecisionRequest = { launch_id: request.value.launch_id, review_id: request.value.review_id, kind: VERIFIER_SEMANTIC_KIND, candidate_digest: request.value.semantic_fingerprint, decision };
      const answer = await this.deps.channel.decide(relay);
      if (!answer.accepted) this.deps.log?.(`verifier-semantic decision not accepted: ${answer.code}`);
      return decisionAnswer(relay, answer, this.deps.now?.() ?? new Date());
    } finally {
      this.inFlight = false;
    }
  }
}
