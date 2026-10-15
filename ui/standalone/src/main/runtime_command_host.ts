/**
 * Runtime-command decision, host side (Demo V2). Pure: no Electron import, no `child_process` import - the one-shot Kriya runner, the executable and environment policy, the
 * open-workspace check and the NATIVE confirmation are injected, so every rule is tested with controlled executors.
 *
 * What this module IS: a narrow proxy for ONE subject - allowing Kriya to run the exact verification commands it recorded for a pending runtime-command review. It takes ONE typed
 * request {workspace, launch_id, review_id, command_fingerprint, decision}, checks it against its closed grammar and against the folder currently open in this host, and
 * - for an APPROVE - re-proves the pending subject through Kriya (`runtime_command.review`) before anything else happens: still verified, the same review and fingerprint, still PENDING,
 * a non-empty recorded command set. If Kriya cannot prove it, there is NO confirmation and NOTHING is sent (a stale or altered subject is never put in front of the person).
 *
 * The human commitment is HERE, in the main process: after the proof and before Kriya is asked, `confirm` shows a native confirmation whose text is FIXED - it carries no command, no
 * directory, no fingerprint, no environment value and no renderer-supplied string. The renderer review shows the commands; this confirmation is the commitment.
 *   - Native CANCEL is "no decision yet": nothing is sent, the review stays PENDING in Kriya (it waits for its own expiry), and the renderer returns to the review.
 *   - The renderer's REJECT is the explicit refusal: no confirmation, Kriya's decision action with reject.
 *
 * What it is NOT: an authority. It never writes a decision, a record or a file, and it never runs a command - the waiting Kriya run does, in its own executor, after Kriya records the
 * decision and re-proves the fingerprint (STALE_REVIEW / WRONG_REVIEW_KIND are Kriya's answers, passed through). There is no field for a command, argument, directory, environment
 * value or stdin, so the renderer cannot approve anything Kriya did not itself propose.
 */
import { buildRuntimeCommandDecisionArgv } from './kriya_argv';
import { validateDecideRuntimeCommand } from './ipc_contract';
import type { Prepared } from './execution_host';
import type { ApprovalRun, DecideApprovalResult } from './approval_host';

export type DecideRuntimeCommandResult = DecideApprovalResult;

/** The native confirmation, as `dialog.showMessageBox` options. Cancel is first, the default and the Escape action; only `Run Commands` (index 1) commits. */
export const CONFIRM_BUTTONS = ['Cancel', 'Run Commands'] as const;
export const CONFIRM_RUN_INDEX = 1;
export interface ConfirmationDialog { type: 'question'; title: string; message: string; detail: string; buttons: string[]; defaultId: number; cancelId: number; noLink: true }
/** The whole text of the native confirmation. It is a constant: nothing the renderer or the review record holds can enter it. */
export function confirmationDialog(): ConfirmationDialog {
  return {
    type: 'question', title: 'Run Verification Commands', message: 'Allow Kriya to run the reviewed verification commands?',
    detail: 'Kriya will run the commands you reviewed in its isolated worktree to verify the application.',
    buttons: [...CONFIRM_BUTTONS], defaultId: 0, cancelId: 0, noLink: true,
  };
}
/** True only for the explicit `Run Commands` button. */
export const isRunResponse = (response: number): boolean => response === CONFIRM_RUN_INDEX;

export interface RuntimeCommandDeps {
  run(options: { executable: string; argv: readonly string[]; cwd: string; env: Record<string, string>; limits: { timeoutMs: number } }): Promise<ApprovalRun>;
  prepare(): Prepared | null;
  /** Kriya's `runtime_command.review` envelope for one review (the exact recorded command set, re-proved against its fingerprint). */
  reviewable(workspace: string, launchId: string, reviewId: string): Promise<unknown>;
  /** The native confirmation (main process). Resolves true only when the person pressed `Run Commands`. It is given no argument: its text is fixed. */
  confirm(): Promise<boolean>;
  currentWorkspace(): string | null;
  log?(line: string): void;
}

