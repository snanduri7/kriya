/**
 * A scripted, step-driven stand-in for Kriya's execution side (GUI M6): DEV/TEST ONLY, imported only by fixtureHost.ts and tests.
 *
 * It does not run anything. It answers the same questions Kriya answers about a launch (`execution.preflight`, `launch.status`, the live
 * `evidence.records` of the run) from a script whose frames the TEST advances explicitly (`advance()`), so every UI state - launching, running, each
 * terminal outcome, a process that dies, a launch Kriya never recorded - is reachable deterministically, without timers and without a model.
 * The host-process side (`ExecutionHostPort`) reports only the process, exactly like the real host.
 */
import type { ExecutionHostPort, NotifyRequest, NotifyResult } from '../../../host/HostAdapter';
import type { DecideApprovalRequest, DecideApprovalResult, ExecutionState, StartRunResult } from '../../../model/kup';

export type FixtureOutcome = 'success' | 'failure' | 'died' | 'refused' | 'never_recorded' | 'approval_required' | 'stuck_starting' | 'success_after_stale_read' | 'success_with_declined' | 'review';

export interface FixtureExecutionOptions {
  /** How the next launch ends (default 'success'). */
  outcome?: FixtureOutcome;
  /** Folders whose preflight carries a blocker (code -> message), besides the built-in /fixture/blocked. */
  blockers?: Readonly<Record<string, { code: string; message: string }>>;
  /** The host refuses the start with this typed refusal (EXECUTION_BUSY is produced by the real host; here for UI tests). */
  refuseStart?: { code: string; message: string };
}

export const LAUNCH_ID = '20261010T101010-abcdef01';
export const LAUNCHED_TRACE_ID = 'fx-launched';
export const REVIEW_ID = 'ra-' + 'a1'.repeat(12);
export const REVIEW_DIGEST = 'sha256:' + 'b2'.repeat(32);
export const REVIEW_REASON = 'Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe change implements the goal and keeps the public signature.';
export type ReviewStatus = 'PENDING' | 'APPROVED' | 'REJECTED' | 'EXPIRED';
const REVIEW_FRAME = 3;
const RECORD_KINDS = ['run.opened', 'unit.opened', 'attempt.opened', 'model.request', 'model.response', 'candidate.change', 'gate.result', 'gate.result', 'attempt.closed', 'run.closed'];

/** Evidence lines recorded at each frame (index 0 = before any record). */
const LINES_AT = [0, 0, 3, 7, RECORD_KINDS.length];

type Json = Record<string, unknown>;

export class FixtureExecution {
  /** Every Start Run request the host received (for assertions). */
  readonly starts: Array<{ workspace: string; goal: string }> = [];
  private frame = -1; // -1 = no launch
  private launchGoal = '';
  private launchWorkspace = '';
  private outcome: FixtureOutcome;
  private staleServed = false;
  private reviewStatus: ReviewStatus = 'PENDING';
  private reviewSuperseded = false;
  private reviewSequence = 1;
  private killed = false;
  private statusFault: string | null = null;
  /** Every notification the renderer asked the host for (for assertions). */
  readonly notifications: NotifyRequest[] = [];
  /** Every decision request the host received (for assertions). */
  readonly decisions: DecideApprovalRequest[] = [];
  readonly port: ExecutionHostPort;

  constructor(private readonly options: FixtureExecutionOptions = {}) {
    this.outcome = options.outcome ?? 'success';
    this.port = {
      startRun: async (request): Promise<StartRunResult> => {
        this.starts.push({ workspace: request.workspace, goal: request.goal });
        if (this.options.refuseStart) return { started: false, launch_id: null, refusal: this.options.refuseStart };
        if (this.active()) return { started: false, launch_id: null, refusal: { code: 'EXECUTION_BUSY', message: 'a run is already in progress; only one run at a time can be started from this window' } };
        this.frame = 0;
        this.reviewStatus = 'PENDING';
        this.reviewSuperseded = false;
        this.killed = false;
        this.launchGoal = request.goal;
        this.launchWorkspace = request.workspace;
        return { started: true, launch_id: LAUNCH_ID, refusal: null };
      },
      state: async (): Promise<ExecutionState> => this.hostState(),
      decideApproval: async (request): Promise<DecideApprovalResult> => this.decide(request),
      notify: async (request): Promise<NotifyResult> => { this.notifications.push(request); return { shown: true, reason: 'shown' }; },
    };
  }

