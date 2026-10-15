import { describe, expect, it } from 'vitest';
import { changeFacts, describeGate, validationFacts } from './runFacts';
import type { EvidenceRecord } from './records';

const rec = (seq: number, kind: string, attempt: number | null, payload: Record<string, unknown>): EvidenceRecord => ({ seq, kind, attempt_number: attempt, payload, blobs: {}, content_digests: {} } as unknown as EvidenceRecord);
const staged = (seq: number, attempt: number, path: string, added: number, removed: number) => rec(seq, 'candidate.change', attempt, { path, decision: 'STAGED', before_digest: 'sha256:a', after_digest: 'sha256:b', lines_added: added, lines_removed: removed });

describe('validation facts', () => {
  const records = [
    rec(1, 'gate.result', 1, { gate: 'tests', success: true, stage: 'validator', test_execution: { cases: 53, status_counts: { passed: 53 } } }),
    rec(2, 'gate.result', 1, { gate: 'compile', success: true, stage: 'validator' }),
    rec(3, 'mirror.gate_outcome', 1, { type: 'tests', success: true }),
    rec(4, 'mirror.gate_outcome', 1, { type: 'goal_spec_compliance', success: true }),
  ];
  it('keeps the latest result per gate and the structured test count the recorder persisted', () => {
    const facts = validationFacts(records);
    expect(facts.map((f) => f.type)).toEqual(['tests', 'compile', 'goal_spec_compliance']);
    expect(describeGate(facts[0]!)).toBe('Tests passed (53 tests)'); // the later mirrored outcome did not erase the count
    expect(describeGate(facts[1]!)).toBe('Compile passed');
  });
  it('a failed gate, a partial pass and a missing result are each said as recorded, never as passed', () => {
    expect(describeGate({ type: 'tests', passed: false, tests: { cases: 53, passed: 51 } })).toBe('Tests failed (51 of 53 tests)');
    expect(describeGate({ type: 'compile', passed: null, tests: null })).toBe('Compile no result recorded');
    expect(describeGate({ type: 'custom_gate', passed: true, tests: null })).toBe('custom_gate passed');
  });
  it('no test count is invented when the recorder persisted no structured summary', () => {
    expect(validationFacts([rec(1, 'gate.result', 1, { gate: 'tests', success: true })])[0]!.tests).toBeNull();
  });
});

describe('change facts', () => {
  it('lists the staged files of the newest attempt with the recorded line counts, not an earlier candidate', () => {
    const records = [staged(1, 1, 'A.java', 3, 1), staged(2, 2, 'B.java', 1, 0)];
    expect(changeFacts(records)).toEqual([{ path: 'B.java', linesAdded: 1, linesRemoved: 0, kind: 'MODIFIED' }]);
  });
  it('omits a change the recorder marked unavailable or unchanged', () => {
    expect(changeFacts([rec(1, 'candidate.change', 1, { path: 'X', decision: 'REJECTED' }), rec(2, 'candidate.change', 1, { path: 'Y', decision: 'STAGED', unchanged: true })])).toEqual([]);
  });
});

describe('validationFacts: a hung runtime check does not poison a later completed one (review I-3)', () => {
  const rec = (seq: number, kind: string, payload: Record<string, unknown>) => ({ seq, kind, attempt_number: 1, payload, blobs: {} }) as never;
  it('the later of run_verification_hung / run_verification speaks', () => {
    const hungThenPass = validationFacts([rec(1, 'mirror.gate_outcome', { type: 'run_verification_hung', success: false }), rec(2, 'mirror.gate_outcome', { type: 'run_verification', success: true })]);
    expect(hungThenPass.map((f) => [f.type, f.passed])).toEqual([['run_verification', true]]);
    const passThenHung = validationFacts([rec(1, 'mirror.gate_outcome', { type: 'run_verification', success: true }), rec(2, 'mirror.gate_outcome', { type: 'run_verification_hung', success: false })]);
    expect(passThenHung.map((f) => [f.type, f.passed])).toEqual([['run_verification_hung', false]]);
  });
});
