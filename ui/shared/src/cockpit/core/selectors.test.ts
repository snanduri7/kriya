import { describe, expect, it } from 'vitest';
import { gateKey, attemptFor, attemptNumbers, attemptOutcomes, changeRows, diagnoses, fallbacks, firstFailedGate, gateRows, recoveries, runClosure, type EvidenceRecord } from '.';

let seq = 0;
const rec = (kind: string, payload: Record<string, unknown> = {}, over: Record<string, unknown> = {}): EvidenceRecord => ({ schema: 's', seq: ++seq, prev: 'p', kind, run_id: 'r', provenance: 'OBSERVED', payload, content_digests: {}, blobs: {}, phase_provenance: null, attempt_number: null, ...over }) as unknown as EvidenceRecord;

describe('gateRows: the recorder vocabulary as recorded', () => {
  it('reads validator, terminal and mirrored gates in recorded order, keeping the exact type', () => {
    const rows = gateRows([
      rec('gate.result', { stage: 'validator', gate: 'compile', success: true, exit_code: 0, duration_seconds: 1.4, timed_out: false }, { attempt_number: 1, blobs: { output: 'sha256:aa' } }),
      rec('mirror.gate_outcome', { attempt: 1, index: 1, success: false, type: 'regression_test' }, { attempt_number: 1 }),
      rec('gate.result', { stage: 'terminal', gate: 'static_analysis', success: true }),
      rec('mirror.gate_outcome', { type: 'operation_contract', success: false }, { attempt_number: 2 }),
    ]);
    expect(rows.map((g) => [g.type, g.origin, g.success, g.attempt, g.id])).toEqual([
      ['compile', 'validator', true, 1, gateKey(1, 'compile', rows[0]!.seq)], ['regression_test', 'mirrored', false, 1, gateKey(1, 'regression_test', rows[1]!.seq)], ['static_analysis', 'terminal', true, null, gateKey(null, 'static_analysis', rows[2]!.seq)], ['operation_contract', 'mirrored', false, 2, gateKey(2, 'operation_contract', rows[3]!.seq)],
    ]);
    expect(new Set(rows.map((g) => g.id)).size).toBe(4);
    // the same gate type recorded twice in one attempt (validator result + mirrored outcome) must stay two selectable gates
    const twice = gateRows([rec('gate.result', { gate: 'compile', success: true }, { attempt_number: 1 }), rec('mirror.gate_outcome', { type: 'compile', success: true }, { attempt_number: 1 })]);
    expect(twice[0]!.id).not.toBe(twice[1]!.id);
    expect(rows[0]).toMatchObject({ exitCode: 0, durationSeconds: 1.4, timedOut: false, outputKept: true, testSummary: null });
    expect(rows[1]).toMatchObject({ index: 1, exitCode: null, durationSeconds: null, outputKept: false });
  });
  it('incomplete records stay incomplete: no outcome is invented and a missing type is said so', () => {
    const [g] = gateRows([rec('gate.result', {}, { attempt_number: 3 })]);
    expect(g).toMatchObject({ success: null, type: '(gate type not recorded)', exitCode: null, durationSeconds: null, timedOut: null });
    expect(gateRows([rec('gate.result', { gate: 'tests', success: 'yes', exit_code: '1' })])[0]).toMatchObject({ success: null, exitCode: null });
  });
  it('keeps a structured test summary only when the recorder persisted one', () => {
    expect(gateRows([rec('gate.result', { gate: 'tests', success: false, test_execution: { status_counts: { passed: 3, failed: 1 } } })])[0]!.testSummary).toEqual({ status_counts: { passed: 3, failed: 1 } });
    expect(gateRows([rec('gate.result', { gate: 'tests', success: false, test_execution: 'x' })])[0]!.testSummary).toBeNull();
  });
  it('the first failed gate is the first in RECORDED order, not the first by name', () => {
    const rows = gateRows([rec('gate.result', { gate: 'tests', success: true }), rec('gate.result', { gate: 'tests', success: false }), rec('gate.result', { gate: 'compile', success: false })]);
    expect(firstFailedGate(rows)?.seq).toBe(rows[1]!.seq);
    expect(firstFailedGate(gateRows([rec('gate.result', { gate: 'tests', success: true })]))).toBeNull();
  });
});

