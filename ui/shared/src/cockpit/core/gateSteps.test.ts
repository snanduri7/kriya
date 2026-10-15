/** Gate steps: the structural grouping of the validator's gate.result and the workflow's mirror.gate_outcome (never a comparison of display strings). */
import { describe, expect, it } from 'vitest';
import { activityFromRecords } from './activity';
import { gateSteps } from './gateSteps';
import type { EvidenceRecord } from './records';

const rec = (seq: number, kind: string, extra: Record<string, unknown> = {}): EvidenceRecord => ({ seq, kind, attempt_number: 1, unit_id: 'u1', t_wall: `2026-10-15T10:00:${String(seq).padStart(2, '0')}Z`, payload: {}, blobs: {}, ...extra }) as unknown as EvidenceRecord;
const result = (seq: number, gate: string, success: boolean | null, extra: Record<string, unknown> = {}, over: Record<string, unknown> = {}) => rec(seq, 'gate.result', { payload: { gate, success, stage: 'validator', ...extra }, ...over });
const outcome = (seq: number, type: string, success: boolean | null, over: Record<string, unknown> = {}) => rec(seq, 'mirror.gate_outcome', { payload: { type, success, index: 0 }, ...over });
const texts = (records: EvidenceRecord[]) => activityFromRecords(records).map((e) => e.text);

describe('pairing', () => {
  it('a result and the outcome of its family are one step; the step is named by the outcome (the workflow\'s gate identity)', () => {
    const steps = gateSteps([result(1, 'tests', true, { test_execution: { cases: 3 } }), outcome(2, 'regression_test', true)]);
    expect(steps).toHaveLength(1);
    expect(steps[0]).toMatchObject({ type: 'regression_test', family: 'tests', sources: [1, 2], success: true, testSummary: { cases: 3 } });
  });
  it('an outcome never pairs across families, attempts or units', () => {
    expect(gateSteps([result(1, 'compile', true), outcome(2, 'targeted_test', true)])).toHaveLength(2);
    expect(gateSteps([result(1, 'compile', true), outcome(2, 'compile', true, { attempt_number: 2 })])).toHaveLength(2);
    expect(gateSteps([result(1, 'compile', true), outcome(2, 'compile', true, { unit_id: 'u2' })])).toHaveLength(2);
  });
  it('each outcome claims ONE result: two test executions with two outcomes stay two steps, in order', () => {
    const steps = gateSteps([result(1, 'tests', true), outcome(2, 'targeted_test', true), result(3, 'tests', true), outcome(4, 'regression_test', true)]);
    expect(steps.map((s) => [s.sources, s.type])).toEqual([[[1, 2], 'targeted_test'], [[3, 4], 'regression_test']]);
  });
  it('either side failing makes the step fail: a grader can withhold a pass but a pass is never made of one side alone', () => {
    expect(gateSteps([result(1, 'runtime_verification', true), outcome(2, 'run_verification', false)])[0]!.success).toBe(false);
    expect(gateSteps([result(1, 'runtime_verification', true), outcome(2, 'run_verification', null)])[0]!.success).toBe(true);
    expect(gateSteps([result(1, 'compile', false), outcome(2, 'compile', true)])[0]!.success).toBe(false);
  });
});

describe('folding a repeat that adds no fact', () => {
  const attemptWithOutcomes = [result(1, 'compile', true), outcome(2, 'compile', true)];
  it('a passing repeated build with no outcome of its own, in an attempt that records outcomes, is folded (and still listed among the sources)', () => {
    const steps = gateSteps([...attemptWithOutcomes, result(3, 'compile', true)]);
    expect(steps.map((s) => [s.sources, s.folded])).toEqual([[[1, 2], false], [[3], true]]);
    expect(texts([rec(0, 'attempt.opened'), ...attemptWithOutcomes, result(3, 'compile', true)]).filter((t) => t === 'Build passed')).toHaveLength(1);
  });
  it('a FAILED repeat is never folded: the failure speaks, with its rejection', () => {
    const steps = gateSteps([...attemptWithOutcomes, result(3, 'compile', false)]);
    expect(steps[1]).toMatchObject({ folded: false, success: false });
    expect(texts([...attemptWithOutcomes, result(3, 'compile', false)])).toEqual(['Build passed', 'Build failed', 'Attempt 1 rejected', 'The project did not build.']);
  });
  it('without outcomes the records are the only account, so two builds are two lines (nothing is assumed to be a prerequisite)', () => {
    expect(texts([result(1, 'compile', true), result(2, 'compile', true)])).toEqual(['Build passed', 'Build passed']);
  });
  it('the first build of a later attempt is a new fact even though an earlier attempt built', () => {
    const second = { attempt_number: 2 };
    const steps = gateSteps([...attemptWithOutcomes, result(3, 'compile', true, {}, second), result(4, 'tests', true, {}, second), outcome(5, 'targeted_test', true, second)]);
    expect(steps.find((s) => s.sources[0] === 3)!.folded).toBe(false);
  });
  it('only a BUILD repeat is a prerequisite: a second passing test run in the attempt is its own fact (review I-2)', () => {
    const steps = gateSteps([result(1, 'tests', true, { test_execution: { cases: 1 } }), outcome(2, 'targeted_test', true), result(3, 'tests', true, { test_execution: { cases: 40 } })]);
    expect(steps.map((s) => s.folded)).toEqual([false, false]);
    expect(texts([result(1, 'tests', true, { test_execution: { cases: 1 } }), outcome(2, 'targeted_test', true), result(3, 'tests', true, { test_execution: { cases: 40 } })])).toEqual(['Tests passed (1 test)', 'Tests passed (40 tests)']);
  });
  it('a repeat in ANOTHER attempt is a new fact', () => {
    const steps = gateSteps([...attemptWithOutcomes, result(3, 'compile', true, {}, { attempt_number: 2 }), outcome(4, 'compile', true, { attempt_number: 2 })]);
    expect(steps.map((s) => s.folded)).toEqual([false, false]);
  });
});

describe('rejections survive the grouping', () => {
  it('a paired step whose OUTCOME is the failing record (the grader) still reports the attempt\'s rejection, once', () => {
    const t = texts([rec(0, 'attempt.opened'), result(1, 'runtime_verification', true), outcome(2, 'run_verification', false)]);
    expect(t).toEqual(['Working on the change', 'Runtime verification failed', 'Attempt 1 rejected', 'Running the application did not give the expected result.']);
  });
  it('a lone mirrored outcome of a type with no line of its own keeps its rejection words', () => {
    expect(texts([outcome(1, 'static_rule_violation', false)])).toEqual(['Attempt 1 rejected', 'A static quality rule was violated.']);
  });
});
