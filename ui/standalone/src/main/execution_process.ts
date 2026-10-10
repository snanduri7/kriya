/**
 * The one place an EXECUTION process is spawned (GUI M6): `child_process.spawn` with the validated executable, the argv built by
 * execution_host.ts::executionArgv, the opened workspace as the working directory (configuration discovery is the process working directory - the
 * same as running `kriya generate` there), the host's fixed child environment (child_env.ts) and NEVER a shell. Kept apart from kriya_process.ts and
 * kriya_service.ts, which serve reads: a long run must not share a process, a pipe or a queue with navigation.
 */
import { spawn } from 'node:child_process';
import type { ExecChild, Invocation } from './execution_host';

export function launchExecution(invocation: Invocation): ExecChild {
  return spawn(invocation.executable, [...invocation.argv], { cwd: invocation.cwd, env: { ...invocation.env }, shell: false, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true });
}
