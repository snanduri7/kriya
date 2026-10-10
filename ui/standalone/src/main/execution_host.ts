/**
 * Start Run, host side (GUI M6). Pure: no Electron import, no `child_process` import - the process launcher, Kriya's preflight, the executable and
 * environment policy and the clock are injected, so the rules are tested with controlled executors and the production wiring is one small block in main.ts.
 *
 * What this module IS: the single place a Kriya EXECUTION process is started. It is separate from the persistent read process (kriya_service.ts) on
 * purpose - a coding run can take minutes and must never occupy the transport that serves navigation and evidence reads.
 *
 * What it is NOT: an authority. It does not decide whether a run may start (Kriya's preflight does, and the launched process re-resolves it and
 * refuses on any blocker), it does not read the run's result, and it forwards no process output - the only outcome the cockpit ever shows comes from
 * Kriya's durable records (launch record, RunRecord, evidence store, trace row). A process exit is reported as a process exit, nothing more.
 *
 * Launch contract, exactly one shape:
 *     kriya generate --json --launch-id <id>        cwd = the opened workspace, goal on stdin, shell: false, the host's fixed child environment
 * No option can be added by the renderer: the request is {workspace, goal} (validateStartRun). The workspace must be the folder currently open in
 * this host (chosen with Open Folder - the registry's current workspace), so a compromised renderer cannot launch in a folder the person never opened.
 */
import { LAUNCH_ID_RE, validateStartRun } from './ipc_contract';

export interface ExecChild {
  pid?: number;
  stdin: { write(chunk: string): unknown; end(): unknown; on(event: 'error', cb: (error: Error) => void): unknown } | null;
  stdout: { on(event: 'data', cb: (chunk: Buffer | string) => void): unknown } | null;
  stderr: { on(event: 'data', cb: (chunk: Buffer | string) => void): unknown } | null;
  on(event: 'exit', cb: (code: number | null, signal: string | null) => void): unknown;
  on(event: 'error', cb: (error: Error) => void): unknown;
  kill(signal?: NodeJS.Signals): boolean;
}

export interface Invocation { executable: string; argv: readonly string[]; cwd: string; env: Readonly<Record<string, string>> }

export type Prepared = { ok: true; executable: string; env: Record<string, string> } | { ok: false; message: string };

export interface ExecutionDeps {
  launch(invocation: Invocation): ExecChild;
  /** The executable and the fixed child environment (child_env.ts), or why none can be used. null = this host has no execution backend (fixture stand-in). */
  prepare(): Prepared | null;
  /** Kriya's `execution.preflight` envelope for the workspace (through the persistent read transport). */
  preflight(workspace: string): Promise<unknown>;
  /** The folder currently open in this host (the registry's current workspace). */
  currentWorkspace(): string | null;
  mintLaunchId(): string;
  now(): Date;
  /** SIGINT -> wait -> SIGKILL on shutdown. */
  graceMs?: number;
  /** Bounded diagnostics for the developer log only (never the UI). */
  log?(line: string): void;
}

export type RefusalCode = 'INVALID_REQUEST' | 'EXECUTION_BUSY' | 'EXECUTION_UNAVAILABLE' | 'WORKSPACE_NOT_OPEN' | 'PREFLIGHT_UNAVAILABLE' | 'PREFLIGHT_BLOCKED' | 'HOST_ERROR' | string;
export interface Refusal { code: RefusalCode; message: string; blockers?: Array<{ code: string; message: string }> }
export interface StartRunResult { started: boolean; launch_id: string | null; refusal: Refusal | null }

export type ExecutionPhase = 'idle' | 'starting' | 'running' | 'exited';
export type Diagnostic = 'spawn_failed' | 'terminated_by_the_host' | null;
export interface ExecutionState {
  phase: ExecutionPhase;
  launch_id: string | null;
  workspace: string | null;
  started_at: string | null;
  ended_at: string | null;
  exit: { code: number | null; signal: string | null } | null;
  diagnostic: Diagnostic;
}

const IDLE: ExecutionState = { phase: 'idle', launch_id: null, workspace: null, started_at: null, ended_at: null, exit: null, diagnostic: null };

/** The ONE command line an execution process ever gets. Pure and exported so the hardening test pins it. */
export function executionArgv(launchId: string): string[] {
  if (!LAUNCH_ID_RE.test(launchId)) throw new Error('launch id outside the grammar');
  return ['generate', '--json', '--launch-id', launchId];
}

function isEnvelope(value: unknown): value is { error: { code?: unknown; message?: unknown } | null; data: unknown } {
  return typeof value === 'object' && value !== null && 'error' in value && 'data' in value;
}

export class ExecutionHost {
  private current: ExecutionState = { ...IDLE };
  private child: ExecChild | null = null;
  private reserving = false;
  private stdoutBytes = 0;
  private stderrBytes = 0;
  private exited: Promise<void> = Promise.resolve();

  constructor(private readonly deps: ExecutionDeps) {}

  /** A run this host started is in progress (or being started): the workspace must not change, and the app must not quit silently. */
  active(): boolean { return this.reserving || this.current.phase === 'starting' || this.current.phase === 'running'; }

  state(): ExecutionState { return { ...this.current, exit: this.current.exit ? { ...this.current.exit } : null }; }

  private refuse(code: RefusalCode, message: string, blockers?: Refusal['blockers']): StartRunResult {
    return { started: false, launch_id: null, refusal: { code, message, ...(blockers ? { blockers } : {}) } };
  }

