/**
 * A scripted, step-driven stand-in for Kriya's execution side (GUI M6): DEV/TEST ONLY, imported only by fixtureHost.ts and tests.
 *
 * It does not run anything. It answers the same questions Kriya answers about a launch (`execution.preflight`, `launch.status`, the live
 * `evidence.records` of the run) from a script whose frames the TEST advances explicitly (`advance()`), so every UI state - launching, running, each
 * terminal outcome, a process that dies, a launch Kriya never recorded - is reachable deterministically, without timers and without a model.
 * The host-process side (`ExecutionHostPort`) reports only the process, exactly like the real host.
 */
import type { ExecutionHostPort, NotifyRequest, NotifyResult } from '../../../host/HostAdapter';
import type { ApproveConfigurationRequest, ApproveConfigurationResult, InitializeProjectRequest, InitializeProjectResult, ConfigurationApprovalStatus, DecideApprovalRequest, DecideApprovalResult, DecideRuntimeCommandRequest, DecideRuntimeCommandResult, ExecutionState, StartRunResult } from '../../../model/kup';

export type FixtureOutcome = 'success' | 'failure' | 'died' | 'refused' | 'never_recorded' | 'approval_required' | 'stuck_starting' | 'success_after_stale_read' | 'success_with_declined' | 'review' | 'runtime_review';

export interface FixtureExecutionOptions {
  /** How the next launch ends (default 'success'). */
  outcome?: FixtureOutcome;
  /** The RunRecord's commit result of a successful run (default COMMITTED; the real controlled body records NO_COMMIT). */
  commitResult?: string;
  /** Folders whose preflight carries a blocker (code -> message), besides the built-in /fixture/blocked. */
  blockers?: Readonly<Record<string, { code: string; message: string }>>;
  /** Scripted readiness facts for every folder (DEV/TEST ONLY): what Kriya's preflight reports about configuration, state, tools and approvals. */
  facts?: {
    blockers?: ReadonlyArray<{ code: string; message: string }>;
    gitAvailable?: boolean;
    authority?: 'no_findings' | 'approved' | 'refused' | 'failed';
    configFailed?: boolean;
    stateDirectory?: string | null;
    observable?: boolean;
    approvals?: 'change_review' | 'configured_mode';
  };
  /**
   * A scripted configuration-authority world (DEV/TEST ONLY): the operator-controlled settings this folder's configuration carries. While any exist and no current
   * approval covers them, the preflight reports the CONFIG_AUTHORITY_REFUSED blocker (as Kriya does) and `configuration.approval_status` lists their NAMES.
   * `configuration.approve` records an approval only for the fingerprint the status gave and only while the configuration is unchanged (`changeConfiguration()`).
   * It takes precedence over `facts.authority`.
   */
  configuration?: {
    fields: readonly string[];
    /** The approval cannot be given from this application: the status is `unavailable` with this reason. */
    unavailable?: { reason: string; message: string };
    /** `configuration.approve` fails with this typed error (e.g. APPROVAL_UNAVAILABLE / store_unwritable). */
    approveFault?: { code: string; message: string; reason?: string };
    /** The host offers no way to record an approval (a browser test host): the page falls back to saying so. */
    noApprovalPort?: boolean;
    /** A folder with NO configuration file yet (Project Setup is offered): `fields` stays empty until Setup creates the file, then these protected settings appear (and need approval). */
    unconfigured?: { fieldsAfterSetup: readonly string[]; fault?: { code: string; message: string; reason?: string } };
    /** The host offers no way to create a configuration (a browser test host). */
    noSetupPort?: boolean;
  };
  /** How the scripted change review behaves (DEV/TEST ONLY): Kriya cannot serve the exact change (`unavailable`: no stored diff / `unverified`: the stored diff does not match its digests), or the change is larger than one response (`truncated`). */
  review?: { fault?: 'unavailable' | 'unverified'; truncated?: boolean; /** The review answers with a candidate digest that is NOT the pending review's (a host or store out of step). */ digestMismatch?: boolean };
  /** How the scripted runtime-command review behaves (DEV/TEST ONLY): Kriya cannot prove the recorded commands against their fingerprint (`unverified`), so none are shown. */
  runtimeReview?: { fault?: 'unverified' };
  /** The host has no runtime-command decision path (an older host): the review is shown but cannot be decided from this window. */
  noRuntimeDecisionPort?: boolean;
  /** The host refuses the start with this typed refusal (EXECUTION_BUSY is produced by the real host; here for UI tests). */
  refuseStart?: { code: string; message: string };
}

