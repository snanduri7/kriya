/**
 * The one place an EXECUTION process is spawned (GUI M6): `child_process.spawn` with the validated executable, the argv built by
 * execution_host.ts::executionArgv, the opened workspace as the working directory (configuration discovery is the process working directory - the
 * same as running `kriya generate` there), the host's fixed child environment (child_env.ts) and NEVER a shell. Kept apart from kriya_process.ts and
 * kriya_service.ts, which serve reads: a long run must not share a process, a pipe or a queue with navigation.
 *
 * D2 M2: the fourth stdio entry is the PRIVATE REVIEW CHANNEL - a bidirectional socket the child finds at fd 3 and the host reaches as `child.stdio[3]`
 * (review_channel.ts). It is the only descriptor beyond stdin/stdout/stderr this host ever hands a Kriya process, and it exists for execution processes only.
 */
import { spawn } from 'node:child_process';
import type { ExecChild, Invocation } from './execution_host';

export function launchExecution(invocation: Invocation): ExecChild {
  return spawn(invocation.executable, [...invocation.argv], { cwd: invocation.cwd, env: { ...invocation.env }, shell: false, stdio: ['pipe', 'pipe', 'pipe', 'pipe'], windowsHide: true });
}