  async start(raw: unknown): Promise<StartRunResult> {
    const request = validateStartRun(raw);
    if (!request.ok) return this.refuse('INVALID_REQUEST', request.message);
    // Duplicate suppression covers the whole launch, including the preflight round trip: a second click while the first is still being
    // admitted is refused, not queued.
    if (this.active()) return this.refuse('EXECUTION_BUSY', 'a run is already in progress; only one run at a time can be started from this window');
    const prepared = this.deps.prepare();
    if (prepared === null) return this.refuse('EXECUTION_UNAVAILABLE', 'this build has no Kriya execution backend (fixture mode)');
    if (!prepared.ok) return this.refuse('HOST_ERROR', `kriya not launched: ${prepared.message}`);
    const { workspace, goal } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return this.refuse('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; open it with Open Folder first');
    this.reserving = true;
    try {
      let envelope: unknown;
      try {
        envelope = await this.deps.preflight(workspace);
      } catch (error) {
        return this.refuse('PREFLIGHT_UNAVAILABLE', `Kriya could not be asked for the execution preflight: ${error instanceof Error ? error.message : String(error)}`);
      }
      if (!isEnvelope(envelope)) return this.refuse('PREFLIGHT_UNAVAILABLE', 'Kriya returned something that is not a KUP envelope');
      if (envelope.error) return this.refuse(typeof envelope.error.code === 'string' ? envelope.error.code : 'PREFLIGHT_UNAVAILABLE', String(envelope.error.message ?? 'preflight refused'));
      const data = envelope.data as { ready?: unknown; blockers?: unknown } | null;
      if (!data || data.ready !== true) {
        const blockers = Array.isArray(data?.blockers) ? (data.blockers as Array<{ code: string; message: string }>).filter((b) => typeof b?.code === 'string' && typeof b?.message === 'string') : [];
        return this.refuse('PREFLIGHT_BLOCKED', blockers[0]?.message ?? 'Kriya reports this folder is not ready for a run', blockers);
      }
      // A changed workspace between the click and the end of the preflight is refused, never launched elsewhere.
      if (this.deps.currentWorkspace() !== workspace) return this.refuse('WORKSPACE_NOT_OPEN', 'the open workspace changed while the run was being prepared');

      const launchId = this.deps.mintLaunchId();
      let argv: string[];
      try {
        argv = executionArgv(launchId);
      } catch (error) {
        return this.refuse('HOST_ERROR', error instanceof Error ? error.message : String(error));
      }
      this.stdoutBytes = this.stderrBytes = 0;
      this.current = { phase: 'starting', launch_id: launchId, workspace, started_at: this.deps.now().toISOString(), ended_at: null, exit: null, diagnostic: null };
      let child: ExecChild;
      try {
        child = this.deps.launch({ executable: prepared.executable, argv, cwd: workspace, env: prepared.env });
      } catch (error) {
        this.current = { ...this.current, phase: 'exited', ended_at: this.deps.now().toISOString(), diagnostic: 'spawn_failed' };
        return this.refuse('HOST_ERROR', `the Kriya execution process could not be started: ${error instanceof Error ? error.message : String(error)}`);
      }
      this.child = child;
      this.exited = new Promise<void>((resolve) => {
        const finish = (state: Partial<ExecutionState>) => {
          if (this.child === child) {
            this.current = { ...this.current, phase: 'exited', ended_at: this.deps.now().toISOString(), ...state };
            this.child = null;
          }
          resolve();
        };
        child.on('exit', (code, signal) => finish({ exit: { code, signal }, diagnostic: this.current.diagnostic }));
        child.on('error', (error) => { this.deps.log?.(`execution process error: ${error.message}`); finish({ diagnostic: 'spawn_failed' }); });
      });
      // Output is drained (a full pipe would stall the run) and counted, never kept or forwarded: the cockpit shows Kriya's durable records only.
      child.stdout?.on('data', (chunk) => { this.stdoutBytes += chunk.length; });
      child.stderr?.on('data', (chunk) => { this.stderrBytes += chunk.length; });
      child.stdin?.on('error', () => undefined); // an early exit closes the pipe; the exit event reports it
      child.stdin?.write(goal);
      child.stdin?.end();
      this.current = { ...this.current, phase: 'running' };
      this.deps.log?.(`execution ${launchId} started (pid ${child.pid ?? 'unknown'})`);
      return { started: true, launch_id: launchId, refusal: null };
    } finally {
      this.reserving = false;
    }
  }

  /** Quit-time handling: the terminal's own cancellation (SIGINT, which Kriya turns into a sealed failure record), then SIGKILL after the grace period. */
  async shutdown(): Promise<void> {
    const child = this.child;
    if (!child) return;
    this.current = { ...this.current, diagnostic: 'terminated_by_the_host' };
    child.kill('SIGINT');
    const grace = this.deps.graceMs ?? 8000;
    let timer: NodeJS.Timeout | undefined;
    const timedOut = await Promise.race([
      this.exited.then(() => false),
      new Promise<boolean>((resolve) => { timer = setTimeout(() => resolve(true), grace); }),
    ]);
    if (timer) clearTimeout(timer);
    if (timedOut && this.child === child) {
      child.kill('SIGKILL');
      await this.exited;
    }
  }

  /** Byte counts only (measurement / developer log). */
  outputBytes(): { stdout: number; stderr: number } { return { stdout: this.stdoutBytes, stderr: this.stderrBytes }; }
}
