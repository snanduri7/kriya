/**
 * Review decision, host side (GUI final). Pure: no Electron import, no `child_process` import - the one-shot Kriya runner, the executable and environment
 * policy and the open-workspace check are injected, so the rules are tested with controlled executors.
 *
 * What this module IS: a narrow proxy. It takes ONE typed request {workspace, launch_id, review_id, candidate_digest, decision}, checks it against its closed
 * grammar and against the folder currently open in this host, runs Kriya's `launch-decision` action in that folder (no shell, the host's fixed child
 * environment) and reports Kriya's own answer.
 *
 * What it is NOT: an authority. It never writes a decision, a record or a file; it cannot approve "whatever is current" (the identities come from Kriya's pending
 * review and are re-checked by Kriya); it has no auto-approval, no `-y`, no autonomy option. Kriya validates the review is still the pending one for this
 * launch and workspace, records the decision durably, and the waiting run - not Electron - resumes from it.
 */
import { buildLaunchDecisionArgv } from './kriya_argv';
import { validateDecideApproval } from './ipc_contract';
import type { Prepared } from './execution_host';

export interface DecideApprovalResult { accepted: boolean; code: string; message: string; launch_id: string | null; review_id: string | null; decision: string | null; decided_at: string | null }

export interface ApprovalRun { kind: 'json' | 'error'; json?: unknown; message?: string; exitCode: number | null; stderrTail: string }
export interface ApprovalDeps {
  /** One-shot Kriya runner (kriya_process.ts runKriya in production). */
  run(options: { executable: string; argv: readonly string[]; cwd: string; env: Record<string, string>; limits: { timeoutMs: number } }): Promise<ApprovalRun>;
  prepare(): Prepared | null;
  currentWorkspace(): string | null;
  log?(line: string): void;
}

const TIMEOUT_MS = 30_000;
const refused = (code: string, message: string): DecideApprovalResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null);

export class ApprovalHost {
  private inFlight = false;

  constructor(private readonly deps: ApprovalDeps) {}

  async decide(raw: unknown): Promise<DecideApprovalResult> {
    const request = validateDecideApproval(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    // One decision at a time: a double click is refused here, before any process exists; Kriya refuses a replay again on its own durable state.
    if (this.inFlight) return refused('DECISION_IN_FLIGHT', 'a decision is already being recorded');
    const prepared = this.deps.prepare();
    if (prepared === null) return refused('EXECUTION_UNAVAILABLE', 'this build has no Kriya execution backend (fixture mode)');
    if (!prepared.ok) return refused('HOST_ERROR', `kriya not launched: ${prepared.message}`);
    const { workspace } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; a decision can only be recorded for the open folder');
    this.inFlight = true;
    try {
      const argv = buildLaunchDecisionArgv(request.value);
      const outcome = await this.deps.run({ executable: prepared.executable, argv, cwd: workspace, env: prepared.env, limits: { timeoutMs: TIMEOUT_MS } });
      if (outcome.kind !== 'json' || typeof outcome.json !== 'object' || outcome.json === null) {
        this.deps.log?.(`launch-decision: ${outcome.message ?? 'no JSON'} (exit ${outcome.exitCode ?? 'null'}) ${outcome.stderrTail.slice(-300)}`);
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
