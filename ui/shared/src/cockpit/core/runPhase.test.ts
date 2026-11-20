/**
 * The user-facing run phase, driven prefix by prefix with the recorded `kind` / `phase` order of a REAL run (scenario A, 2026-10-14, run 20261014T181340-a69de47d:
 * Planner call, one unit, one Developer attempt that passed its gates, Kriya's review call, a change review waiting for the person, then the closing records).
 * Only the order, the recorded phase and the roles are reproduced; no payload is invented. A second attempt is the one thing this run did not have; it is added
 * below as an explicit, labelled variant (a repair), because the mapping must not call attempt 1 a repair.
 */
import { describe, expect, it } from 'vitest';
import type { LaunchStatus } from '../../model/kup';
import { foldRecords, NO_PROGRESS, type RunProgress, type RunView } from './execution';
import { runPhase } from './runPhase';

type Rec = [kind: string, phase: string | null, role: string | null, attempt: number | null];
const P: Rec[] = [
  ['run.opened', null, null, null], ['prompt.sections', null, null, null],
  ['model.request', 'planning', 'planner', null], ['model.response', 'planning', 'planner', null], ['model.result', 'planning', 'planner', null],
  ['mirror.decision', null, null, null], ['mirror.decision', null, null, null], ['mirror.decision', null, null, null],
  ['unit.opened', null, null, null],
  ['mirror.event', null, null, null], ['mirror.evidence', null, null, null], ['gate.result', null, null, null], // the baseline gate, before any attempt
  ['attempt.opened', 'attempt', null, 1],
  ['fallback.decision', 'attempt', null, 1], ['authority.snapshot', 'attempt', null, 1], ['prompt.sections', 'attempt', null, 1],
  ['model.request', 'attempt', 'developer', 1], ['model.response', 'attempt', 'developer', 1], ['model.result', 'attempt', 'developer', 1],
  ['developer.parse', 'attempt', null, 1], ['candidate.change', 'attempt', null, 1],
  ['gate.result', 'attempt', null, 1], ['mirror.gate_outcome', 'attempt', null, 1],
  ['model.request', 'attempt', 'reviewer', 1], ['model.response', 'attempt', 'reviewer', 1], ['model.result', 'attempt', 'reviewer', 1],
  ['mirror.gate_outcome', 'attempt', null, 1], ['attempt.closed', 'attempt', null, 1],
];
const rec = (r: Rec, seq: number): Record<string, unknown> => ({ seq, kind: r[0], phase: r[1], role: r[2], attempt_number: r[3], unit_id: r[0] === 'run.opened' ? null : 's1', t_wall: '2026-10-14T12:44:00Z' });
const progressOf = (records: Rec[]): RunProgress => records.reduce<RunProgress>((acc, r, i) => foldRecords(acc, [rec(r, i + 1)]), NO_PROGRESS);

const status = (phase: 'STARTING' | 'RUNNING' | 'ENDED', approval?: 'PENDING' | 'APPROVED', lifecycle?: string): LaunchStatus => ({
  launch: { phase, pending_approval: approval ? { status: approval } : undefined },
  ...(lifecycle ? { run_record: { found: true, unreadable: null, lifecycle_state: lifecycle, terminal_status: null, commit_result: null, revision: 1 } } : {}),
} as unknown as LaunchStatus);
function run(records: Rec[], extra: Partial<RunView> = {}): RunView {
  return { launchId: 'l1', workspace: '/w', host: null, status: status('RUNNING'), statusError: null, progress: progressOf(records), stage: 'running', outcome: null, review: null, runtimeReview: null, verifierReview: null,
    timing: { clickedAt: 0, acknowledgedAt: 0, recordSeenAt: 0, terminalAt: null, refreshedAt: null }, ...extra };
}
const states = (records: Rec[]) => runPhase(run(records)).state;
const upTo = (kind: string, nth = 1): Rec[] => { let seen = 0; const i = P.findIndex((r) => r[0] === kind && ++seen === nth); return P.slice(0, i + 1); };