  setOutcome(outcome: FixtureOutcome): void { this.outcome = outcome; }
  launched(): boolean { return this.frame >= 0; }
  workspace(): string { return this.launchWorkspace; }
  goal(): string { return this.launchGoal; }

  /** Test hook: the run moves to its next frame. */
  advance(): void { if (this.frame >= 0 && this.frame < 4 && !this.reviewPending()) this.frame += 1; }
  /** Test hook: jump to the final frame (a run waiting on an undecided review cannot be finished: it is waiting). */
  finish(): void { if (this.frame >= 0 && !this.reviewPending()) this.frame = 4; }
  /** The run is waiting on the human's decision (a pending review exists and nobody has decided it). */
  reviewPending(): boolean { return this.outcome === 'review' && this.frame === REVIEW_FRAME && this.reviewStatus === 'PENDING'; }
  /** Test hook: the execution process is killed (SIGKILL) where it stands - the launch record stays RUNNING, the workspace lock is released, nothing is sealed. */
  killProcess(): void { this.killed = true; }
  /** Test hook: Kriya's launch.status answers a typed error (other than not-found) from now on. */
  breakStatus(code: string | null): void { this.statusFault = code; }
  statusBroken(): string | null { return this.statusFault; }
  /** Test hook: a retry produced a new candidate, so the review the person was shown is superseded (decisions for it are stale). */
  supersedeReview(): void { this.reviewSuperseded = true; }
  /** Kriya raises a NEW review for the same launch (another candidate): new review id and digest, the previous identities now stale. */
  replaceReview(): void { this.reviewSequence += 1; this.reviewSuperseded = false; }
  private reviewId(): string { return this.reviewSequence === 1 ? REVIEW_ID : 'ra-' + String(this.reviewSequence).padStart(2, '0').repeat(12); }
  private reviewDigest(): string { return this.reviewSequence === 1 ? REVIEW_DIGEST : 'sha256:' + String(this.reviewSequence).padStart(2, '0').repeat(32); }

  /** The process is alive (the real host's `active()`): not yet at the final frame. */
  active(): boolean { return this.frame >= 0 && this.frame < 4 && !this.killed; }
  /** The run's final records are durable (a finished, sealed run the history now lists). */
  private succeeds(): boolean { return (this.outcome === 'review' && this.reviewStatus === 'APPROVED') || this.outcome === 'success' || this.outcome === 'success_after_stale_read' || this.outcome === 'success_with_declined'; }
  ended(): boolean { return this.frame === 4 && (this.succeeds() || this.outcome === 'failure' || (this.outcome === 'review' && this.reviewStatus === 'REJECTED')); }

  private hostState(): ExecutionState {
    if (this.frame < 0) return { phase: 'idle', launch_id: null, workspace: null, started_at: null, ended_at: null, exit: null, diagnostic: null };
    const over = this.frame === 4 || this.killed;
    const code = this.killed ? null : this.succeeds() ? 0 : this.outcome === 'refused' ? 2 : this.outcome === 'died' ? null : 1;
    return {
      phase: over ? 'exited' : 'running', launch_id: LAUNCH_ID, workspace: this.launchWorkspace, started_at: '2026-10-10T10:10:10.000Z', ended_at: over ? '2026-10-10T10:12:00.000Z' : null,
      exit: over ? { code, signal: this.outcome === 'died' || this.killed ? 'SIGKILL' : null } : null, diagnostic: null,
    };
  }

