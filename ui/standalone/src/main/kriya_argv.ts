/**
 * The ONLY place a kriya command line is built (P-25 grammar, P-31). Pure: no Electron, no child_process.
 * Argument ARRAYS, never shell text; every value validated by ipc_contract first.
 */
import { LIMITS, type KupRequest } from './ipc_contract';

export function buildKriyaArgv(request: KupRequest): string[] {
  switch (request.operation) {
    case 'capabilities':
      return ['traces', '--capabilities', '--json'];
    case 'history.list': {
      const limit = request.limit ?? LIMITS.listDefault;
      if (!Number.isInteger(limit) || limit < 1 || limit > LIMITS.listMax) throw new Error('limit outside the grammar');
      const argv = ['traces', '--json', '-n', String(limit)];
      if (request.cursor !== undefined) argv.push('--cursor', request.cursor);
      return argv;
    }
    case 'history.detail':
      return ['traces', '--json', '--run-id', request.run_id];
    case 'history.prompt':
      return ['traces', '--json', '--run-id', request.run_id, '--include-prompt'];
    case 'workspace.status':
      return ['runs', 'status', '--workspace', request.workspace, '--json', '--kup-version', '1'];
    default: {
      const never: never = request;
      throw new Error(`operation outside the grammar: ${JSON.stringify(never)}`);
    }
  }
}

/** Every argv the shell may ever produce starts with one of these prefixes; the test pins it. */
export const ALLOWED_ARGV_PREFIXES: readonly (readonly string[])[] = [
  ['traces', '--capabilities', '--json'],
  ['traces', '--json', '-n'],
  ['traces', '--json', '--run-id'],
  ['runs', 'status', '--workspace'],
];

export function argvIsAllowed(argv: readonly string[]): boolean {
  return ALLOWED_ARGV_PREFIXES.some((p) => p.every((tok, i) => argv[i] === tok));
}