describe('changeRows: generic change semantics, nothing guessed', () => {
  const change = (payload: Record<string, unknown>, over: Record<string, unknown> = {}) => changeRows([rec('candidate.change', { path: 'a/B.java', decision: 'STAGED', ...payload }, { attempt_number: 1, ...over })])[0]!;
  it('classifies only what the record says', () => {
    expect(change({ created: true, before_digest: null, after_digest: 'sha256:2' }).kind).toBe('ADDED');
    expect(change({ before_digest: 'sha256:1', after_digest: 'sha256:2' }).kind).toBe('MODIFIED');
    expect(change({ before_digest: 'sha256:1', after_digest: null, deleted: true }).kind).toBe('DELETED');
    expect(change({ before_digest: 'sha256:1', after_digest: 'sha256:1', unchanged: true }).kind).toBe('UNCHANGED');
    expect(change({ after_digest: 'sha256:2' }).kind).toBe('UNAVAILABLE'); // no baseline recorded and not created
    expect(change({}).kind).toBe('UNAVAILABLE');
  });
  it('never produces RENAMED (not recorded) and drops digests and counts of a refused candidate', () => {
    for (const p of [{ created: true }, { deleted: true }, { unchanged: true }, { before_digest: 'sha256:1' }]) expect(change(p).kind).not.toBe('RENAMED');
    expect(change({ decision: 'REFUSED', reason_code: 'PROTOCOL_INVALID', before_digest: 'sha256:1', lines_added: 9 })).toMatchObject({ kind: 'UNAVAILABLE', reasonCode: 'PROTOCOL_INVALID', beforeDigest: null, linesAdded: null });
  });
  it('separates content the recorder kept from content it only digested', () => {
    expect(change({ created: true }, { blobs: { after: 'sha256:a', diff: 'sha256:d' }, content_digests: { after: { digest: 'sha256:a', bytes: 1 }, diff: { digest: 'sha256:d', bytes: 2 }, before: { digest: 'sha256:b', bytes: 3 } } })).toMatchObject({ kept: ['after', 'diff'], digestOnly: ['before'] });
    expect(change({ created: true }, { content_digests: { diff: { digest: 'sha256:d', bytes: 2 } } })).toMatchObject({ kept: [], digestOnly: ['diff'] });
  });
});

describe('diagnosis, recovery, fallback, outcomes', () => {
  it('keeps the recorded evidence class exactly; absent stays null; an unrecognized value is UNKNOWN, never promoted', () => {
    const base = { type: 'compile', reason_code: 'X', attribution_kind: 'SOURCE_DEFECT', attribution_tier: 'judge', attribution_confidence: 'medium', likely_files: ['a', 5] };
    const [a, b, c] = diagnoses([rec('diagnosis', { ...base, evidence_class: 'MODEL_CLAIMED' }, { attempt_number: 1, provenance: 'MODEL_CLAIMED' }), rec('diagnosis', base), rec('diagnosis', { ...base, evidence_class: 'TRACED' })]);
    expect([a!.evidenceClass, b!.evidenceClass, c!.evidenceClass]).toEqual(['MODEL_CLAIMED', null, 'UNKNOWN']);
    expect(a).toMatchObject({ type: 'compile', reasonCode: 'X', attributionKind: 'SOURCE_DEFECT', attributionTier: 'judge', attributionConfidence: 'medium', likelyFiles: ['a'], provenance: 'MODEL_CLAIMED', messageKept: false });
    expect(diagnoses([rec('diagnosis', base, { blobs: { message: 'sha256:m' } })])[0]!.messageKept).toBe(true);
  });
  it('recovery and fallback keep the recorded values, including the inline reason', () => {
    expect(recoveries([rec('recovery.decision', { action: 'stop_environment', retry: false, stop_reason_code: 'R', progress_classification: 'PROGRESS', failure_type: 'f', retry_decision: { reason: 'because' } }, { attempt_number: 1 })])[0]).toEqual({ seq: expect.any(Number), attempt: 1, action: 'stop_environment', retry: false, reason: 'because', stopReasonCode: 'R', progress: 'PROGRESS', failureType: 'f' });
    expect(fallbacks([rec('fallback.decision', { fallback: false, phase: 'call', requested: 'm', selected: 'm' })])[0]).toMatchObject({ phase: 'call', fell_back: false, requested: 'm', selected: 'm' });
  });
  it('outcomes: concluded per attempt, closed outcome, run closure', () => {
    const records = [rec('attempt.concluded', { succeeded: false }, { attempt_number: 1 }), rec('attempt.closed', { outcome: 'FAILED' }, { attempt_number: 1 }), rec('attempt.concluded', { succeeded: true }, { attempt_number: 2 }), rec('run.closed', { terminal_status: 'SUCCESS' })];
    expect(attemptOutcomes(records)).toEqual([{ attempt: 1, concluded: false, closedOutcome: 'FAILED' }, { attempt: 2, concluded: true, closedOutcome: null }]);
    expect(runClosure(records)).toEqual({ terminalStatus: 'SUCCESS', present: true });
    expect(runClosure([])).toEqual({ terminalStatus: null, present: false });
  });
  it('attempt selection: the selected attempt wins, else the highest attempt that has such a record', () => {
    const records = [rec('gate.result', {}, { attempt_number: 1 }), rec('gate.result', {}, { attempt_number: 3 }), rec('candidate.change', {}, { attempt_number: 2 })];
    expect(attemptNumbers(records)).toEqual([1, 2, 3]);
    expect([attemptFor(records, null, ['gate.result']), attemptFor(records, '1', ['gate.result']), attemptFor(records, null, ['candidate.change']), attemptFor(records, null, ['diagnosis'])]).toEqual([3, 1, 2, null]);
  });
});