  /** `execution.preflight` for a folder: ready unless the folder is scripted with a blocker. */
  preflight(workspace: string, stateDirectory: string, blocked: { code: string; message: string } | null): Json {
    const blockers = blocked ? [blocked] : [];
    return {
      ready: blockers.length === 0, blockers,
      workspace: { path: workspace, identity: 'ab'.repeat(32), name: workspace.slice(workspace.lastIndexOf('/') + 1) },
      configuration: { status: 'loaded', source: 'packaged_default', file: null, directory: workspace, discovery: 'Configuration is discovered from the process working directory, which is this folder.', fingerprint: 'cd'.repeat(32), authority: { status: 'no_findings', fields: [] } },
      state: { directory: stateDirectory, source: 'env', cockpit_directory: stateDirectory, observable_by_cockpit: true },
      model: { provider: 'openai', model: 'fixture-model:7b', endpoint: 'http://localhost:11434/v1', context_window: 32768, fallback_chain: ['fixture-fallback:3b'], role_overrides: {} },
      recovery: { status: 'CLEAN', run_active: false },
      interaction: { approvals: 'change_review', autonomy_mode: 'human-in-the-loop', knowledge_policy: 'strict', note: 'A change Kriya proposes is held for your review in this window and applied only if you approve it. Any other step that needs a human decision (a knowledge-gap acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.' },
      toolchains: { tools: [{ tool: 'python3', available: true, path: '/usr/bin/python3' }, { tool: 'git', available: true, path: '/usr/bin/git' }, { tool: 'java', available: false, path: null }, { tool: 'mvn', available: false, path: null }], note: 'Visibility under this process\'s own environment (a fixed PATH; no login shell). Which tools a goal needs is only known while the workflow runs.' },
    };
  }

  /** `launch.status` data for the scripted launch, or null when Kriya has not recorded it (the answer is LAUNCH_NOT_FOUND). */
  launchStatus(): Json | null {
    const f = this.frame;
    if (f < 0 || this.outcome === 'never_recorded') return null;
    if (f === 0) return null; // the process exists; Kriya has not written the launch record yet
    if (this.outcome === 'stuck_starting') return this.stuck();
    if (this.outcome === 'success_after_stale_read' && f === 4 && !this.staleServed) { this.staleServed = true; return this.stale(); }
    return this.statusFor(this.outcome);
  }

  private pendingReview(): Json | null {
    if (this.outcome !== 'review' || this.frame < REVIEW_FRAME) return null;
    const decided = this.reviewStatus !== 'PENDING';
    return {
      review_id: this.reviewId(), sequence: this.reviewSequence, kind: 'change_approval', status: this.reviewStatus, requested_at: '2026-10-10T10:11:00+00:00', expires_at: '2026-10-10T11:11:00+00:00',
      candidate_digest: this.reviewDigest(), files: [{ filepath: 'src/clamp.py', diff_sha256: 'sha256:' + 'c3'.repeat(32), lines_added: 3, lines_removed: 0 }],
      reason: REVIEW_REASON, reason_truncated: false, reason_sha256: 'sha256:' + 'd4'.repeat(32),
      decision: decided ? { decision: this.reviewStatus === 'APPROVED' ? 'approve' : 'reject', decided_at: '2026-10-10T10:11:30+00:00', source: 'launch-decision' } : null,
    };
  }

