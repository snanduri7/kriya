/**
 * The user-facing phase of the run this window started: the smallest truthful set of states, derived ONLY from what the cockpit already reads from Kriya
 * (the launch record, the pending review, the derived outcome and the tallies of the verified evidence records). Nothing is predicted and nothing is counted
 * that was not recorded: no percentage, no "step n of m", no test count while a run executes. If the records do not say, the state is the coarse RUNNING.
 *
 * Deliberately absent: an "understanding the repository" state. No recorded kind distinguishes repository analysis from the start of planning, so it is not drawn.
 */
import type { RunProgress, RunView } from './execution';

export type PhaseState = 'STARTING' | 'PLANNING' | 'EDITING' | 'VALIDATING' | 'REPAIRING' | 'WAITING_FOR_APPROVAL' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'STOPPED';
export type PhaseTone = 'info' | 'warn' | 'ok' | 'bad';
export interface PhaseStep { id: 'planning' | 'editing' | 'validating' | 'finished'; label: string; status: 'done' | 'current' | 'pending' }
export interface RunPhase {
  state: PhaseState;
  /** A few plain words: the headline of the current-run surface and the banner. */
  label: string;
  /** One sentence that only restates what was recorded. */
  detail: string;
  /** The attempt number of the newest record that carries one (null when none). */
  attempt: number | null;
  tone: PhaseTone;
  /** Kriya cannot continue until the person decides. */
  needsAction: boolean;
  /** The outcome is durable. */
  terminal: boolean;
  /** The coarse checklist, or null when the records do not support one (a failed run shows only the last stage reached). */
  steps: PhaseStep[] | null;
  /** For a run that ended without success: the last stage the records show it reached. */
  lastStage: string | null;
}

const STEP_LABEL = { planning: 'Planning', editing: 'Editing', validating: 'Validating', finished: 'Finished' } as const;
const STEP_ORDER = ['planning', 'editing', 'validating', 'finished'] as const;
/** The roles whose model calls are Kriya checking a candidate rather than producing one. */
const CHECKING_ROLES = new Set(['reviewer', 'run_verifier', 'spec_compliance']);
const GATE_KINDS = new Set(['gate.result', 'mirror.gate_outcome']);
const STOPPED_OUTCOMES = new Set(['rejected', 'review_expired', 'approval_required']);

const count = (p: RunProgress, kind: string): number => p.kinds[kind] ?? 0;

/** How far the recorded evidence shows the run got: 0 nothing read, 1 planning, 2 an attempt opened, 3 a gate outcome recorded for an attempt. */
export function stageReached(p: RunProgress): 0 | 1 | 2 | 3 {
  if (count(p, 'mirror.gate_outcome') > 0 && count(p, 'attempt.opened') > 0) return 3;
  if (count(p, 'attempt.opened') > 0) return 2;
  return p.lastSeq > 0 ? 1 : 0;
}

function steps(rank: number): PhaseStep[] {
  return STEP_ORDER.map((id, i) => ({ id, label: STEP_LABEL[id], status: rank > i + 1 ? 'done' : rank === i + 1 ? 'current' : 'pending' }));
}

/** The phase of one run. Pure: the tests drive it with the recorded sequence of a real run, prefix by prefix. */
export function runPhase(run: RunView): RunPhase {
  const p = run.progress;
  const latest = p.recent[p.recent.length - 1] ?? null;
  const attempt = [...p.recent].reverse().find((r) => r.attempt !== null)?.attempt ?? null;
  const base = { attempt, needsAction: false, terminal: false, lastStage: null } as const;

  if (run.stage === 'finished' && run.outcome) {
    const kind = run.outcome.kind;
    const reached = stageReached(p);
    const lastStage = reached === 0 ? null : (['Planning', 'Editing', 'Validating'] as const)[reached - 1] ?? null;
    if (kind === 'success') return { ...base, state: 'COMPLETED', label: 'Completed', detail: run.outcome.detail, tone: 'ok', terminal: true, steps: steps(5), lastStage: null };
    if (STOPPED_OUTCOMES.has(kind)) return { ...base, state: 'STOPPED', label: 'Stopped', detail: run.outcome.detail, tone: 'warn', terminal: true, steps: null, lastStage };
    return { ...base, state: 'FAILED', label: 'Failed', detail: run.outcome.detail, tone: 'bad', terminal: true, steps: null, lastStage };
  }
  if (run.review) {
    return { ...base, state: 'WAITING_FOR_APPROVAL', label: 'Waiting for your approval', detail: 'Kriya paused the run and applies nothing until you decide.', tone: 'warn', needsAction: true, steps: steps(3.5) };
  }
  if (run.runtimeReview) {
    return { ...base, state: 'WAITING_FOR_APPROVAL', label: 'Waiting for permission to run commands', detail: 'Kriya paused the run and runs nothing until you decide.', tone: 'warn', needsAction: true, steps: steps(3.5) };
  }
  const pendingApproval = run.status?.launch.pending_approval;
  const approved = pendingApproval?.status === 'APPROVED' && run.status?.launch.phase === 'RUNNING';
  if (approved && pendingApproval?.kind === 'runtime_command') {
    return { ...base, state: 'RUNNING', label: 'Verifying your application', detail: 'You allowed the reviewed commands. Kriya is running them in its isolated worktree and checking the result.', tone: 'info', steps: steps(3) };
  }
  if (approved) {
    return { ...base, state: 'VALIDATING', label: 'Finishing', detail: 'Your change was approved. Kriya is running final checks.', tone: 'info', steps: steps(3) };
  }
  if (run.stage === 'starting' || p.lastSeq === 0) {
    return { ...base, state: 'STARTING', label: 'Starting', detail: 'Starting the run…', tone: 'info', steps: steps(0) };
  }
  if (count(p, 'unit.opened') === 0) {
    return { ...base, state: 'PLANNING', label: 'Planning', detail: 'Kriya is planning the change before any file is edited.', tone: 'info', steps: steps(1) };
  }
  const open = count(p, 'attempt.opened') > count(p, 'attempt.closed');
  if (count(p, 'attempt.opened') === 0) {
    return { ...base, state: 'PLANNING', label: 'Planning', detail: 'The plan is accepted; Kriya is preparing the first attempt.', tone: 'info', steps: steps(1) };
  }
  if (open && latest) {
    const checking = GATE_KINDS.has(latest.kind) || (latest.role !== null && CHECKING_ROLES.has(latest.role));
    const n = attempt ?? 1;
    if (checking) return { ...base, state: 'VALIDATING', label: 'Validating', detail: `Attempt ${n}: Kriya is checking the candidate change.`, tone: 'info', steps: steps(3) };
    if (n >= 2) return { ...base, state: 'REPAIRING', label: 'Repairing', detail: `Attempt ${n}: Kriya is making another attempt after an earlier one did not complete the unit.`, tone: 'info', steps: steps(2) };
    return { ...base, state: 'EDITING', label: 'Editing', detail: `Attempt ${n}: Kriya is producing a candidate change.`, tone: 'info', steps: steps(2) };
  }
  // Running, but the records do not place it in one stage (between attempts, or after the last attempt closed): say only that it runs.
  const reached = stageReached(p);
  return { ...base, state: 'RUNNING', label: 'Running', detail: 'Kriya is working; the records do not place it in a single stage right now.', tone: 'info', steps: steps(reached) };
}
