/**
 * Gate steps: the recorded gate records grouped into the checks a person would count (Demo V2). Kriya records each workflow gate in two places - the validator's `gate.result` (what ran:
 * exit code, test counts) and the workflow's `mirror.gate_outcome` (which gate of the attempt it was: `targeted_test`, `regression_test`, `run_verification`, ...) - and the runtime
 * check records its own build prerequisite. Showing every record as a line shows one build as "Build passed" twice. The grouping is structural, never a comparison of display strings:
 *
 *  - an outcome belongs to the nearest earlier, unclaimed validator result of the SAME family in the same unit and attempt (the closed FAMILY table below), and the pair is ONE step;
 *  - a validator result no outcome claims stays a step of its own - except a PASSING repeat of the build family the attempt already reported, in an attempt that records outcomes at all (that is a
 *    prerequisite of a later gate, such as the build the runtime check repeats; it adds no new fact). A failure is never folded.
 *
 * Nothing is dropped from the evidence: `sources` lists every record behind a step, and the technical layer still lists each record.
 */
import { gateRows, type GateRow } from './selectors';
import type { EvidenceRecord } from './records';

/** Recorded gate type -> the family a validator result and its outcome share. A type not listed is its own family (never merged with another). */
const FAMILY: Record<string, string> = {
  compile: 'build', test: 'tests', tests: 'tests', targeted_test: 'tests', regression_test: 'tests',
  run_verification: 'runtime', run_verification_hung: 'runtime', runtime_verification: 'runtime',
};
export const familyOf = (type: string): string => FAMILY[type] ?? type;

export interface GateStep {
  /** The first record of the step: where it is placed in the record order. */
  seq: number;
  attempt: number | null;
  unitId: string | null;
  /** The gate type that names the step: the outcome's (the workflow's gate identity) when there is one, else the validator's. */
  type: string;
  family: string;
  /** false when either side recorded a failure; true when every recorded side passed; null when none carries a result. */
  success: boolean | null;
  /** The structured test summary (from the validator result). */
  testSummary: GateRow['testSummary'];
  /** Every gate record behind the step, in record order. */
  sources: number[];
  /** A passing repeat that adds no new fact to the user-level list; still listed under the technical layer. */
  folded: boolean;
}

const unitOf = (r: EvidenceRecord): string | null => { const u = (r as { unit_id?: unknown }).unit_id; return typeof u === 'string' ? u : null; };
const both = (a: boolean | null, b: boolean | null): boolean | null => (a === false || b === false ? false : a === true && b === true ? true : a ?? b);

/** The recorded gate records as steps, in record order (terminal, run-level gates excluded: they are not part of an attempt). */
export function gateSteps(records: readonly EvidenceRecord[]): GateStep[] {
  const ordered = [...records].sort((a, b) => a.seq - b.seq);
  const units = new Map(ordered.map((r) => [r.seq, unitOf(r)] as const));
  const steps: GateStep[] = [];
  const mirroredScopes = new Set<string>();
  for (const row of gateRows(ordered)) if (row.origin === 'mirrored') mirroredScopes.add(`${units.get(row.seq) ?? '-'}|${row.attempt ?? '-'}`);
  const claimed = new Set<GateStep>();
  const alone = new Set<GateStep>();   // validator results no outcome claims (candidates for folding)
  for (const row of gateRows(ordered)) {
    if (row.origin === 'terminal') continue;
    const family = familyOf(row.type);
    const unitId = units.get(row.seq) ?? null;
    if (row.origin === 'mirrored') {
      const open = [...steps].reverse().find((s) => alone.has(s) && !claimed.has(s) && s.family === family && s.attempt === row.attempt && s.unitId === unitId);
      if (open) {
        claimed.add(open);
        open.type = row.type; open.success = both(open.success, row.success); open.sources.push(row.seq);
        continue;
      }
      steps.push({ seq: row.seq, attempt: row.attempt, unitId, type: row.type, family, success: row.success, testSummary: row.testSummary, sources: [row.seq], folded: false });
      continue;
    }
    const step: GateStep = { seq: row.seq, attempt: row.attempt, unitId, type: row.type, family, success: row.success, testSummary: row.testSummary, sources: [row.seq], folded: false };
    steps.push(step);
    alone.add(step);
  }
  // A validator result no outcome claimed, passing, repeating a family the attempt already reported (and the attempt records outcomes at all): a prerequisite of a later gate. A failure stays.
  for (const [i, step] of steps.entries()) {
    if (!alone.has(step) || claimed.has(step) || step.family !== 'build' || step.success !== true || !mirroredScopes.has(`${step.unitId ?? '-'}|${step.attempt ?? '-'}`)) continue;
    step.folded = steps.slice(0, i).some((s) => !s.folded && s.family === step.family && s.attempt === step.attempt && s.unitId === step.unitId);
  }
  return steps;
}
