/**
 * The persistent local read transport (GUI M5; resolves GUI-KRIYA-PROCESS-STARTUP-LATENCY-001): ONE long-lived `kriya kup-stdio` child per
 * application, spoken to over newline-delimited JSON on its stdin/stdout. It is the same Kriya operation implementation the one-shot commands
 * (kriya_process.ts) reach - this module only keeps the process warm. No listener, no socket, no network.
 *
 * What it does NOT do: it never parses a Kriya store, never caches an answer, never decides authority (content visibility is Kriya's, evaluated
 * by the child per request), and never dispatches anything outside the typed KupRequest table. Failure is typed, never partial: a malformed or
 * refused request is answered for that request alone; a child that dies, hangs or breaks the protocol fails every request in flight with HOST_ERROR
 * and is restarted by the NEXT request (controlled restart; there is no retry of a failed request here).
 *
 * Workspace switching (decision, 2026-10-10): the workspace is a per-request typed ARGUMENT (`workspace`), never process state. The child holds
 * nothing that belongs to a workspace, so switching A -> B needs no restart and nothing can leak from A into B. The child's working directory is
 * the validated configuration directory, exactly as for the one-shot path; a change of executable, configuration directory or environment starts a
 * new child (`KriyaServiceHolder`).
 */
import { spawn, type ChildProcess } from 'node:child_process';
import { KRIYA_CHILD_PATH } from './child_env';
import type { KupRequest } from './ipc_contract';

export const SERVICE_PROTOCOL = 'kriya-kup-stdio/1';
export const SERVICE_CAPABILITY_SET = '1.1';
export const SERVICE_ARGV: readonly string[] = ['kup-stdio'];

export interface ServiceLimits { requestTimeoutMs: number; readyTimeoutMs: number; lineMaxBytes: number; requestMaxBytes: number; stderrTailBytes: number; stopGraceMs: number }
export const SERVICE_LIMITS: ServiceLimits = {
  /** Per request, from the moment it is written. */
  requestTimeoutMs: 60_000,
  readyTimeoutMs: 30_000,
  /** One response line: the 8 MiB envelope cap plus the wrapper. A longer line is a protocol violation. */
  lineMaxBytes: 9 * 1024 * 1024,
  requestMaxBytes: 64 * 1024,
  stderrTailBytes: 4000,
  stopGraceMs: 3000,
};

/** The arguments each operation may carry to the child - mirrors kriya/kup/serve.py::ARGUMENTS (a cross-language test pins the equality). */
export const SERVICE_ARGUMENTS: Readonly<Record<KupRequest['operation'], readonly string[]>> = Object.freeze({
  capabilities: [],
  'snapshot.acquire': ['workspace'],
  'snapshot.list': ['verify'],
  'snapshot.prune': ['keep'],
  'snapshot.verify': ['snapshot_id'],
  'history.list': ['snapshot_id', 'limit', 'cursor', 'workspace'],
  'history.detail': ['snapshot_id', 'run_id'],
  'history.prompt': ['snapshot_id', 'run_id'],
  'workspace.status': ['workspace'],
  'workspace.describe': ['workspace'],
  'evidence.manifest': ['run_id', 'snapshot_id', 'from_trace'],
  'evidence.verify': ['run_id'],
  'evidence.records': ['run_id', 'unit', 'phase', 'role', 'attempt', 'from_seq', 'kind', 'limit', 'cursor'],
  'evidence.change_content': ['run_id', 'seq', 'part'],
  'evidence.call_content': ['run_id', 'seq', 'part'],
});

export interface ServiceRequestLine { id: string; schema_version: 1; capability_set: string; operation: string; arguments: Record<string, unknown> }

/** The wire request for an already-validated KupRequest. Throws for an operation outside the table or an argument the table does not list. */
export function toServiceRequest(id: string, request: KupRequest): ServiceRequestLine {
  const allowed = SERVICE_ARGUMENTS[request.operation];
  if (!allowed) throw new Error(`operation outside the service allowlist: ${String((request as { operation?: unknown }).operation)}`);
  const { operation, ...rest } = request as { operation: string } & Record<string, unknown>;
  const extra = Object.keys(rest).filter((k) => !allowed.includes(k));
  if (extra.length) throw new Error(`${operation} does not take: ${extra.join(', ')}`);
  return { id, schema_version: 1, capability_set: SERVICE_CAPABILITY_SET, operation, arguments: rest };
}

export type ServiceOutcome =
  | { kind: 'json'; json: unknown; durationMs: number }
  | { kind: 'error'; code: string; message: string; stderrTail: string; durationMs: number };

export interface ServiceOptions {
  executable: string;
  argv?: readonly string[];
  cwd?: string;
  /** ONLY what the caller passes (child_env.ts), plus the fixed PATH; never the ambient environment. */
  env?: NodeJS.ProcessEnv;
  limits?: Partial<ServiceLimits>;
}