describe('run phase from the recorded sequence of a real run', () => {
  it('before any record is read the run is STARTING, whatever the launch record says', () => {
    expect(runPhase(run([], { status: null, stage: 'starting' })).state).toBe('STARTING');
    expect(runPhase(run([])).state).toBe('STARTING'); // RUNNING launch, no evidence record read yet
  });
  it('the Planner call, plan acceptance and the baseline gate are PLANNING: no unit edits anything yet', () => {
    expect(states(upTo('run.opened'))).toBe('PLANNING');
    expect(states(upTo('model.result'))).toBe('PLANNING');
    expect(states(upTo('unit.opened'))).toBe('PLANNING');
    // the two planning moments say different true things: the Planner is working, then the accepted plan is being prepared for attempt 1
    expect(runPhase(run(upTo('model.result'))).detail).toMatch(/planning the change/);
    expect(runPhase(run(upTo('unit.opened'))).detail).toMatch(/plan is accepted/);
    expect(states(upTo('gate.result', 1))).toBe('PLANNING'); // the baseline gate precedes attempt 1: it must not read as VALIDATING
  });
  it('attempt 1 opened and the Developer producing a candidate is EDITING, never REPAIRING', () => {
    for (const k of ['attempt.opened', 'model.request', 'developer.parse', 'candidate.change']) {
      const p = runPhase(run(upTo(k, k === 'model.request' ? 2 : 1)));
      expect(p.state, k).toBe('EDITING');
      expect(p.attempt).toBe(1);
    }
    expect(runPhase(run(upTo('candidate.change'))).detail).toContain('Attempt 1');
  });
  it('gate records and Kriya\'s own review call are VALIDATING: the checks of this unit\'s change, inside Implementing', () => {
    expect(states(upTo('gate.result', 2))).toBe('VALIDATING');
    expect(states(upTo('mirror.gate_outcome'))).toBe('VALIDATING');
    expect(states(upTo('model.request', 3))).toBe('VALIDATING'); // the role is the reviewer
    expect(states(upTo('model.result', 3))).toBe('VALIDATING');
    const checking = runPhase(run(upTo('mirror.gate_outcome')));
    expect(checking.label).toBe('Checking this change');
    expect(checking.steps?.map((s) => `${s.id}:${s.status}`)).toEqual(['planning:done', 'editing:current', 'validating:pending', 'finished:pending']);
  });
  it('between attempts, with no attempt open, the state is the coarse RUNNING instead of a guess', () => {
    const phase = runPhase(run(P));
    expect(phase.state).toBe('RUNNING');
    expect(phase.label).toBe('Running');
  });
  it('a pending review (and only that) is WAITING_FOR_APPROVAL with needsAction', () => {
    const waiting = runPhase(run(P, { review: { review_id: 'r1' } as unknown as RunView['review'], status: status('RUNNING', 'PENDING') }));
    expect(waiting.state).toBe('WAITING_FOR_APPROVAL');
    expect(waiting.needsAction).toBe(true);
    expect(waiting.steps?.map((s) => s.status)).toEqual(['done', 'current', 'pending', 'pending']); // the change under review belongs to Implementing; verification has not started
    // a PENDING record without the live-run conditions (activeReview gave no review) is NOT waiting for the person
    expect(runPhase(run(P, { status: status('RUNNING', 'PENDING') })).state).not.toBe('WAITING_FOR_APPROVAL');
  });
  it('the four steps carry the customer\'s words and a unit\'s runtime-command approval sits inside Implementing', () => {
    const labels = (r: RunView) => runPhase(r).steps?.map((s) => s.label);
    expect(labels(run(P, { status: status('RUNNING', undefined, 'CANDIDATE') }))).toEqual(['Planning', 'Implementing', 'Verifying', 'Complete']);
    expect(runPhase(run(P)).steps).toBeNull(); // between attempts with no lifecycle state read: the records cannot place the run, so no checklist is drawn
    const waiting = runPhase(run(P, { runtimeReview: { review_id: 'r2' } as unknown as RunView['runtimeReview'], status: status('RUNNING', 'PENDING') }));
    expect(waiting.steps?.map((s) => `${s.id}:${s.status}`)).toEqual(['planning:done', 'editing:current', 'validating:pending', 'finished:pending']);
    const running = runPhase(run(P, { status: { ...status('RUNNING', 'APPROVED'), launch: { ...status('RUNNING', 'APPROVED').launch, pending_approval: { ...status('RUNNING', 'APPROVED').launch.pending_approval!, kind: 'runtime_command' } } } as RunView['status'] }));
    expect(running).toMatchObject({ label: 'Verifying your application', verifyingApplication: true });
    expect(running.steps?.map((s) => s.status)).toEqual(['done', 'current', 'pending', 'pending']);
  });
  it('after the person approved a change and while the run is still going: not waiting, and NOT "finishing" - an approval is not a stage', () => {
    const after = runPhase(run([...P, ['verification_contract.compiled', null, null, null]], { status: status('RUNNING', 'APPROVED') }));
    expect(after.state).toBe('RUNNING');
    expect(after.needsAction).toBe(false);
    expect(`${after.label} ${after.detail}`).not.toMatch(/Finishing|final checks|approved/i);
    expect(after.terminalActivity).toBeUndefined();
  });
  it('an attempt numbered 2 or later that is open is REPAIRING; its gates are VALIDATING again', () => {
    const second: Rec[] = [...P, ['attempt.opened', 'attempt', null, 2], ['model.request', 'attempt', 'developer', 2]];
    expect(runPhase(run(second)).state).toBe('REPAIRING');
    expect(runPhase(run(second)).attempt).toBe(2);
    expect(runPhase(run([...second, ['gate.result', 'attempt', null, 2]])).state).toBe('VALIDATING');
  });
  it('the checklist marks a step done only once a later stage was recorded', () => {
    const at = (records: Rec[]) => runPhase(run(records)).steps?.map((s) => `${s.id}:${s.status}`);
    expect(at(upTo('model.result'))).toEqual(['planning:current', 'editing:pending', 'validating:pending', 'finished:pending']);
    expect(at(upTo('candidate.change'))).toEqual(['planning:done', 'editing:current', 'validating:pending', 'finished:pending']);
    expect(at(upTo('mirror.gate_outcome'))).toEqual(['planning:done', 'editing:current', 'validating:pending', 'finished:pending']); // a unit's own gates never advance the global step
  });
});

