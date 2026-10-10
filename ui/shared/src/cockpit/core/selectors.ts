/**
 * Narrow, typed projections of the recorded evidence that more than one module reads. Each is a pure function of the records (and
 * nothing else); modules render their own view of them. There is deliberately no single "run model": a module asks only for the
 * projection it needs. Everything here reads recorded fields verbatim; a field that is not recorded is null, never inferred.
 */
import { ofKind, payloadOf, type EvidenceRecord } from './records';

const str = (v: unknown): string | null => (typeof v === 'string' && v.length ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
const obj = (v: unknown): Record<string, unknown> | null => (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);

/** Central tabs other modules may point the user to (a navigation contract by selection value, never an import). */
export const OPEN_TARGETS = { validation: 'validation', changes: 'changes', evidence: 'evidence' } as const;

export function attemptNumbers(records: readonly EvidenceRecord[]): number[] {
  return [...new Set(records.map((r) => r.attempt_number).filter((n): n is number => typeof n === 'number'))].sort((a, b) => a - b);
}

/** The attempt a document shows: the selected one, else the highest attempt that has a record of one of `kinds`. */
export function attemptFor(records: readonly EvidenceRecord[], selected: string | null, kinds: readonly string[]): number | null {
  if (selected !== null && Number.isInteger(Number(selected))) return Number(selected);
  const numbers = records.filter((r) => kinds.includes(r.kind) && typeof r.attempt_number === 'number').map((r) => r.attempt_number as number);
  return numbers.length ? Math.max(...numbers) : null;
}

// ------------------------------------------------------------------ gates

export type GateOrigin = 'validator' | 'terminal' | 'mirrored';

/** The selection key of one recorded gate record. It includes the record's seq because one attempt can record the same gate type more than once
 * (a validator gate.result and the mirrored outcome of the same gate, or a repeated gate), and a selection must name exactly one record. */
export const gateKey = (attempt: number | null, type: string, seq: number): string => `${attempt ?? '-'}:${type}@${seq}`;

export interface GateRow {
  seq: number;
  /** null: a run-level gate (terminal gates are recorded outside any attempt). */
  attempt: number | null;
  /** The selection key (gateKey): attempt, recorded type and the record's seq (`-` for a run-level gate). */
  id: string;
  /** The gate type EXACTLY as recorded (gate.result.gate or mirror.gate_outcome.type). */
  type: string;
  success: boolean | null;
  origin: GateOrigin;
  index: number | null;
  exitCode: number | null;
  durationSeconds: number | null;
  timedOut: boolean | null;
  /** The structured test summary, only when the recorder persisted one (gate.result.test_execution). */
  testSummary: Record<string, unknown> | null;
  /** Whether the recorder kept the gate's output as content (shown only through an authorized content API; not part of M3). */
  outputKept: boolean;
}

/** Gate evidence in recorded order: validator/terminal gate.result records and the trace row's mirrored gate outcomes. */
export function gateRows(records: readonly EvidenceRecord[]): GateRow[] {
  const rows: GateRow[] = [];
  for (const r of records) {
    if (r.kind !== 'gate.result' && r.kind !== 'mirror.gate_outcome') continue;
    const p = payloadOf(r);
    const mirrored = r.kind === 'mirror.gate_outcome';
    const type = str(mirrored ? p.type : p.gate) ?? '(gate type not recorded)';
    const attempt = r.attempt_number ?? null;
    rows.push({
      seq: r.seq, attempt, id: gateKey(attempt, type, r.seq), type, success: bool(p.success), origin: mirrored ? 'mirrored' : p.stage === 'terminal' ? 'terminal' : 'validator',
      index: num(p.index), exitCode: num(p.exit_code), durationSeconds: num(p.duration_seconds), timedOut: bool(p.timed_out),
      testSummary: obj(p.test_execution), outputKept: Object.keys(r.blobs ?? {}).length > 0,
    });
  }
  return rows;
}

export const firstFailedGate = (rows: readonly GateRow[]): GateRow | null => rows.find((g) => g.success === false) ?? null;

// ------------------------------------------------------------------ candidate changes

export type ChangeKind = 'ADDED' | 'MODIFIED' | 'DELETED' | 'UNCHANGED' | 'UNAVAILABLE';

export interface ChangeRow {
  seq: number;
  attempt: number | null;
  path: string;
  kind: ChangeKind;
  decision: string | null;
  reasonCode: string | null;
  beforeDigest: string | null;
  afterDigest: string | null;
  linesAdded: number | null;
  linesRemoved: number | null;
  /** Content parts the recorder kept as retrievable bytes / only as a digest (the record is the authority on why content is absent). */
  kept: readonly string[];
  digestOnly: readonly string[];
}

/** Generic change semantics from the recorded payload: no assumption that a baseline or the file pre-existed, nothing guessed. */
export function changeRows(records: readonly EvidenceRecord[]): ChangeRow[] {
  return ofKind(records, 'candidate.change').map((r) => {
    const p = payloadOf(r);
    const kept = Object.keys(r.blobs ?? {});
    const digestOnly = Object.keys(r.content_digests ?? {}).filter((k) => !kept.includes(k));
    const common = { seq: r.seq, attempt: r.attempt_number ?? null, path: str(p.path) ?? '(path not recorded)', decision: str(p.decision), reasonCode: str(p.reason_code), kept, digestOnly,
      beforeDigest: str(p.before_digest), afterDigest: str(p.after_digest), linesAdded: num(p.lines_added), linesRemoved: num(p.lines_removed) };
    if (p.decision !== 'STAGED') return { ...common, kind: 'UNAVAILABLE' as const, beforeDigest: null, afterDigest: null, linesAdded: null, linesRemoved: null };
    let kind: ChangeKind = 'UNAVAILABLE';
    if (p.unchanged === true) kind = 'UNCHANGED';
    else if (p.deleted === true) kind = 'DELETED';
    else if (p.created === true) kind = 'ADDED';
    else if (typeof p.before_digest === 'string') kind = 'MODIFIED';
    return { ...common, kind };
  });
}

// ------------------------------------------------------------------ why: diagnosis, recovery, fallback

export type EvidenceClass = 'MEASURED' | 'DERIVED_DETERMINISTIC' | 'MODEL_CLAIMED' | 'UNKNOWN';
const CLASSES: readonly string[] = ['MEASURED', 'DERIVED_DETERMINISTIC', 'MODEL_CLAIMED', 'UNKNOWN'];

export interface DiagnosisFacts {
  seq: number;
  attempt: number | null;
  type: string | null;
  reasonCode: string | null;
  attributionKind: string | null;
  attributionTier: string | null;
  attributionConfidence: string | null;
  likelyFiles: readonly string[];
  /** As recorded; null when the record carries none (never defaulted). */
  evidenceClass: EvidenceClass | null;
  /** The record's own provenance (OBSERVED, DERIVED, MODEL_CLAIMED, MIRRORED...), distinct from the evidence class. */
  provenance: string;
  messageKept: boolean;
}

export interface RecoveryFacts {
  seq: number;
  attempt: number | null;
  action: string | null;
  retry: boolean | null;
  reason: string | null;
  stopReasonCode: string | null;
  progress: string | null;
  failureType: string | null;
}

export interface FallbackFacts { seq: number; attempt: number | null; phase: string | null; fell_back: boolean | null; requested: string | null; selected: string | null }

export function diagnoses(records: readonly EvidenceRecord[]): DiagnosisFacts[] {
  return ofKind(records, 'diagnosis').map((r) => {
    const p = payloadOf(r);
    const cls = str(p.evidence_class);
    return {
      seq: r.seq, attempt: r.attempt_number ?? num(p.attempt), type: str(p.type), reasonCode: str(p.reason_code), attributionKind: str(p.attribution_kind), attributionTier: str(p.attribution_tier),
      attributionConfidence: str(p.attribution_confidence), likelyFiles: Array.isArray(p.likely_files) ? p.likely_files.filter((f): f is string => typeof f === 'string') : [],
      evidenceClass: cls !== null && CLASSES.includes(cls) ? (cls as EvidenceClass) : cls === null ? null : 'UNKNOWN', provenance: r.provenance, messageKept: 'message' in (r.blobs ?? {}),
    };
  });
}

export function recoveries(records: readonly EvidenceRecord[]): RecoveryFacts[] {
  return ofKind(records, 'recovery.decision').map((r) => {
    const p = payloadOf(r);
    const decision = obj(p.retry_decision);
    return { seq: r.seq, attempt: r.attempt_number ?? num(p.attempt), action: str(p.action), retry: bool(p.retry), reason: str(decision?.reason), stopReasonCode: str(p.stop_reason_code), progress: str(p.progress_classification), failureType: str(p.failure_type) };
  });
}

export function fallbacks(records: readonly EvidenceRecord[]): FallbackFacts[] {
  return ofKind(records, 'fallback.decision').map((r) => {
    const p = payloadOf(r);
    return { seq: r.seq, attempt: r.attempt_number ?? null, phase: str(p.phase), fell_back: bool(p.fallback), requested: str(p.requested), selected: str(p.selected) };
  });
}

export interface AttemptOutcome { attempt: number; concluded: boolean | null; closedOutcome: string | null }

export function attemptOutcomes(records: readonly EvidenceRecord[]): AttemptOutcome[] {
  return attemptNumbers(records).map((attempt) => {
    const concluded = ofKind(records, 'attempt.concluded').find((r) => r.attempt_number === attempt);
    const closed = ofKind(records, 'attempt.closed').find((r) => r.attempt_number === attempt);
    return { attempt, concluded: concluded ? bool(payloadOf(concluded).succeeded) : null, closedOutcome: closed ? str(payloadOf(closed).outcome) : null };
  });
}

export function runClosure(records: readonly EvidenceRecord[]): { terminalStatus: string | null; present: boolean } {
  const r = ofKind(records, 'run.closed')[0];
  return r ? { terminalStatus: str(payloadOf(r).terminal_status), present: true } : { terminalStatus: null, present: false };
}