interface Pending { operation: string; started: number; resolve(o: ServiceOutcome): void; timer: NodeJS.Timeout }

export class KriyaService {
  private child: ChildProcess | null = null;
  private starting: Promise<ServiceOutcome | null> | null = null;
  private pending = new Map<string, Pending>();
  private sequence = 0;
  private stderrChunks: Buffer[] = [];
  private stderrBytes = 0;
  private stopped = false;
  /** Counters for tests and diagnostics: how many children were started and how many ended while the service still wanted them. */
  readonly stats = { starts: 0, deaths: 0, requests: 0, lastReadyMs: null as number | null };
  private readonly limits: ServiceLimits;

  constructor(private readonly options: ServiceOptions) {
    this.limits = { ...SERVICE_LIMITS, ...options.limits };
  }

  get pid(): number | null { return this.child?.pid ?? null; }
  get alive(): boolean { return this.child !== null && this.child.exitCode === null && this.child.signalCode === null; }

  async request(request: KupRequest): Promise<ServiceOutcome> {
    const started = Date.now();
    const fail = (code: string, message: string): ServiceOutcome => ({ kind: 'error', code, message, stderrTail: this.tail(), durationMs: Date.now() - started });
    if (this.stopped) return fail('HOST_ERROR', 'the kriya service has been stopped');
    const id = `q${++this.sequence}`;
    let line: string;
    try {
      line = JSON.stringify(toServiceRequest(id, request));
    } catch (error) {
      return fail('HOST_ERROR', error instanceof Error ? error.message : String(error));
    }
    if (Buffer.byteLength(line) > this.limits.requestMaxBytes) return fail('INVALID_REQUEST', `the request exceeds ${this.limits.requestMaxBytes} bytes`);
    const startup = await this.ensureReady();
    if (startup) return startup;
    const child = this.child;
    if (!child?.stdin || !this.alive) return fail('HOST_ERROR', 'the kriya service is not running');
    this.stats.requests += 1;
    return new Promise<ServiceOutcome>((resolve) => {
      const timer = setTimeout(() => {
        // A running operation cannot be cancelled: the child is ended, so nothing it was doing can answer a later request out of turn.
        this.complete(id, fail('HOST_ERROR', `kriya did not answer ${request.operation} within ${this.limits.requestTimeoutMs} ms; the service was restarted`));
        child.kill('SIGKILL');
      }, this.limits.requestTimeoutMs);
      this.pending.set(id, { operation: request.operation, started, resolve, timer });
      child.stdin!.write(`${line}\n`, (error) => { if (error) this.complete(id, fail('HOST_ERROR', `could not write to the kriya service: ${error.message}`)); });
    });
  }

  /** Ends the child cleanly (EOF on stdin ends `kup-stdio`), then forcibly after a grace period. No request is accepted afterwards. */
  async stop(): Promise<void> {
    this.stopped = true;
    const child = this.child;
    if (!child) return;
    await new Promise<void>((resolve) => {
      if (child.exitCode !== null || child.signalCode !== null) return resolve();
      const force = setTimeout(() => child.kill('SIGKILL'), this.limits.stopGraceMs);
      child.once('exit', () => { clearTimeout(force); resolve(); });
      child.stdin?.end();
    });
  }

  private tail(): string { return Buffer.concat(this.stderrChunks).toString('utf8').slice(-this.limits.stderrTailBytes); }

  private complete(id: string, outcome: ServiceOutcome): void {
    const entry = this.pending.get(id);
    if (!entry) return;
    clearTimeout(entry.timer);
    this.pending.delete(id);
    entry.resolve(outcome);
  }

  private failAll(code: string, message: string): void {
    for (const [id, entry] of [...this.pending]) this.complete(id, { kind: 'error', code, message, stderrTail: this.tail(), durationMs: Date.now() - entry.started });
  }

  /** null = a ready child exists; otherwise the typed failure to report for the request that needed it. */
  private ensureReady(): Promise<ServiceOutcome | null> {
    if (this.alive && this.child && !this.starting) return Promise.resolve(null);
    if (!this.starting) this.starting = this.start().finally(() => { this.starting = null; });
    return this.starting;
  }

