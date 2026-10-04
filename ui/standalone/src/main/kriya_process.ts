/**
 * Spawns the selected Kriya executable (P-31, P-32): argument arrays, no shell, stdin closed, a minimal
 * environment, a 60 s timeout, 8 MiB stdout and 1 MiB stderr limits. The ONLY process-spawning module.
 * Returns a typed outcome; never partial data.
 */
import { spawn } from 'node:child_process';
import { argvIsAllowed } from './kriya_argv';

export const PROCESS_LIMITS = { timeoutMs: 60_000, stdoutMaxBytes: 8 * 1024 * 1024, stderrMaxBytes: 1024 * 1024 } as const;

export interface RunOutcome {
  kind: 'json' | 'error';
  json?: unknown;
  code?: 'RESPONSE_TOO_LARGE' | 'INVALID_RESPONSE' | 'HOST_ERROR';
  message?: string;
  exitCode: number | null;
  signal: string | null;
  stderrTail: string;
  durationMs: number;
  stdoutBytes: number;
}

export interface SpawnOptions {
  executable: string;
  argv: readonly string[];
  cwd?: string;
  env?: NodeJS.ProcessEnv; // ONLY what the caller passes; never ambient process.env by default
  limits?: Partial<typeof PROCESS_LIMITS>;
  /** Used for the fixture kriya: run a script with Electron's own binary as Node. */
  nodeScript?: boolean;
}

export function runKriya(opts: SpawnOptions): Promise<RunOutcome> {
  const limits = { ...PROCESS_LIMITS, ...opts.limits };
  if (!argvIsAllowed(opts.nodeScript ? opts.argv.slice(1) : opts.argv)) {
    return Promise.resolve({ kind: 'error', code: 'HOST_ERROR', message: 'argv outside the allowlisted grammar', exitCode: null, signal: null, stderrTail: '', durationMs: 0, stdoutBytes: 0 });
  }
  const started = Date.now();
  return new Promise((resolve) => {
    const env: NodeJS.ProcessEnv = { PATH: '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin', ...(opts.env ?? {}) };
    if (opts.nodeScript) env.ELECTRON_RUN_AS_NODE = '1';
    const child = spawn(opts.executable, [...opts.argv], { stdio: ['ignore', 'pipe', 'pipe'], shell: false, windowsHide: true, cwd: opts.cwd, env });
    const out: Buffer[] = []; let outBytes = 0; let errBytes = 0; const err: Buffer[] = [];
    let settled = false; let tooLarge = false; let timedOut = false;
    const finish = (o: RunOutcome) => { if (!settled) { settled = true; clearTimeout(timer); resolve(o); } };
    const timer = setTimeout(() => { timedOut = true; child.kill('SIGKILL'); }, limits.timeoutMs);
    child.stdout.on('data', (chunk: Buffer) => {
      outBytes += chunk.length;
      if (outBytes > limits.stdoutMaxBytes) { tooLarge = true; child.kill('SIGKILL'); return; }
      out.push(chunk);
    });
    child.stderr.on('data', (chunk: Buffer) => { errBytes += chunk.length; if (errBytes <= limits.stderrMaxBytes) err.push(chunk); });
    child.on('error', (e) => finish({ kind: 'error', code: 'HOST_ERROR', message: `spawn failed: ${e.message}`, exitCode: null, signal: null, stderrTail: '', durationMs: Date.now() - started, stdoutBytes: outBytes }));
    child.on('close', (exitCode, signal) => {
      const stderrTail = Buffer.concat(err).toString('utf8').slice(-4000);
      const base = { exitCode, signal: signal ?? null, stderrTail, durationMs: Date.now() - started, stdoutBytes: outBytes };
      if (tooLarge) return finish({ kind: 'error', code: 'RESPONSE_TOO_LARGE', message: `stdout exceeded ${limits.stdoutMaxBytes} bytes`, ...base });
      if (timedOut) return finish({ kind: 'error', code: 'HOST_ERROR', message: `kriya did not answer within ${limits.timeoutMs} ms`, ...base });
      const text = Buffer.concat(out).toString('utf8');
      try {
        return finish({ kind: 'json', json: JSON.parse(text), ...base });
      } catch {
        return finish({ kind: 'error', code: 'INVALID_RESPONSE', message: `stdout was not JSON (exit ${exitCode ?? 'null'})`, ...base });
      }
    });
  });
}
