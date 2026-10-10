/**
 * A scripted, step-driven stand-in for Kriya's execution side (GUI M6): DEV/TEST ONLY, imported only by fixtureHost.ts and tests.
 *
 * It does not run anything. It answers the same questions Kriya answers about a launch (`execution.preflight`, `launch.status`, the live
 * `evidence.records` of the run) from a script whose frames the TEST advances explicitly (`advance()`), so every UI state - launching, running, each
 * terminal outcome, a process that dies, a launch Kriya never recorded - is reachable deterministically, without timers and without a model.
 * The host-process side (`ExecutionHostPort`) reports only the process, exactly like the real host.
 */
import type { ExecutionHostPort } from '../../../host/HostAdapter';
import type { ExecutionState, StartRunResult } from '../../../model/kup';

export type FixtureOutcome = 'success' | 'failure' | 'died' | 'refused' | 'never_recorded' | 'approval_required';

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
  readonly port: ExecutionHostPort;

  constructor(private readonly options: FixtureExecutionOptions = {}) {
    this.outcome = options.outcome ?? 'success';
    this.port = {
      startRun: async (request): Promise<StartRunResult> => {
        this.starts.push({ workspace: request.workspace, goal: request.goal });
        if (this.options.refuseStart) return { started: false, launch_id: null, refusal: this.options.refuseStart };
        if (this.active()) return { started: false, launch_id: null, refusal: { code: 'EXECUTION_BUSY', message: 'a run is already in progress; only one run at a time can be started from this window' } };
        this.frame = 0;
        this.launchGoal = request.goal;
        this.launchWorkspace = request.workspace;
        return { started: true, launch_id: LAUNCH_ID, refusal: null };
      },
      state: async (): Promise<ExecutionState> => this.hostState(),
    };
  }

  setOutcome(outcome: FixtureOutcome): void { this.outcome = outcome; }
  launched(): boolean { return this.frame >= 0; }
  workspace(): string { return this.launchWorkspace; }
  goal(): string { return this.launchGoal; }

  /** Test hook: the run moves to its next frame. */
  advance(): void { if (this.frame >= 0 && this.frame < 4) this.frame += 1; }
  /** Test hook: jump to the final frame. */
  finish(): void { if (this.frame >= 0) this.frame = 4; }

  /** The process is alive (the real host's `active()`): not yet at the final frame. */
  active(): boolean { return this.frame >= 0 && this.frame < 4; }
  /** The run's final records are durable (a finished, sealed run the history now lists). */
  ended(): boolean { return this.frame === 4 && (this.outcome === 'success' || this.outcome === 'failure'); }

  private hostState(): ExecutionState {
    if (this.frame < 0) return { phase: 'idle', launch_id: null, workspace: null, started_at: null, ended_at: null, exit: null, diagnostic: null };
    const over = this.frame === 4;
    const code = this.outcome === 'success' ? 0 : this.outcome === 'refused' ? 2 : this.outcome === 'died' ? null : 1;
    return {
      phase: over ? 'exited' : 'running', launch_id: LAUNCH_ID, workspace: this.launchWorkspace, started_at: '2026-10-10T10:10:10.000Z', ended_at: over ? '2026-10-10T10:12:00.000Z' : null,
      exit: over ? { code, signal: this.outcome === 'died' ? 'SIGKILL' : null } : null, diagnostic: null,
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
      interaction: { approvals: 'unavailable', knowledge_policy: 'strict', note: 'This host has no interactive approval: a step that needs one stops the run instead of being approved for you.' },
      toolchains: { tools: [{ tool: 'python3', available: true, path: '/usr/bin/python3' }, { tool: 'git', available: true, path: '/usr/bin/git' }, { tool: 'java', available: false, path: null }, { tool: 'mvn', available: false, path: null }], note: 'Visibility under this process\'s own environment (a fixed PATH; no login shell). Which tools a goal needs is only known while the workflow runs.' },
    };
  }

  /** `launch.status` data for the scripted launch, or null when Kriya has not recorded it (the answer is LAUNCH_NOT_FOUND). */
  launchStatus(): Json | null {
    const f = this.frame;
    if (f < 0 || this.outcome === 'never_recorded') return null;
    if (f === 0) return null; // the process exists; Kriya has not written the launch record yet
    const lines = LINES_AT[f] ?? 0;
    const sealed = f === 4 && (this.outcome === 'success' || this.outcome === 'failure');
    const refused = this.outcome === 'refused' && f === 4;
    const approval = this.outcome === 'approval_required' && f === 4;
    const died = this.outcome === 'died' && f === 4;
    const phase = refused || sealed || approval ? 'ENDED' : f === 1 ? 'STARTING' : 'RUNNING';
    const terminal = sealed ? (this.outcome === 'success' ? 'SUCCESS' : 'FAILURE') : approval ? 'FAILURE' : null;
    const run = f >= 2 && !refused ? { run_id: LAUNCH_ID, started_at: '2026-10-10T10:10:12+00:00', base_revision: null } : null;
    const ended = phase === 'ENDED' ? {
      at: '2026-10-10T10:12:00+00:00', exit_code: refused ? 2 : this.outcome === 'success' ? 0 : 1, workflow_status: approval ? null : refused ? 'refused' : this.outcome === 'success' ? 'success' : 'failure',
      quality_gates_passed: refused ? false : this.outcome === 'success', failure_category: this.outcome === 'failure' ? 'quality_gate_failed' : null, trace_run_id: refused || approval ? null : LAUNCHED_TRACE_ID,
      error: refused ? 'another Kriya run already owns this folder' : approval ? 'Aborted!' : null, note: 'copied from the workflow\'s own result; the RunRecord, evidence store and trace row are the outcome authority',
    } : null;
    return {
      launch: {
        launch_id: LAUNCH_ID, workspace: { path: this.launchWorkspace, identity: 'ab'.repeat(32) }, requested_at: '2026-10-10T10:10:10+00:00', goal: { sha256: 'ef'.repeat(32), chars: this.launchGoal.length },
        options: { knowledge_policy: 'strict', interactive: false }, phase, run, ended,
        history: [{ phase: 'STARTING', at: '2026-10-10T10:10:10+00:00' }, ...(run ? [{ phase: 'RUNNING', at: '2026-10-10T10:10:12+00:00' }] : []), ...(ended ? [{ phase: 'ENDED', at: '2026-10-10T10:12:00+00:00' }] : [])],
        process: { pid: 4242 },
        approval_required: approval ? { kind: 'change_approval', reason: 'the proposed change touches a sensitive path', details: { files: ['.github/workflows/ci.yml'] }, noted_at: '2026-10-10T10:11:59+00:00' } : null,
      },
      run_record: run ? { found: true, unreadable: null, lifecycle_state: terminal ?? 'RUNNING', terminal_status: terminal, commit_result: terminal === 'SUCCESS' ? 'COMMITTED' : null, revision: 2 } : { found: false, unreadable: null },
      evidence: run ? { state: sealed ? 'sealed' : 'open', line_count: lines, sealed } : { state: 'absent', line_count: null, sealed: false },
      workspace_lock: { held: f >= 2 && !sealed && !died && !refused, held_by_this_launch: f >= 2 && !sealed && !died && !refused },
      liveness: phase === 'ENDED' ? 'ended' : died ? 'interrupted' : f === 1 ? 'starting' : 'live',
      liveness_note: 'DERIVED from the launch phase and the workspace run lock; a process exit alone is never the outcome',
    };
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