  private statusFor(outcome: FixtureOutcome): Json | null {
    const f = this.frame;
    const lines = LINES_AT[f] ?? 0;
    const approved = outcome === 'review' && this.reviewStatus === 'APPROVED';
    const rejected = outcome === 'review' && this.reviewStatus === 'REJECTED';
    const ok = outcome === 'success' || outcome === 'success_after_stale_read' || outcome === 'success_with_declined' || approved;
    const sealed = f === 4 && (ok || outcome === 'failure' || rejected);
    const refused = outcome === 'refused' && f === 4;
    const approval = outcome === 'approval_required' && f === 4;
    const died = (outcome === 'died' && f === 4) || this.killed;
    const phase = refused || sealed || approval ? 'ENDED' : f === 1 ? 'STARTING' : 'RUNNING';
    const terminal = sealed ? (ok ? 'SUCCESS' : 'FAILURE') : approval ? 'FAILURE' : null;
    const run = f >= 2 && !refused ? { run_id: LAUNCH_ID, started_at: '2026-10-10T10:10:12+00:00', base_revision: null } : null;
    const ended = phase === 'ENDED' ? {
      at: '2026-10-10T10:12:00+00:00', exit_code: refused ? 2 : ok ? 0 : 1, workflow_status: approval || rejected ? null : refused ? 'refused' : ok ? 'success' : 'failure',
      quality_gates_passed: refused || rejected ? false : ok, failure_category: outcome === 'failure' ? 'quality_gate_failed' : null, trace_run_id: refused || approval ? null : LAUNCHED_TRACE_ID,
      error: refused ? 'another Kriya run already owns this folder' : approval ? 'Aborted!' : outcome === 'failure' ? 'Failed to create an isolated generation sandbox (fixture)' : null, note: 'copied from the workflow\'s own result; the RunRecord, evidence store and trace row are the outcome authority',
    } : null;
    const pending = this.pendingReview();
    return {
      launch: {
        launch_id: LAUNCH_ID, workspace: { path: this.launchWorkspace, identity: 'ab'.repeat(32) }, requested_at: '2026-10-10T10:10:10+00:00', goal: { sha256: 'ef'.repeat(32), chars: this.launchGoal.length },
        options: { knowledge_policy: 'strict', interactive: false }, phase, run, ended,
        history: [{ phase: 'STARTING', at: '2026-10-10T10:10:10+00:00' }, ...(run ? [{ phase: 'RUNNING', at: '2026-10-10T10:10:12+00:00' }] : []), ...(ended ? [{ phase: 'ENDED', at: '2026-10-10T10:12:00+00:00' }] : [])],
        process: { pid: 4242 },
        approval_required: outcome === 'success_with_declined' ? { kind: 'skill_gap_decision', reason: 'a stale note from before the fix', details: {}, noted_at: '2026-10-10T10:11:00+00:00' } : approval ? { kind: 'change_approval', reason: 'the proposed change touches a sensitive path', details: { files: ['.github/workflows/ci.yml'] }, noted_at: '2026-10-10T10:11:59+00:00' } : null,
        declined_decisions: outcome === 'success_with_declined' ? [{ kind: 'skill_gap_decision', reason: 'no verified skill for lib-x', details: { skills: ['lib-x'] }, noted_at: '2026-10-10T10:11:00+00:00' }] : [],
        pending_approval: pending, approval_history: [],
      },
      run_record: run ? { found: true, unreadable: null, lifecycle_state: terminal ?? 'RUNNING', terminal_status: terminal, commit_result: terminal === 'SUCCESS' ? 'COMMITTED' : null, revision: 2 } : { found: false, unreadable: null },
      evidence: run ? { state: sealed ? 'sealed' : 'open', line_count: lines, sealed } : { state: 'absent', line_count: null, sealed: false },
      workspace_lock: { held: f >= 2 && !sealed && !died && !refused, held_by_this_launch: f >= 2 && !sealed && !died && !refused },
      liveness: phase === 'ENDED' ? 'ended' : died ? 'interrupted' : f === 1 ? 'starting' : 'live',
      liveness_note: 'DERIVED from the launch phase and the workspace run lock; a process exit alone is never the outcome',
    };
  }