  private start(): Promise<ServiceOutcome | null> {
    const started = Date.now();
    const failure = (message: string): ServiceOutcome => ({ kind: 'error', code: 'HOST_ERROR', message, stderrTail: this.tail(), durationMs: Date.now() - started });
    return new Promise((resolve) => {
      this.stderrChunks = []; this.stderrBytes = 0;
      this.stats.starts += 1;
      let child: ChildProcess;
      try {
        child = spawn(this.options.executable, [...(this.options.argv ?? SERVICE_ARGV)], {
          stdio: ['pipe', 'pipe', 'pipe'], shell: false, windowsHide: true, cwd: this.options.cwd, env: { PATH: KRIYA_CHILD_PATH, ...(this.options.env ?? {}) },
        });
      } catch (error) {
        return resolve(failure(`spawn failed: ${error instanceof Error ? error.message : String(error)}`));
      }
      this.child = child;
      let ready = false;
      let settled = false;
      let broken = false; // after a violation the stream cannot be trusted: nothing more from this child is read
      let buffer: Buffer = Buffer.alloc(0);
      const settle = (outcome: ServiceOutcome | null) => { if (!settled) { settled = true; clearTimeout(readyTimer); resolve(outcome); } };
      const readyTimer = setTimeout(() => { settle(failure(`the kriya service did not become ready within ${this.limits.readyTimeoutMs} ms`)); child.kill('SIGKILL'); }, this.limits.readyTimeoutMs);

      const violation = (message: string) => {
        // A broken protocol cannot be resynchronized: end the child; the next request starts a clean one.
        broken = true;
        settle(failure(message));
        this.failAll('HOST_ERROR', message);
        child.kill('SIGKILL');
      };
      const onLine = (raw: Buffer) => {
        const text = raw.toString('utf8');
        let message: Record<string, unknown>;
        try {
          const parsed = JSON.parse(text);
          if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('not an object');
          message = parsed as Record<string, unknown>;
        } catch {
          return violation('the kriya service wrote a line that is not a JSON object on its protocol stream');
        }
        if (!ready) {
          if (message.event === 'ready' && message.protocol === SERVICE_PROTOCOL && message.schema_version === 1) { ready = true; this.stats.lastReadyMs = Date.now() - started; return settle(null); }
          return violation(`the kriya service did not announce ${SERVICE_PROTOCOL} (first line: ${text.slice(0, 120)})`);
        }
        const id = message.id;
        if (typeof id !== 'string' || !this.pending.has(id)) return violation('the kriya service answered a request that is not in flight');
        const entry = this.pending.get(id)!;
        const durationMs = Date.now() - entry.started;
        if (message.ok === true && typeof message.envelope === 'object' && message.envelope !== null) return this.complete(id, { kind: 'json', json: message.envelope, durationMs });
        const error = message.ok === false && typeof message.error === 'object' && message.error !== null ? message.error as Record<string, unknown> : null;
        if (error && typeof error.code === 'string' && typeof error.message === 'string') return this.complete(id, { kind: 'error', code: error.code, message: error.message, stderrTail: this.tail(), durationMs });
        return violation('the kriya service answered with neither an envelope nor a typed error');
      };

      const tooLong = () => violation(`the kriya service wrote a protocol line over ${this.limits.lineMaxBytes} bytes`);
      child.stdout!.on('data', (chunk: Buffer) => {
        if (broken) return;
        buffer = buffer.length ? Buffer.concat([buffer, chunk]) : chunk;
        for (let at = buffer.indexOf(10); at !== -1 && !broken; at = buffer.indexOf(10)) {
          const line = buffer.subarray(0, at);
          buffer = buffer.subarray(at + 1);
          if (line.length > this.limits.lineMaxBytes) return tooLong();
          if (line.length) onLine(line);
        }
        if (!broken && buffer.length > this.limits.lineMaxBytes) tooLong();
      });
      child.stderr!.on('data', (chunk: Buffer) => { this.stderrBytes += chunk.length; if (this.stderrBytes <= 1024 * 1024) this.stderrChunks.push(chunk); });
      child.stdin!.on('error', () => { /* a write after the child ended is reported through the pending request and the exit handler */ });
      child.on('error', (error) => {
        settle(failure(`spawn failed: ${error.message}`));
        this.failAll('HOST_ERROR', `the kriya service failed: ${error.message}`);
      });
      child.on('exit', (code, signal) => {
        if (this.child === child) this.child = null;
        if (!this.stopped) this.stats.deaths += 1;
        const why = `the kriya service exited (${signal ? `signal ${signal}` : `code ${code ?? 'null'}`})${this.stopped ? '' : ' unexpectedly'}`;
        settle(failure(`${why} before it was ready`));
        this.failAll('HOST_ERROR', why);
      });
    });
  }
}

/** Keeps exactly one service per (executable, working directory, environment): a different identity ends the old child and starts a new one. */
export class KriyaServiceHolder {
  private current: { key: string; service: KriyaService } | null = null;
  private retired: Promise<void>[] = [];

  get(options: ServiceOptions): KriyaService {
    const key = JSON.stringify([options.executable, options.cwd ?? null, Object.entries(options.env ?? {}).sort()]);
    if (this.current?.key === key) return this.current.service;
    if (this.current) this.retired.push(this.current.service.stop());
    this.current = { key, service: new KriyaService(options) };
    return this.current.service;
  }

  async stop(): Promise<void> {
    const current = this.current;
    this.current = null;
    await Promise.all([...this.retired, current?.service.stop()]);
    this.retired = [];
  }
}
