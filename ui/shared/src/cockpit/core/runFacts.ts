/**
 * Plain-language summaries of a run's recorded gates and staged changes, for the Project page (the current-run surface, the approval panel and the outcome).
 * They only restate recorded fields through the existing selectors: a gate is "passed" only when its record says `success: true`, a test count appears only when
 * the recorder persisted a structured summary, and a change shows its recorded line counts. Anything not recorded stays absent.
 */
import { changeRows, gateRows } from './selectors';
import type { EvidenceRecord } from './records';

export interface GateFact {
  /** The gate type as recorded. */
  type: string;
  /** null: the record carries no result. */
  passed: boolean | null;
  /** The structured test summary the recorder persisted, when there is one. */
  tests: { cases: number; passed: number | null } | null;
  /** `original_requirements` only: every requirement positively verified (true), not all (false), or the record does not say (null). */
  requirementsVerified?: boolean | null;
}
export interface ChangeFact { path: string; linesAdded: number | null; linesRemoved: number | null; kind: string }

const GATE_NAME: Record<string, string> = { compile: 'Compile', test: 'Tests', tests: 'Tests', targeted_test: 'Targeted tests', regression_test: 'Regression tests', goal_spec_compliance: 'Goal compliance', static_analysis: 'Static analysis' };
export const gateName = (type: string): string => GATE_NAME[type] ?? type;

/** The latest recorded result of each gate type, in the order the types were first recorded. */
export function validationFacts(records: readonly EvidenceRecord[]): GateFact[] {
  const latest = new Map<string, GateFact>();
  for (const row of gateRows(records)) {
    const cases = row.testSummary && typeof row.testSummary.cases === 'number' ? row.testSummary.cases : null;
    const counts = row.testSummary && row.testSummary.status_counts !== null && typeof row.testSummary.status_counts === 'object' ? (row.testSummary.status_counts as Record<string, unknown>) : null;
    const passedCount = counts && typeof counts.passed === 'number' ? counts.passed : null;
    const fact: GateFact = { type: row.type, passed: row.success, tests: cases === null ? null : { cases, passed: passedCount }, requirementsVerified: row.requirementsVerified };
    // a mirrored outcome never replaces a validator result that carries the structured test summary
    const prior = latest.get(row.type);
    // a mirrored outcome never replaces what the terminal record said about requirement verification either
    const merged: GateFact = { ...fact, ...(prior && prior.tests && !fact.tests ? { tests: prior.tests } : {}), ...(fact.requirementsVerified == null && prior?.requirementsVerified != null ? { requirementsVerified: prior.requirementsVerified } : {}) };
    latest.set(row.type, merged);
  }
  return [...latest.values()];
}

/** The staged changes of the newest attempt that recorded any (an earlier attempt's candidate was replaced by it). */
export function changeFacts(records: readonly EvidenceRecord[]): ChangeFact[] {
  const rows = changeRows(records).filter((r) => r.kind !== 'UNAVAILABLE' && r.kind !== 'UNCHANGED');
  const newest = rows.reduce<number | null>((n, r) => (r.attempt !== null && (n === null || r.attempt > n) ? r.attempt : n), null);
  return rows.filter((r) => r.attempt === newest).map((r) => ({ path: r.path, linesAdded: r.linesAdded, linesRemoved: r.linesRemoved, kind: r.kind }));
}

/** "Compile passed" / "Tests passed (53 tests)" / "Tests failed" / "Compile: no result recorded". */
export function describeGate(g: GateFact): string {
  const n = g.tests ? ` (${g.tests.passed !== null && g.tests.passed !== g.tests.cases ? `${g.tests.passed} of ${g.tests.cases}` : g.tests.cases} test${g.tests.cases === 1 ? '' : 's'})` : '';
  return `${gateName(g.type)} ${g.passed === true ? 'passed' : g.passed === false ? 'failed' : 'no result recorded'}${n}`;
}