const TIMEOUT_MS = 30_000;
const refused = (code: string, message: string): DecideRuntimeCommandResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null);
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

/** True only for an answer that proves THIS review's exact command set: no error, verified, same review id and fingerprint, still PENDING, a non-empty well-formed command list. */
export function commandsAreReviewable(envelope: unknown, reviewId: string, fingerprint: string): boolean {
  if (!isObject(envelope) || envelope.error !== null || !isObject(envelope.data)) return false;
  const d = envelope.data;
  return d.verified === true && d.review_id === reviewId && d.command_fingerprint === fingerprint && d.status === 'PENDING'
    && Array.isArray(d.commands) && d.commands.length > 0
    && d.commands.every((c) => Array.isArray(c) && c.length > 0 && c.every((a) => typeof a === 'string' && a.length > 0))
    && typeof d.working_directory === 'string' && d.working_directory.length > 0;
}

export class RuntimeCommandHost {
  private inFlight = false;

  constructor(private readonly deps: RuntimeCommandDeps) {}

  async decide(raw: unknown): Promise<DecideRuntimeCommandResult> {
    const request = validateDecideRuntimeCommand(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    // One decision at a time: a double click is refused here, before any dialog or process exists; Kriya refuses a replay again on its own durable state.
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
        try {
          proved = commandsAreReviewable(await this.deps.reviewable(workspace, request.value.launch_id, request.value.review_id), request.value.review_id, request.value.command_fingerprint);
        } catch (error) {
          this.deps.log?.(`runtime_command.review: ${error instanceof Error ? error.message : String(error)}`);
        }
        if (!proved) return refused('COMMAND_NOT_REVIEWABLE', 'Kriya could not confirm the exact commands that are waiting for your decision, so nothing was sent. Review the request again, or reject it.');
        let confirmed: boolean;
        try {
          confirmed = await this.deps.confirm();
        } catch (error) {
          this.deps.log?.(`runtime-command confirmation: ${error instanceof Error ? error.message : String(error)}`);
          return refused('HOST_ERROR', 'The confirmation could not be shown; nothing was sent');
        }
        // The person pressed Cancel: NO decision. Kriya is not asked, the review stays pending, and the renderer returns to it.
        if (!confirmed) return refused('CONFIRMATION_DECLINED', 'Nothing was run. The request is still waiting for your decision.');
        // The sheet was up while other requests flowed: send only if the confirmed folder is still the open one.
        if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'the open folder changed while the confirmation was shown; nothing was sent');
      }
      const argv = buildRuntimeCommandDecisionArgv(request.value);
      const outcome = await this.deps.run({ executable: prepared.executable, argv, cwd: workspace, env: prepared.env, limits: { timeoutMs: TIMEOUT_MS } });
      if (outcome.kind !== 'json' || typeof outcome.json !== 'object' || outcome.json === null) {
        this.deps.log?.(`runtime-command decision: ${outcome.message ?? 'no JSON'} (exit ${outcome.exitCode ?? 'null'}) ${outcome.stderrTail.slice(-300)}`);
        return refused('HOST_ERROR', `Kriya did not answer the decision (${outcome.message ?? 'no JSON answer'}); nothing is known to be recorded - check the run before deciding again`);
      }
      const answer = outcome.json as Record<string, unknown>;
      const accepted = answer.accepted === true && outcome.exitCode === 0;
      return {
        accepted, code: text(answer.code) ?? (accepted ? 'ACCEPTED' : 'HOST_ERROR'), message: text(answer.message) ?? (accepted ? 'recorded' : 'Kriya refused the decision'),
        launch_id: text(answer.launch_id), review_id: text(answer.review_id), decision: text(answer.decision), decided_at: text(answer.decided_at),
      };
    } finally {
      this.inFlight = false;
    }
  }
}
