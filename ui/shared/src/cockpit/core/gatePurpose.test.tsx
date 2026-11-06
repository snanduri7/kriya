/** WHY a gate ran is the backend-declared `purpose` of its gate.result - read, never inferred from the order of records or a label; absent = "not recorded". */
import { describe, expect, it } from 'vitest';
import { gateRows } from './selectors';
import type { EvidenceRecord } from './records';

const rec = (seq: number, kind: string, payload: Record<string, unknown>): EvidenceRecord => ({ seq, kind, attempt_number: 1, payload, blobs: {} } as unknown as EvidenceRecord);

describe('gate purpose', () => {
  it('reads the purpose the orchestrator declared and records none for a gate that declared none', () => {
    const rows = gateRows([
      rec(1, 'gate.result', { gate: 'tests', success: true, stage: 'validator', purpose: 'named_test_closure_base_run' }),
      rec(2, 'gate.result', { gate: 'tests', success: true, stage: 'validator' }),
      rec(3, 'gate.result', { gate: 'compile', success: false, stage: 'validator', purpose: 'candidate_verification' }),
    ]);
    expect(rows.map((r) => [r.seq, r.type, r.purpose])).toEqual([[1, 'tests', 'named_test_closure_base_run'], [2, 'tests', null], [3, 'compile', 'candidate_verification']]);
  });

  it('never attributes a purpose to a mirrored outcome, whatever else its payload carries', () => {
    const [row] = gateRows([rec(5, 'mirror.gate_outcome', { type: 'tests', success: true, purpose: 'candidate_verification' })]);
    expect(row?.purpose).toBeNull();
  });

  it('ignores a purpose that is not a string', () => {
    const [row] = gateRows([rec(6, 'gate.result', { gate: 'tests', success: true, purpose: 7 })]);
    expect(row?.purpose).toBeNull();
  });
});

describe('a failure on the original code is the baseline, not a failure of the change', () => {
  const rows = () => gateRows([
    rec(58, 'gate.result', { gate: 'tests', success: false, stage: 'validator', purpose: 'named_test_closure_base_run' }),
    rec(59, 'gate.result', { gate: 'tests', success: true, stage: 'validator', purpose: 'named_test_closure_candidate_run' }),
  ]);
  it('does not make the base run the "first failed gate", and lists it separately', async () => {
    const { firstFailedGate, expectedBaselineFailures } = await import('./selectors');
    expect(firstFailedGate(rows())).toBeNull();
    expect(expectedBaselineFailures(rows()).map((g) => g.seq)).toEqual([58]);
  });
  it('still reports a failing candidate run, and a failing gate with no declared purpose, as failures', async () => {
    const { firstFailedGate } = await import('./selectors');
    const candidate = gateRows([rec(1, 'gate.result', { gate: 'tests', success: false, purpose: 'named_test_closure_candidate_run' })]);
    const undeclared = gateRows([rec(2, 'gate.result', { gate: 'tests', success: false })]);
    expect(firstFailedGate(candidate)?.seq).toBe(1);
    expect(firstFailedGate(undeclared)?.seq).toBe(2);
  });
});