  /** Kriya's `launch-decision`, scripted: the same closed refusal codes, checked in the same order, against the same identities. */
  private decide(request: DecideApprovalRequest): DecideApprovalResult {
    this.decisions.push(request);
    const no = (code: string, message: string): DecideApprovalResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
    if (this.frame < 0 || request.launch_id !== LAUNCH_ID) return no('LAUNCH_NOT_FOUND', `no launch ${request.launch_id} is recorded for this folder`);
    if (request.workspace !== this.launchWorkspace) return no('WRONG_WORKSPACE', 'this launch belongs to a different workspace');
    if (this.killed) return no('LAUNCH_NOT_ACTIVE', 'the run that was waiting for this decision is no longer running');
    if (this.frame === 4) return no('LAUNCH_ENDED', 'the run has already ended; there is nothing left to approve');
    if (this.outcome !== 'review' || this.frame < REVIEW_FRAME) return no('NOT_PENDING', 'no change is waiting for review in this launch');
    if (this.reviewSuperseded || request.review_id !== this.reviewId() || request.candidate_digest !== this.reviewDigest()) return no('STALE_REVIEW', 'the change under review is no longer the one you were shown; review it again');
    if (this.reviewStatus !== 'PENDING') return no('ALREADY_DECIDED', `this review is already ${this.reviewStatus.toLowerCase()}`);
    this.reviewStatus = request.decision === 'approve' ? 'APPROVED' : 'REJECTED';
    return { accepted: true, code: 'ACCEPTED', message: `${request.decision} recorded for review ${request.review_id}; the waiting run records it and continues`, launch_id: LAUNCH_ID, review_id: this.reviewId(), decision: request.decision, decided_at: '2026-10-10T10:11:30+00:00' };
  }

  /** A launch that never got past STARTING (the child died right after writing its record). */
  private stuck(): Json {
    return { launch: { launch_id: LAUNCH_ID, workspace: { path: this.launchWorkspace, identity: 'ab'.repeat(32) }, requested_at: '2026-10-10T10:10:10+00:00', goal: { sha256: 'ef'.repeat(32), chars: this.launchGoal.length }, options: {}, phase: 'STARTING', run: null, ended: null,
      history: [{ phase: 'STARTING', at: '2026-10-10T10:10:10+00:00' }], process: { pid: 4242 }, approval_required: null, declined_decisions: [], pending_approval: null, approval_history: [] },
    run_record: { found: false, unreadable: null }, evidence: { state: 'absent', line_count: null, sealed: false }, workspace_lock: { held: false, held_by_this_launch: false }, liveness: 'starting', liveness_note: 'DERIVED' };
  }

  /** A read that straddled the process's own final write: RUNNING with the lock already released (the next read shows the sealed truth). */
  private stale(): Json {
    const base = this.statusFor('success') as Json;
    const launch = base.launch as Json;
    return { ...base, launch: { ...launch, phase: 'RUNNING', ended: null }, run_record: { found: true, unreadable: null, lifecycle_state: 'RUNNING', terminal_status: null, commit_result: null, revision: 2 }, workspace_lock: { held: false, held_by_this_launch: false }, liveness: 'interrupted' };
  }

  /** The live records of the run, up to the current frame (verbatim shapes of the recorder's records). */
  records(fromSeq: number, limit: number): Json[] {
    const lines = LINES_AT[Math.max(this.frame, 0)] ?? 0;
    const out: Json[] = [];
    for (let seq = Math.max(fromSeq, 1); seq <= lines && out.length < limit; seq += 1) {
      const kind = RECORD_KINDS[seq - 1] ?? 'run.closed';
      out.push({
        seq, prev: 'sha256:' + String(seq).padStart(2, '0').repeat(32).slice(0, 64), kind, run_id: LAUNCH_ID, unit_id: seq >= 2 ? 'direct' : null, unit_kind: seq >= 2 ? 'direct' : null, invocation_seq: null,
        phase: null, attempt_number: seq >= 3 ? 1 : null, call_seq: null, wire_seq: null, role: kind.startsWith('model.') ? 'developer' : null, t_wall: `2026-10-10T10:10:${String(12 + seq).padStart(2, '0')}Z`, t_mono_ms: seq * 100,
        provenance: 'observed', payload: {}, content_digests: {}, blobs: {}, schema: 'kriya.attempt_evidence/1', phase_provenance: null,
      });
    }
    return out;
  }
  lineCount(): number { return LINES_AT[Math.max(this.frame, 0)] ?? 0; }
}
