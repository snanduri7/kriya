/**
 * Retry facts: what a rejected attempt was rejected FOR, in plain words, from RECORDED gate outcomes only (Demo V2). A deterministic, closed mapping from the recorded gate type to one
 * sentence; a type this table does not know gets no sentence (never a guess, never model prose). The specific rule behind a static finding is not in the recorded payload, so it is not named.
 * Delivery failures (a response the model got wrong in shape) are separated from findings about the candidate itself - mirroring Kriya's own open-finding rule: a delivery failure
 * neither opens nor closes the finding the earlier attempt left unresolved.
 */
import { gateRows } from './selectors';
import type { EvidenceRecord } from './records';

/** Gate types that report the shape of the model's response, not a verdict on the candidate (kriya/workflow/open_findings.py DELIVERY_FAILURE_TYPES). */
const DELIVERY_TYPES = new Set(['anchored_edit', 'attribution_rejected', 'diagnosis_mismatch', 'misdirected_edit', 'no_op_edit', 'operation_contract', 'structural_corruption', 'unaddressed_error_location']);
export const DELIVERY_WORDS = 'The model response format was invalid.';

const FINDING_WORDS: Record<string, string> = {
  compile: 'The project did not build.',
  test: 'A test failed.', tests: 'A test failed.', targeted_test: 'A test failed.', regression_test: 'An existing test failed.',
  run_verification: 'Running the application did not give the expected result.', run_verification_hung: 'The application did not finish on its own.',
  static_rule_violation: 'A static quality rule was violated.',
  pom_semantic_validation: 'The build file (pom.xml) is not valid.',
  semantic_safety: 'A safety check failed.', static_analysis: 'A safety check failed.', stack_contract: 'The change does not match the project stack.',
  goal_spec_compliance: 'The change does not match the planned change.',
};

export interface Rejection { words: string | null; delivery: boolean }
/** The words for a recorded failed gate type, or null when the closed table does not know it. */
export function rejectionOf(type: string): Rejection | null {
  if (DELIVERY_TYPES.has(type)) return { words: DELIVERY_WORDS, delivery: true };
  const words = FINDING_WORDS[type];
  return words === undefined ? null : { words, delivery: false };
}

export interface AttemptRejection { attempt: number; seq: number; type: string; words: string | null; delivery: boolean; /** The earlier finding still unresolved when this attempt was rejected for a delivery failure. */ unresolved: string | null }

/** The first failed (non-terminal) gate of each attempt, in recorded order, with the finding left open by earlier attempts. */
export function attemptRejections(records: readonly EvidenceRecord[]): AttemptRejection[] {
  const out: AttemptRejection[] = [];
  const seen = new Set<number>();
  let open: string | null = null;
  for (const row of gateRows([...records].sort((a, b) => a.seq - b.seq))) {
    if (row.origin === 'terminal' || row.success !== false || row.attempt === null || seen.has(row.attempt)) continue;
    seen.add(row.attempt);
    const rejection = rejectionOf(row.type);
    out.push({ attempt: row.attempt, seq: row.seq, type: row.type, words: rejection?.words ?? null, delivery: rejection?.delivery ?? false, unresolved: rejection?.delivery ? open : null });
    if (rejection !== null && !rejection.delivery && rejection.words !== null) open = rejection.words;
  }
  return out;
}

export interface RetrySummary { attempts: number; /** The finding about the candidate itself that the last non-delivery rejection reported, in words; null when none is recorded. */ dominant: string | null }
export function retrySummary(records: readonly EvidenceRecord[]): RetrySummary {
  const attempts = new Set<number>();
  for (const r of records) if (r.kind === 'attempt.opened' && typeof r.attempt_number === 'number') attempts.add(r.attempt_number);
  const rejections = attemptRejections(records);
  const findings = rejections.filter((r) => !r.delivery && r.words !== null);
  return { attempts: attempts.size, dominant: findings.length ? findings[findings.length - 1]!.words : null };
}