/**
 * The GLOBAL step follows the run record's lifecycle state across a multi-unit run. The order is the recorded order of Run 9 (launch 20261120T160112-cfcdc56e,
 * 2026-11-20: nine units, each "attempt opened -> Developer -> candidate -> attempt closed -> unit gates -> unit closed" with a per-unit change approval, then the
 * terminal verification, the approval of the complete change, the commit and SUCCESS), shortened to three units. The lifecycle states are the ones
 * kriya/control/run_record.py defines: CANDIDATE during every unit, VERIFYING from just before the terminal gates, COMMIT_ELIGIBLE / COMMITTED for the commit.
 */
describe('the global step follows the run record lifecycle across a multi-unit run (the recorded order of Run 9)', () => {
  const PLAN: Rec[] = P.slice(0, 8);                                              // run opened .. the plan decisions (no unit yet)
  const UNIT_OPEN: Rec[] = [['unit.opened', null, null, null]];
  const IMPLEMENTING: Rec[] = [['attempt.opened', 'attempt', null, 1], ['model.request', 'attempt', 'developer', 1], ['model.response', 'attempt', 'developer', 1],
    ['model.result', 'attempt', 'developer', 1], ['developer.parse', 'attempt', null, 1], ['candidate.change', 'attempt', null, 1]];
  const CLOSED: Rec[] = [['attempt.closed', 'attempt', null, 1]];                   // the unit's change review is asked here
  const UNIT_GATES: Rec[] = [['gate.result', 'attempt', null, 1], ['mirror.gate_outcome', 'attempt', null, 1], ['attempt.concluded', 'attempt', null, 1], ['unit.closed', null, null, null]];
  const UNIT: Rec[] = [...UNIT_OPEN, ...IMPLEMENTING, ...CLOSED, ...UNIT_GATES];
  const TERMINAL_GATES: Rec[] = [['model.request', 'requirement_verification', 'spec_compliance', null], ['model.result', 'requirement_verification', 'spec_compliance', null],
    ['gate.result', null, null, null], ['gate.result', null, null, null]];
  const review = { review_id: 'r1' } as unknown as RunView['review'];
  const at = (records: Rec[], lifecycle: string | undefined, approval?: 'PENDING' | 'APPROVED', extra: Partial<RunView> = {}) =>
    runPhase(run(records, { status: status('RUNNING', approval, lifecycle), ...extra }));
  const stepsOf = (phase: ReturnType<typeof runPhase>) => phase.steps?.map((s) => `${s.id}:${s.status}`);
  const IMPLEMENTING_STEPS = ['planning:done', 'editing:current', 'validating:pending', 'finished:pending'];
  const VERIFYING_STEPS = ['planning:done', 'editing:done', 'validating:current', 'finished:pending'];
  const FALSE_CLAIM = /Finishing|final checks|final regression|Your change was approved/i;

  it('S1 implement -> S1 review -> S1 approved and checked -> S2 -> S3: every unit stays under Implementing, whatever was approved', () => {
    const s1Implementing = at([...PLAN, ...UNIT_OPEN, ...IMPLEMENTING], 'CANDIDATE');
    expect(s1Implementing).toMatchObject({ state: 'EDITING', label: 'Implementing' });
    expect(stepsOf(s1Implementing)).toEqual(IMPLEMENTING_STEPS);

    const s1Review = at([...PLAN, ...UNIT_OPEN, ...IMPLEMENTING, ...CLOSED], 'CANDIDATE', 'PENDING', { review });
    expect(s1Review).toMatchObject({ state: 'WAITING_FOR_APPROVAL', needsAction: true });
    expect(stepsOf(s1Review)).toEqual(IMPLEMENTING_STEPS);
    expect(s1Review.detail).not.toMatch(/complete change/);

    // THE FIRST RED BEFORE THE CORRECTION: the unit's change was approved and Kriya builds and tests that one unit. The old projection said
    // "Finishing - Your change was approved. Kriya is running final checks." with Verifying current; the run was at unit 1 of 9.
    const s1Approved = at([...PLAN, ...UNIT_OPEN, ...IMPLEMENTING, ...CLOSED, ...UNIT_GATES.slice(0, 2)], 'CANDIDATE', 'APPROVED');
    expect(stepsOf(s1Approved)).toEqual(IMPLEMENTING_STEPS);
    expect(`${s1Approved.label} ${s1Approved.detail}`).not.toMatch(FALSE_CLAIM);
    expect(s1Approved.terminalActivity).toBeUndefined();

    const s2Implementing = at([...PLAN, ...UNIT, ...UNIT_OPEN, ...IMPLEMENTING], 'CANDIDATE', 'APPROVED');   // the launch record still carries S1's approved review
    expect(s2Implementing).toMatchObject({ state: 'EDITING', label: 'Implementing' });
    expect(stepsOf(s2Implementing)).toEqual(IMPLEMENTING_STEPS);
    expect(`${s2Implementing.label} ${s2Implementing.detail}`).not.toMatch(FALSE_CLAIM);

    const s3Implementing = at([...PLAN, ...UNIT, ...UNIT, ...UNIT_OPEN, ...IMPLEMENTING], 'CANDIDATE', 'APPROVED');   // the moment of the Run 9 screenshot (16:07:52)
    expect(s3Implementing).toMatchObject({ state: 'EDITING', label: 'Implementing' });
    expect(stepsOf(s3Implementing)).toEqual(IMPLEMENTING_STEPS);

    const betweenUnits = at([...PLAN, ...UNIT, ...UNIT, ...UNIT], 'CANDIDATE', 'APPROVED');   // the last unit closed; the lifecycle has not moved yet
    expect(stepsOf(betweenUnits)).toEqual(IMPLEMENTING_STEPS);
    expect(betweenUnits.terminalActivity).toBeUndefined();
  });

  it('VERIFYING -> terminal gates -> complete-change review -> commit -> SUCCESS: Verifying only from the lifecycle state, Complete only from the outcome', () => {
    const allUnits: Rec[] = [...PLAN, ...UNIT, ...UNIT, ...UNIT];
    const verifying = at(allUnits, 'VERIFYING', 'APPROVED');                         // the old unit approval is still the launch record's last review
    expect(verifying).toMatchObject({ state: 'VALIDATING', label: 'Verifying', terminalActivity: 'verifying', needsAction: false });
    expect(stepsOf(verifying)).toEqual(VERIFYING_STEPS);
    expect(verifying.detail).toBe('Kriya is running its final checks on the complete change.');

    const gatesRecorded = at([...allUnits, ...TERMINAL_GATES], 'VERIFYING', 'APPROVED');
    expect(gatesRecorded).toMatchObject({ label: 'Verifying', terminalActivity: 'verifying' });

    const completeReview = at([...allUnits, ...TERMINAL_GATES], 'VERIFYING', 'PENDING', { review });
    expect(completeReview).toMatchObject({ state: 'WAITING_FOR_APPROVAL', needsAction: true });
    expect(stepsOf(completeReview)).toEqual(VERIFYING_STEPS);
    expect(completeReview.detail).toMatch(/complete change/);
    expect(completeReview.terminalActivity).toBeUndefined();

    // the complete change was approved: Kriya applies it. Nothing is checked after this approval, and nothing says so.
    for (const lifecycle of ['COMMIT_ELIGIBLE', 'COMMITTED']) {
      const applying = at([...allUnits, ...TERMINAL_GATES], lifecycle, 'APPROVED');
      expect(applying, lifecycle).toMatchObject({ label: 'Applying', terminalActivity: 'applying', needsAction: false });
      expect(stepsOf(applying), lifecycle).toEqual(VERIFYING_STEPS);
      expect(`${applying.label} ${applying.detail}`, lifecycle).not.toMatch(FALSE_CLAIM);
      expect(applying.detail, lifecycle).not.toMatch(/approv/i);                    // COMMIT_ELIGIBLE is the commit, not a statement about an approval
      expect(applying.steps?.find((s) => s.id === 'finished')?.status, lifecycle).toBe('pending');   // applied is not Complete: only the outcome is
    }

    const done = runPhase(run([...allUnits, ...TERMINAL_GATES], { stage: 'finished', status: status('ENDED', 'APPROVED', 'SUCCESS'),
      outcome: { kind: 'success', label: 'L', detail: 'D', source: 'S', traceRunId: null, workflowStatus: null, declined: [], recordedError: null } as RunView['outcome'] }));
    expect(done).toMatchObject({ state: 'COMPLETED', terminal: true });
    expect(done.steps?.every((s) => s.status === 'done')).toBe(true);
  });

  it('negative control: an approved or decided review of ANY subject never promotes the global step while the lifecycle is CANDIDATE', () => {
    const records: Rec[] = [...PLAN, ...UNIT, ...UNIT_OPEN, ...IMPLEMENTING, ...CLOSED];
    for (const kind of ['change_approval', 'runtime_command', 'verifier_defect_repair_authorization', 'verifier_semantic_review']) {
      for (const decided of ['APPROVED', 'DECIDED', 'REJECTED']) {
        const launch = { phase: 'RUNNING', pending_approval: { kind, status: decided, decision: { decision: 'expectation_valid' } } };
        const phase = runPhase(run(records, { status: { launch, run_record: { found: true, unreadable: null, lifecycle_state: 'CANDIDATE' } } as unknown as RunView['status'] }));
        expect(phase.steps?.find((s) => s.id === 'validating')?.status, `${kind} ${decided}`).toBe('pending');
        expect(phase.terminalActivity, `${kind} ${decided}`).toBeUndefined();
        expect(`${phase.label} ${phase.detail}`, `${kind} ${decided}`).not.toMatch(FALSE_CLAIM);
      }
    }
  });

  it('negative control: a run that failed in or after its terminal stage never shows Complete', () => {
    for (const lifecycle of ['VERIFYING', 'COMMIT_ELIGIBLE', 'COMMITTED', 'FAILURE', 'UNCERTAIN']) {
      for (const kind of ['failure', 'needs_review', 'interrupted']) {
        const failed = runPhase(run([...PLAN, ...UNIT, ...TERMINAL_GATES], { stage: 'finished', status: status('ENDED', 'APPROVED', lifecycle),
          outcome: { kind, label: 'L', detail: 'D', source: 'S', traceRunId: null, workflowStatus: null, declined: [], recordedError: null } as RunView['outcome'] }));
        expect(failed.state, `${lifecycle} ${kind}`).toBe('FAILED');
        expect(failed.steps, `${lifecycle} ${kind}`).toBeNull();
      }
    }
  });

  it('a recovery after terminal verification (VERIFYING -> CANDIDATE) moves the step back to Implementing', () => {
    const allUnits: Rec[] = [...PLAN, ...UNIT, ...UNIT];
    expect(stepsOf(at([...allUnits, ...TERMINAL_GATES], 'VERIFYING'))).toEqual(VERIFYING_STEPS);
    const repair: Rec[] = [...allUnits, ...TERMINAL_GATES, ['unit.opened', null, null, null], ['attempt.opened', 'attempt', null, 2], ['model.request', 'attempt', 'developer', 2]];
    const recovering = at(repair, 'CANDIDATE');
    expect(recovering).toMatchObject({ state: 'REPAIRING' });
    expect(stepsOf(recovering)).toEqual(IMPLEMENTING_STEPS);
    expect(recovering.terminalActivity).toBeUndefined();
  });

  it('without a readable lifecycle state nothing is promoted and nothing is invented', () => {
    const allUnits: Rec[] = [...PLAN, ...UNIT, ...UNIT, ...UNIT];
    const unreadable = (found: boolean) => ({ launch: { phase: 'RUNNING', pending_approval: { status: 'APPROVED' } }, run_record: { found, unreadable: found ? null : 'damaged' } }) as unknown as RunView['status'];
    for (const st of [status('RUNNING', 'APPROVED'), unreadable(false), unreadable(true)]) {
      const after = runPhase(run([...allUnits, ...TERMINAL_GATES], { status: st }));     // everything recorded, a review approved, but no lifecycle state was read
      expect(after.state).toBe('RUNNING');
      expect(after.steps).toBeNull();
      expect(after.terminalActivity).toBeUndefined();
      expect(`${after.label} ${after.detail}`).not.toMatch(FALSE_CLAIM);
      expect(stepsOf(runPhase(run([...PLAN, ...UNIT_OPEN, ...IMPLEMENTING], { status: st })))).toEqual(IMPLEMENTING_STEPS);   // an open attempt is unit work whatever the status says
      expect(stepsOf(runPhase(run([...allUnits], { status: st, review })))).toEqual(IMPLEMENTING_STEPS);   // a change review is never taken for the complete-change one without the lifecycle state
    }
    // a lifecycle state the projection does not stage (NEW / RUNNING) is treated the same way
    expect(runPhase(run(allUnits, { status: status('RUNNING', 'APPROVED', 'RUNNING') })).steps).toBeNull();
  });
});

