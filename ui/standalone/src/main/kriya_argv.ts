/**
 * The ONLY place a kriya command line is built (P-25 grammar, P-31). Pure: no Electron, no child_process.
 * Argument ARRAYS, never shell text; every value validated by ipc_contract first.
 */
import { CANDIDATE_DIGEST_RE, LAUNCH_ID_RE, LIMITS, REVIEW_ID_RE, type DecideApprovalRequest, type KupRequest } from './ipc_contract';

function evidenceHead(subcommand: 'manifest' | 'verify' | 'records' | 'change-content' | 'call-content', runId: string): string[] {
  return ['evidence', subcommand, runId, '--json', '--kup-version', '1'];
}

export function buildKriyaArgv(request: KupRequest): string[] {
  switch (request.operation) {
    case 'capabilities':
      return ['traces', '--json', '--capabilities'];
    case 'snapshot.acquire':
      return request.workspace !== undefined ? ['traces', '--json', '--snapshot', '--workspace', request.workspace] : ['traces', '--json', '--snapshot'];
    case 'snapshot.list':
      return request.verify ? ['traces', '--json', '--snapshots', '--verify'] : ['traces', '--json', '--snapshots'];
    case 'snapshot.prune':
      return request.keep !== undefined ? ['traces', '--json', '--snapshot-prune', '--keep', String(request.keep)] : ['traces', '--json', '--snapshot-prune'];
    case 'snapshot.verify':
      return ['traces', '--json', '--snapshot-verify', request.snapshot_id];
    case 'history.list': {
      const limit = request.limit ?? LIMITS.listDefault;
      if (!Number.isInteger(limit) || limit < 1 || limit > LIMITS.listMax) throw new Error('limit outside the grammar');
      const argv = ['traces', '--json', '--snapshot-id', request.snapshot_id, '-n', String(limit)];
      if (request.cursor !== undefined) argv.push('--cursor', request.cursor);
      if (request.workspace !== undefined) argv.push('--workspace', request.workspace);
      return argv;
    }
    case 'history.detail':
      return ['traces', '--json', '--snapshot-id', request.snapshot_id, '--run-id', request.run_id];
    case 'history.prompt':
      return ['traces', '--json', '--snapshot-id', request.snapshot_id, '--run-id', request.run_id, '--include-prompt'];
    case 'evidence.manifest':
      return [...evidenceHead('manifest', request.run_id), ...(request.snapshot_id !== undefined ? [`--snapshot-id=${request.snapshot_id}`] : []), ...(request.from_trace === true ? ['--from-trace'] : [])];
    case 'evidence.verify':
      return evidenceHead('verify', request.run_id);
    case 'evidence.change_content':
      return [...evidenceHead('change-content', request.run_id), `--seq=${request.seq}`, `--part=${request.part}`];
    case 'evidence.call_content':
      return [...evidenceHead('call-content', request.run_id), `--seq=${request.seq}`, `--part=${request.part}`];
    case 'evidence.records': {
      // Every value is attached with `=`: a value that starts with `-` (a unit id, a cursor) can never be read as an option.
      const argv = evidenceHead('records', request.run_id);
      for (const [flag, value] of [['unit', request.unit], ['phase', request.phase], ['role', request.role], ['attempt', request.attempt], ['from-seq', request.from_seq], ['limit', request.limit], ['cursor', request.cursor]] as const) {
        if (value !== undefined) argv.push(`--${flag}=${String(value)}`);
      }
      for (const kind of request.kind ?? []) argv.push(`--kind=${kind}`);
      return argv;
    }
    case 'workspace.status':
      return ['runs', 'status', '--workspace', request.workspace, '--json', '--kup-version', '1'];
    case 'workspace.describe':
      return ['traces', '--json', '--describe-workspace', request.workspace];
    case 'execution.preflight':
      return ['traces', '--json', '--execution-preflight', request.workspace];
    case 'launch.status':
      return ['traces', '--json', '--launch-status', request.launch_id, '--workspace', request.workspace];
    case 'workspace.create':
    case 'configuration.approval_status':
    case 'configuration.approve':
    case 'change_approval.review':
    case 'execution.context':
    case 'execution.model_probe':
    case 'skills.list':
    case 'skills.describe':
    case 'tools.list':
    case 'tools.describe':
    case 'tools.approval_state':
      // Served by the persistent Kriya service only (the one-shot grammar has no flag for them, by design).
      throw new Error(`${request.operation} is served only by the persistent Kriya service`);
    default: {
      const never: never = request;
      throw new Error(`operation outside the grammar: ${JSON.stringify(never)}`);
    }
  }
}

/** The review decision command line, exactly one shape (the workspace is the working directory, never an argument):
 *     launch-decision --launch-id <id> --review-id <id> --candidate-digest <sha256:...> --decision approve|reject */
export function buildLaunchDecisionArgv(request: DecideApprovalRequest): string[] {
  const argv = ['launch-decision', '--launch-id', request.launch_id, '--review-id', request.review_id, '--candidate-digest', request.candidate_digest, '--decision', request.decision];
  if (!isLaunchDecisionArgv(argv)) throw new Error('launch-decision argv outside the grammar');
  return argv;
}

export function isLaunchDecisionArgv(argv: readonly string[]): boolean {
  return argv.length === 9 && argv[0] === 'launch-decision' && argv[1] === '--launch-id' && LAUNCH_ID_RE.test(argv[2] ?? '') && argv[3] === '--review-id' && REVIEW_ID_RE.test(argv[4] ?? '')
    && argv[5] === '--candidate-digest' && CANDIDATE_DIGEST_RE.test(argv[6] ?? '') && argv[7] === '--decision' && (argv[8] === 'approve' || argv[8] === 'reject');
}

/** Every argv the shell may ever produce starts with one of these prefixes; the test pins it. */
export const ALLOWED_ARGV_PREFIXES: readonly (readonly string[])[] = [
  ['traces', '--json', '--capabilities'],
  ['traces', '--json', '--snapshot'],
  ['traces', '--json', '--snapshots'],
  ['traces', '--json', '--snapshot-prune'],
  ['traces', '--json', '--snapshot-verify'],
  ['traces', '--json', '--snapshot-id'],
  ['traces', '--json', '--describe-workspace'],
  ['traces', '--json', '--execution-preflight'],
  ['traces', '--json', '--launch-status'],
  ['runs', 'status', '--workspace'],
];

const EVIDENCE_SUBCOMMANDS: readonly string[] = ['manifest', 'verify', 'records', 'change-content', 'call-content'];

/** `evidence <read-only KUP subcommand> <run id> --json --kup-version 1 ...`: the subcommand, the KUP flags and their position are pinned. */
function isEvidenceArgv(argv: readonly string[]): boolean {
  return argv[0] === 'evidence' && EVIDENCE_SUBCOMMANDS.includes(argv[1] ?? '') && typeof argv[2] === 'string' && argv[2].length > 0 && !argv[2].startsWith('-')
    && argv[3] === '--json' && argv[4] === '--kup-version' && argv[5] === '1';
}

export function argvIsAllowed(argv: readonly string[]): boolean {
  return isEvidenceArgv(argv) || isLaunchDecisionArgv(argv) || ALLOWED_ARGV_PREFIXES.some((p) => p.every((tok, i) => argv[i] === tok));
}
