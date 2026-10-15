/** Retry facts: the closed table, the open-finding rule and the exhausted-run numbers all come from recorded gate types; nothing is guessed. */
import { describe, expect, it } from 'vitest';
import { exhaustedSummary } from './presentation';
import { attemptRejections, rejectionOf, retrySummary } from './retryFacts';
import type { EvidenceRecord } from './records';

const rec = (seq: number, kind: string, attempt: number, payload: Record<string, unknown> = {}): EvidenceRecord => ({ seq, kind, attempt_number: attempt, t_wall: `2026-10-15T10:00:${String(seq).padStart(2, '0')}Z`, payload } as unknown as EvidenceRecord);
const opened = (seq: number, n: number) => rec(seq, 'attempt.opened', n);
const failed = (seq: number, n: number, type: string) => rec(seq, 'mirror.gate_outcome', n, { index: seq, type, success: false, attempt: n });

describe('the closed table', () => {
  it('knows findings and delivery failures; an unknown type has no sentence', () => {
    expect(rejectionOf('compile')).toEqual({ words: 'The project did not build.', delivery: false });
    expect(rejectionOf('operation_contract')).toEqual({ words: 'The model response format was invalid.', delivery: true });
    expect(rejectionOf('something_new')).toBeNull();
  });
});

describe('attempt rejections and the exhausted-run summary', () => {
  const live = [opened(1, 1), failed(2, 1, 'static_rule_violation'), opened(3, 2), failed(4, 2, 'operation_contract'), opened(5, 3), failed(6, 3, 'operation_contract')];
  it('the open finding survives delivery failures (the live Demo 00 cycle)', () => {
    expect(attemptRejections(live).map((r) => [r.attempt, r.delivery, r.unresolved])).toEqual([[1, false, null], [2, true, 'A static quality rule was violated.'], [3, true, 'A static quality rule was violated.']]);
    expect(retrySummary(live)).toEqual({ attempts: 3, dominant: 'A static quality rule was violated.' });
  });
  it('no recorded finding: no dominant finding', () => {
    expect(retrySummary([opened(1, 1), failed(2, 1, 'operation_contract'), opened(3, 2)])).toEqual({ attempts: 2, dominant: null });
  });
  it('the final message states the attempts and the finding, and that nothing was applied', () => {
    expect(exhaustedSummary('quality_gate_failed', 'NOT_COMMITTED', { attempts: 8, dominant: 'A static quality rule was violated.' }))
      .toBe('Kriya could not produce a valid change after 8 attempts. A static quality rule was violated. Nothing was applied to your project.');
    expect(exhaustedSummary('quality_gate_failed', 'NOT_COMMITTED', { attempts: 8, dominant: null }))
      .toBe('Kriya could not produce a change that passed validation after 8 attempts. Nothing was applied to your project.');
  });
  it('it is silent (generic sentence) when it cannot be honest: one attempt, another category, or a committed change', () => {
    expect(exhaustedSummary('quality_gate_failed', 'NOT_COMMITTED', { attempts: 1, dominant: 'x' })).toBeNull();
    expect(exhaustedSummary('internal_error', 'NOT_COMMITTED', { attempts: 5, dominant: 'x' })).toBeNull();
    expect(exhaustedSummary('quality_gate_failed', 'COMMITTED', { attempts: 5, dominant: 'x' })).toBeNull();
    expect(exhaustedSummary('quality_gate_failed', 'NOT_COMMITTED', null)).toBeNull();
    expect(exhaustedSummary('quality_gate_failed', null, { attempts: 5, dominant: 'x' })).toBeNull();
  });
});