describe('run phase once the outcome is durable', () => {
  const finished = (kind: string, extra: Record<string, unknown> = {}): RunView => run(P, { stage: 'finished', outcome: { kind, label: 'L', detail: 'D', source: 'S', traceRunId: null, workflowStatus: null, declined: [], recordedError: null, ...extra } as RunView['outcome'] });
  it('success is COMPLETED with every step done', () => {
    const p = runPhase(finished('success'));
    expect(p).toMatchObject({ state: 'COMPLETED', terminal: true, tone: 'ok', needsAction: false });
    expect(p.steps?.every((s) => s.status === 'done')).toBe(true);
  });
  it('the person\'s own rejection, an expired review and a decision the desktop cannot take are STOPPED', () => {
    for (const kind of ['rejected', 'review_expired', 'approval_required']) expect(runPhase(finished(kind)).state, kind).toBe('STOPPED');
  });
  it('every other recorded end is FAILED, with the last stage the records show', () => {
    for (const kind of ['failure', 'needs_review', 'refused', 'interrupted', 'not_recorded']) expect(runPhase(finished(kind)).state, kind).toBe('FAILED');
    expect(runPhase(finished('failure'))).toMatchObject({ steps: null, lastStage: 'Validating', terminal: true });
    expect(runPhase(run([P[0]!, P[2]!], { stage: 'finished', outcome: { kind: 'failure', label: 'L', detail: 'D', source: 'S', traceRunId: null, workflowStatus: null, declined: [], recordedError: null } })).lastStage).toBe('Planning');
  });
  it('a finished run never keeps needing action even if a stale review object is still attached', () => {
    const stale = { ...finished('success'), review: { review_id: 'r1' } as unknown as RunView['review'] };
    expect(runPhase(stale).needsAction).toBe(false);
  });
});
