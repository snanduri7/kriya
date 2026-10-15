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

const status = (phase: 'STARTING' | 'RUNNING' | 'ENDED', approval?: 'PENDING' | 'APPROVED'): LaunchStatus => ({ launch: { phase, pending_approval: approval ? { status: approval } : undefined } } as unknown as LaunchStatus);
function run(records: Rec[], extra: Partial<RunView> = {}): RunView {
  return { launchId: 'l1', workspace: '/w', host: null, status: status('RUNNING'), statusError: null, progress: progressOf(records), stage: 'running', outcome: null, review: null, runtimeReview: null,
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
  it('gate records and Kriya\'s own review call are VALIDATING', () => {
    expect(states(upTo('gate.result', 2))).toBe('VALIDATING');
    expect(states(upTo('mirror.gate_outcome'))).toBe('VALIDATING');
    expect(states(upTo('model.request', 3))).toBe('VALIDATING'); // the role is the reviewer
    expect(states(upTo('model.result', 3))).toBe('VALIDATING');
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
    expect(waiting.steps?.map((s) => s.status)).toEqual(['done', 'done', 'done', 'pending']);
    // a PENDING record without the live-run conditions (activeReview gave no review) is NOT waiting for the person
    expect(runPhase(run(P, { status: status('RUNNING', 'PENDING') })).state).not.toBe('WAITING_FOR_APPROVAL');
  });
  it('after the person approved and while the run is still going: finishing verification, not waiting', () => {
    const after = runPhase(run([...P, ['verification_contract.compiled', null, null, null]], { status: status('RUNNING', 'APPROVED') }));
    expect(after.state).toBe('VALIDATING');
    expect(after.label).toBe('Finishing');
    expect(after.needsAction).toBe(false);
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
    expect(at(upTo('mirror.gate_outcome'))).toEqual(['planning:done', 'editing:done', 'validating:current', 'finished:pending']);
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