export const LAUNCH_ID = '20261010T101010-abcdef01';
export const LAUNCHED_TRACE_ID = 'fx-launched';
export const REVIEW_ID = 'ra-' + 'a1'.repeat(12);
export const REVIEW_DIGEST = 'sha256:' + 'b2'.repeat(32);
export const REVIEW_REASON = 'Human-in-the-loop review policy\n\n=== Automated Code Review ===\nThe change implements the goal and keeps the public signature.';
/** The scripted runtime-command review (Demo V2): the exact commands Kriya asks to run, in the isolated worktree. */
export const RUNTIME_COMMANDS: [string, ...string[]][] = [['mvn', '-q', 'package'], ['java', '-cp', 'target/classes', 'com.example.App']];
export const RUNTIME_WORKTREE = '/fixture/.kriya/worktrees/candidate-1';
export const RUNTIME_CRITERION = 'The application prints "Hello from Kriya" and exits with status 0.';
export const RUNTIME_FINGERPRINT = 'sha256:' + '5e'.repeat(32);
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
  /** Every runtime-command decision request that reached Kriya (a request refused or cancelled in the host never gets here). */
  readonly runtimeDecisions: DecideRuntimeCommandRequest[] = [];
  readonly port: ExecutionHostPort;
  /** Every configuration approval request the host received, and every status read (for assertions). */
  readonly approvals: ApproveConfigurationRequest[] = [];
  /** Every Project Setup request the host received. */
  readonly setups: InitializeProjectRequest[] = [];
  private setupDone = false;
  /** Every `change_approval.review` read the fixture answered (a request that never reaches Kriya is not here). */
  readonly reviewReads: Array<{ launch_id: string; review_id: string }> = [];
  /** The scripted answers of the host's NATIVE confirmation, consumed in order (true = Approve Configuration, false = Cancel); once empty the fixture host approves. Test hook. */
  readonly nativeAnswers: boolean[] = [];
  /** How many times the (scripted) native confirmation was shown. A request that never reaches it never reaches Kriya either. */
  confirmationsShown = 0;
  statusReads = 0;
  private configFields: string[];
  private configRevision = 1;
  private approvedFingerprint: string | null = null;

  constructor(private readonly options: FixtureExecutionOptions = {}) {
    this.outcome = options.outcome ?? 'success';
    this.configFields = [...(options.configuration?.fields ?? [])];
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
      ...(options.noRuntimeDecisionPort ? {} : { decideRuntimeCommand: async (request: DecideRuntimeCommandRequest): Promise<DecideRuntimeCommandResult> => this.decideRuntime(request) }),
      ...(options.configuration?.noApprovalPort ? {} : { approveConfiguration: async (request: ApproveConfigurationRequest): Promise<ApproveConfigurationResult> => this.confirmThenApprove(request) }),
      ...(options.configuration?.noSetupPort ? {} : { initializeProject: async (request: InitializeProjectRequest): Promise<InitializeProjectResult> => this.initializeProjectNow(request) }),
      notify: async (request): Promise<NotifyResult> => { this.notifications.push(request); return { shown: true, reason: 'shown' }; },
    };
  }

  setOutcome(outcome: FixtureOutcome): void { this.outcome = outcome; }

  // ---- the scripted configuration-authority world ---------------------------------------------------------------------
  /** Test hook: the folder's operator-controlled settings change (any of them added, removed or changed): the fingerprint changes, a prior approval stops covering. */
  changeConfiguration(fields?: readonly string[]): void { if (fields) this.configFields = [...fields]; this.configRevision += 1; }
  /** Test hook: the approval is revoked (as `kriya authority revoke`). */
  revokeConfigurationApproval(): void { this.approvedFingerprint = null; }
  private configScripted(): boolean { return this.options.configuration !== undefined; }
  private fingerprint(): string | null {
    if (this.configFields.length === 0) return null;
    let h = 17;
    for (const ch of `${this.configFields.join(',')}#${this.configRevision}`) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
    return 'sha256:' + h.toString(16).padStart(8, '0').repeat(8);
  }
  private configCovered(): boolean { const fp = this.fingerprint(); return fp !== null && this.approvedFingerprint === fp; }
  /** `configuration.approval_status` data: NAMES only, as Kriya reports it. */
  approvalStatus(workspace: string): ConfigurationApprovalStatus {
    this.statusReads += 1;
    const fp = this.fingerprint();
    const unavailable = this.options.configuration?.unavailable;
    const covered = this.configCovered();
    const status = fp === null ? 'not_required' : unavailable ? 'unavailable' : covered ? 'approved' : 'required';
    return {
      workspace, identity: 'ab'.repeat(32), status, reason: status === 'required' ? (this.approvedFingerprint === null ? 'no_approval' : 'stale_approval') : unavailable?.reason ?? null, required: fp !== null,
      can_approve: status === 'required', blocking_reason: unavailable?.message ?? null,
      protected_fields: this.configFields.map((field) => ({ field, classification: field.startsWith('mcp.') ? 'security_authority' : 'platform_policy', source: 'auto_discovered_cwd' })),
      fingerprint: fp, scope: 'This approval applies only to the current operator-controlled settings of this folder. If any of them is added, removed or changed, Kriya asks for approval again.',
      values_shown: false, note: 'Only the names of the settings are shown. Their values are not sent to the application.',
    } as ConfigurationApprovalStatus;
  }
  /** Project Setup, scripted: the same refusals Kriya makes, otherwise the recommended profile is created and the folder's protected settings appear (and need approval). */
  private initializeProjectNow(request: InitializeProjectRequest): InitializeProjectResult {
    this.setups.push(request);
    const no = (code: string, message: string, reason: string | null = null): InitializeProjectResult => ({ initialized: false, outcome: null, code, message, reason, summary: null });
    const world = this.options.configuration?.unconfigured;
    if (!world || this.setupDone) return no('INVALID_REQUEST', 'this folder already has kriya.yaml; Project Setup never overwrites a configuration', 'already_configured');
    if (world.fault) return no(world.fault.code, world.fault.message, world.fault.reason ?? null);
    this.setupDone = true;
    this.configFields = [...world.fieldsAfterSetup];
    this.configRevision += 1;
    return { initialized: true, outcome: 'created', code: null, message: null, reason: null, summary: { model: 'fixture-model:7b', fallback_models: ['fixture-fallback:3b'], workflow: 'enforce', changes: 'held for your approval', web_lookup: 'off' } };
  }
  /** The real host asks the person (native confirmation) BEFORE Kriya is asked; Cancel is a normal decision (`declined`), Kriya never hears of it and nothing is written. */
  private confirmThenApprove(request: ApproveConfigurationRequest): ApproveConfigurationResult {
    this.confirmationsShown += 1;
    if (this.nativeAnswers.length > 0 && this.nativeAnswers.shift() === false) return { approved: false, outcome: 'declined', code: null, message: null, reason: null };
    return this.approveConfig(request);
  }
  /** Kriya's `configuration.approve`, scripted: the same refusals in the same order (grammar, stale, unavailable, fault), then the approval. */
  private approveConfig(request: ApproveConfigurationRequest): ApproveConfigurationResult {
    this.approvals.push(request);
    const no = (code: string, message: string, reason: string | null = null): ApproveConfigurationResult => ({ approved: false, outcome: null, code, message, reason });
    if (!/^sha256:[0-9a-f]{64}$/.test(request.fingerprint)) return no('INVALID_REQUEST', 'fingerprint must be the review fingerprint Kriya gave (sha256:<64 hex>)', 'invalid_fingerprint');
    if (this.fingerprint() !== request.fingerprint) return no('APPROVAL_STALE', 'The configuration changed after it was reviewed; nothing was approved. Review the current configuration before approving.', 'configuration_changed');
    const unavailable = this.options.configuration?.unavailable;
    if (unavailable) return no('APPROVAL_UNAVAILABLE', unavailable.message, unavailable.reason);
    const fault = this.options.configuration?.approveFault;
    if (fault) return no(fault.code, fault.message, fault.reason ?? null);
    if (this.configCovered()) return { approved: true, outcome: 'already_approved', code: null, message: null, reason: null };
    this.approvedFingerprint = request.fingerprint;
    return { approved: true, outcome: 'approved', code: null, message: null, reason: null };
  }
  launched(): boolean { return this.frame >= 0; }
  workspace(): string { return this.launchWorkspace; }
  goal(): string { return this.launchGoal; }

  /** Test hook: the run moves to its next frame. */
  advance(): void { if (this.frame >= 0 && this.frame < 4 && !this.reviewPending()) this.frame += 1; }
  /** Test hook: jump to the final frame (a run waiting on an undecided review cannot be finished: it is waiting). */
  finish(): void { if (this.frame >= 0 && !this.reviewPending()) this.frame = 4; }
  /** The run is waiting on the human's decision (a pending review exists and nobody has decided it). */
  private reviewOutcome(): boolean { return this.outcome === 'review' || this.outcome === 'runtime_review'; }
  reviewPending(): boolean { return this.reviewOutcome() && this.frame === REVIEW_FRAME && this.reviewStatus === 'PENDING'; }
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
  /** `change_approval.review`, scripted: the same refusals Kriya makes, otherwise the exact change of the review that was asked for (the current one, or an earlier one marked EXPIRED). */
  changeReview(request: { workspace: string; launch_id: string; review_id: string }): { data: Json } | { code: string; message: string; extra: Json } {
    this.reviewReads.push({ launch_id: request.launch_id, review_id: request.review_id });
    if (this.frame < 0 || request.launch_id !== LAUNCH_ID) return { code: 'LAUNCH_NOT_FOUND', message: `no launch ${request.launch_id} is recorded for this folder`, extra: { reason: 'not_recorded' } };
    let sequence = 0;
    for (let n = 1; n <= this.reviewSequence; n += 1) if (this.idFor(n) === request.review_id) sequence = n;
    if (sequence === 0 || this.outcome !== 'review') return { code: 'REVIEW_NOT_FOUND', message: `launch ${request.launch_id} has no review ${request.review_id}`, extra: { reason: 'not_recorded' } };
    const fault = this.options.review?.fault;
    if (fault === 'unavailable') return { code: 'REVIEW_DIFF_UNAVAILABLE', message: 'the proposed change was not stored for inspection, or its record cannot be trusted', extra: { reason: 'not_stored' } };
    if (fault === 'unverified') return { code: 'REVIEW_DIFF_UNVERIFIED', message: 'the stored proposed change does not match the review it belongs to; it is not shown', extra: { reason: 'file_digest_mismatch' } };
    const current = sequence === this.reviewSequence;
    const body = sequence === 1 ? ['def clamp(value, low, high):', '    return max(low, min(value, high))', ''] : ['def clamp(value, low, high):', '    return min(max(value, low), high)', ''];
    const truncated = this.options.review?.truncated === true;
    return { data: {
      launch_id: LAUNCH_ID, review_id: request.review_id, status: current ? (this.reviewSuperseded ? 'EXPIRED' : this.reviewStatus) : 'EXPIRED', candidate_digest: this.options.review?.digestMismatch ? 'sha256:' + 'ee'.repeat(32) : this.digestFor(sequence), verified: true,
      reason: REVIEW_REASON, policy_note: 'Human-in-the-loop review policy', review_summary: 'The change implements the goal and keeps the public signature.', reason_truncated: false, truncated,
      files: [{ path: 'src/clamp.py', diff_sha256: 'sha256:' + 'c3'.repeat(32), change_type: 'added', additions: body.length, deletions: 0, truncated,
        hunks: [{ header: `@@ -0,0 +1,${body.length} @@`, old_start: 0, old_lines: 0, new_start: 1, new_lines: body.length, lines: (truncated ? body.slice(0, 1) : body).map((text, i) => ({ kind: 'add', text, old_line: null, new_line: i + 1 })) }] }],
    } };
  }
  private idFor(n: number): string { return n === 1 ? REVIEW_ID : 'ra-' + String(n).padStart(2, '0').repeat(12); }
  private digestFor(n: number): string { return n === 1 ? REVIEW_DIGEST : 'sha256:' + String(n).padStart(2, '0').repeat(32); }
  private runtimeFingerprint(): string { return this.reviewSequence === 1 ? RUNTIME_FINGERPRINT : 'sha256:' + String(this.reviewSequence).padStart(2, '0').repeat(32); }
  private reviewDigest(): string { return this.reviewSequence === 1 ? REVIEW_DIGEST : 'sha256:' + String(this.reviewSequence).padStart(2, '0').repeat(32); }

  /** The process is alive (the real host's `active()`): not yet at the final frame. */
  active(): boolean { return this.frame >= 0 && this.frame < 4 && !this.killed; }
  /** The run's final records are durable (a finished, sealed run the history now lists). */
  private succeeds(): boolean { return (this.reviewOutcome() && this.reviewStatus === 'APPROVED') || this.outcome === 'success' || this.outcome === 'success_after_stale_read' || this.outcome === 'success_with_declined'; }
  ended(): boolean { return this.frame === 4 && (this.succeeds() || this.outcome === 'failure' || (this.reviewOutcome() && this.reviewStatus === 'REJECTED')); }

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
  /** A folder has a configuration file once its protected settings come from one (a scripted world with fields) or Project Setup created it. */
  private hasConfigFile(): boolean { return this.setupDone || (this.configScripted() && this.options.configuration?.unconfigured === undefined && this.configFields.length > 0); }
  preflight(workspace: string, stateDirectory: string, blocked: { code: string; message: string } | null): Json {
    const facts = this.options.facts ?? {};
    const scripted = this.configScripted();
    const authority = scripted ? (this.configFields.length === 0 ? 'no_findings' : this.configCovered() ? 'approved' : 'refused') : facts.authority ?? 'no_findings';
    const blockers = [...(blocked ? [blocked] : []), ...(scripted && authority === 'refused' ? [{ code: 'CONFIG_AUTHORITY_REFUSED', message: `${this.configFields.length} field(s) in this folder's configuration need an operator approval (kriya authority approve): ${this.configFields.slice(0, 6).join(', ')}` }] : []), ...(facts.blockers ?? [])];
    const git = facts.gitAvailable === false ? { tool: 'git', available: false, path: null, reason: 'exits 69: the Xcode license has not been accepted' } : { tool: 'git', available: true, path: '/usr/bin/git' };
    return {
      ready: blockers.length === 0, blockers,
      workspace: { path: workspace, identity: 'ab'.repeat(32), name: workspace.slice(workspace.lastIndexOf('/') + 1) },
      configuration: { status: facts.configFailed ? 'failed' : authority === 'refused' ? 'refused' : 'loaded', source: facts.configFailed ? 'unreadable' : this.hasConfigFile() ? 'auto_discovered_cwd' : 'packaged_default', file: this.hasConfigFile() ? `${workspace}/kriya.yaml` : null, directory: workspace, discovery: 'Configuration is discovered from the process working directory, which is this folder.', fingerprint: facts.configFailed ? null : 'cd'.repeat(32), authority: { status: facts.configFailed ? 'failed' : authority, fields: [] } },
      state: { directory: facts.stateDirectory === undefined ? stateDirectory : facts.stateDirectory, source: 'env', cockpit_directory: stateDirectory, observable_by_cockpit: facts.observable ?? true },
      model: facts.configFailed ? null : { provider: 'openai', model: 'fixture-model:7b', endpoint: 'http://localhost:11434/v1', context_window: 32768, fallback_chain: ['fixture-fallback:3b'], role_overrides: {} },
      recovery: { status: 'CLEAN', run_active: false },
      interaction: facts.approvals === 'configured_mode' ? { approvals: 'configured_mode', autonomy_mode: 'autonomous', knowledge_policy: 'strict', note: 'This folder\'s configuration sets the autonomy mode to \'autonomous\': a routine change is not held for review by this configuration.' } : { approvals: 'change_review', autonomy_mode: 'human-in-the-loop', knowledge_policy: 'strict', note: 'A change Kriya proposes is held for your review in this window and applied only if you approve it. Any other step that needs a human decision (a knowledge-gap acknowledgement, a skill choice, a command approval) stops the run instead of being approved for you.' },
      toolchains: { tools: [{ tool: 'python3', available: true, path: '/usr/bin/python3' }, git, { tool: 'java', available: false, path: null }, { tool: 'mvn', available: false, path: null }], note: 'Visibility under this process\'s own environment (a fixed PATH; no login shell). Which tools a goal needs is only known while the workflow runs.' },
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
    if (!this.reviewOutcome() || this.frame < REVIEW_FRAME) return null;
    const decided = this.reviewStatus !== 'PENDING';
    if (this.outcome === 'runtime_review') {
      return {
        review_id: this.reviewId(), sequence: this.reviewSequence, kind: 'runtime_command', status: this.reviewStatus, requested_at: '2026-10-10T10:11:00+00:00', expires_at: '2026-10-10T11:11:00+00:00',
        candidate_digest: this.runtimeFingerprint(), command_fingerprint: this.runtimeFingerprint(), unit_id: 's4', criterion: RUNTIME_CRITERION, commands: RUNTIME_COMMANDS, working_directory: RUNTIME_WORKTREE,
        working_directory_kind: 'ISOLATED_WORKTREE', input_channel: 'none', stdin_present: false, stdin_sha256: null, environment_names: [],
        reason: 'Kriya wants to run commands to verify the application', reason_truncated: false, reason_sha256: 'sha256:' + 'd4'.repeat(32),
        decision: decided ? { decision: this.reviewStatus === 'APPROVED' ? 'approve' : 'reject', decided_at: '2026-10-10T10:11:30+00:00', source: 'launch-decision' } : null,
      };
    }
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
    const approved = (outcome === 'review' || outcome === 'runtime_review') && this.reviewStatus === 'APPROVED';
    const rejected = (outcome === 'review' || outcome === 'runtime_review') && this.reviewStatus === 'REJECTED';
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
      run_record: run ? { found: true, unreadable: null, lifecycle_state: terminal ?? 'RUNNING', terminal_status: terminal, commit_result: terminal === 'SUCCESS' ? this.options.commitResult ?? 'COMMITTED' : null, revision: 2 } : { found: false, unreadable: null },
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
    if (this.outcome === 'runtime_review' && this.frame >= REVIEW_FRAME) return no('WRONG_REVIEW_KIND', 'that review asks to run commands; it is decided as a runtime-command review');
    if (this.outcome !== 'review' || this.frame < REVIEW_FRAME) return no('NOT_PENDING', 'no change is waiting for review in this launch');
    if (this.reviewSuperseded || request.review_id !== this.reviewId() || request.candidate_digest !== this.reviewDigest()) return no('STALE_REVIEW', 'the change under review is no longer the one you were shown; review it again');
    if (this.reviewStatus !== 'PENDING') return no('ALREADY_DECIDED', `this review is already ${this.reviewStatus.toLowerCase()}`);
    this.reviewStatus = request.decision === 'approve' ? 'APPROVED' : 'REJECTED';
    return { accepted: true, code: 'ACCEPTED', message: `${request.decision} recorded for review ${request.review_id}; the waiting run records it and continues`, launch_id: LAUNCH_ID, review_id: this.reviewId(), decision: request.decision, decided_at: '2026-10-10T10:11:30+00:00' };
  }

  /** `runtime_command.review`, scripted: the same refusals Kriya makes, otherwise the exact recorded command set of the review that was asked for. */
  runtimeReview(request: { workspace: string; launch_id: string; review_id: string }): { data: Json } | { code: string; message: string; extra: Json } {
    if (this.frame < 0 || request.launch_id !== LAUNCH_ID) return { code: 'LAUNCH_NOT_FOUND', message: `no launch ${request.launch_id} is recorded for this folder`, extra: { reason: 'not_recorded' } };
    if (this.outcome === 'review' && this.frame >= REVIEW_FRAME && request.review_id === this.reviewId()) return { code: 'REVIEW_NOT_FOUND', message: `review ${request.review_id} is not a runtime-command review`, extra: { reason: 'wrong_kind' } };
    if (this.outcome !== 'runtime_review' || request.review_id !== this.reviewId()) return { code: 'REVIEW_NOT_FOUND', message: `launch ${request.launch_id} has no review ${request.review_id}`, extra: { reason: 'not_recorded' } };
    if (this.options.runtimeReview?.fault === 'unverified') return { code: 'REVIEW_COMMAND_UNVERIFIED', message: 'the recorded commands do not match the fingerprint the review is bound to; they are not shown', extra: { reason: 'fingerprint_mismatch' } };
    return { data: {
      launch_id: LAUNCH_ID, review_id: request.review_id, run_id: LAUNCH_ID, status: this.reviewSuperseded ? 'EXPIRED' : this.reviewStatus, verified: true, command_fingerprint: this.runtimeFingerprint(), unit_id: 's4',
      criterion: RUNTIME_CRITERION, commands: RUNTIME_COMMANDS, working_directory: RUNTIME_WORKTREE, working_directory_kind: 'ISOLATED_WORKTREE', input_channel: 'none', stdin_present: false, environment_names: [],
      requested_at: '2026-10-10T10:11:00+00:00', expires_at: '2026-10-10T11:11:00+00:00',
    } };
  }
  /** The host's runtime-command decision, scripted: an approve passes the (scripted) NATIVE confirmation first; Cancel sends nothing and leaves the review pending. Kriya's closed refusals, in order, otherwise. */
  private decideRuntime(request: DecideRuntimeCommandRequest): DecideRuntimeCommandResult {
    const no = (code: string, message: string): DecideRuntimeCommandResult => ({ accepted: false, code, message, launch_id: null, review_id: null, decision: null, decided_at: null });
    if (request.decision === 'approve') {
      this.confirmationsShown += 1;
      if (this.nativeAnswers.length > 0 && this.nativeAnswers.shift() === false) return no('CONFIRMATION_DECLINED', 'Nothing was run. The request is still waiting for your decision.');
    }
    this.runtimeDecisions.push(request);
    if (this.frame < 0 || request.launch_id !== LAUNCH_ID) return no('LAUNCH_NOT_FOUND', `no launch ${request.launch_id} is recorded for this folder`);
    if (request.workspace !== this.launchWorkspace) return no('WRONG_WORKSPACE', 'this launch belongs to a different workspace');
    if (this.killed) return no('LAUNCH_NOT_ACTIVE', 'the run that was waiting for this decision is no longer running');
    if (this.frame === 4) return no('LAUNCH_ENDED', 'the run has already ended; there is nothing left to approve');
    if (this.outcome === 'review' && this.frame >= REVIEW_FRAME) return no('WRONG_REVIEW_KIND', 'that review is a change review; it is decided as a change review');
    if (this.outcome !== 'runtime_review' || this.frame < REVIEW_FRAME) return no('NOT_PENDING', 'no runtime command is waiting for review in this launch');
    if (this.reviewSuperseded || request.review_id !== this.reviewId() || request.command_fingerprint !== this.runtimeFingerprint()) return no('STALE_REVIEW', 'the commands under review are no longer the ones you were shown; review them again');
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
