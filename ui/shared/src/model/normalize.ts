import { validate } from '@kriya-ui/kup';
import { KUP_SCHEMA_VERSION, type KupEnvelope, type RunDetail, type RunEvent, type RunSummary, type Section } from './kup';
import { missingSection } from './availability';

export type EnvelopeCheck =
  | { ok: true; envelope: KupEnvelope }
  | { ok: false; code: 'UNSUPPORTED_SCHEMA_VERSION' | 'INVALID_RESPONSE'; message: string };

/** Accept only a well-formed envelope of a supported schema version (P-27). Never guess a rendering. */
export function checkEnvelope(value: unknown): EnvelopeCheck {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return { ok: false, code: 'INVALID_RESPONSE', message: 'response is not a JSON object' };
  }
  const v = value as Record<string, unknown>;
  if (typeof v.schema_version !== 'number') {
    return { ok: false, code: 'INVALID_RESPONSE', message: 'schema_version missing' };
  }
  if (v.schema_version !== KUP_SCHEMA_VERSION) {
    return { ok: false, code: 'UNSUPPORTED_SCHEMA_VERSION', message: `schema_version ${v.schema_version} is not supported (supported: ${KUP_SCHEMA_VERSION})` };
  }
  // Structural validity comes from the generated schema validator (runtime validation in every host, P-24).
  const checked = validate.envelope(v);
  if (!checked.ok) return { ok: false, code: 'INVALID_RESPONSE', message: `envelope does not match KUP v1: ${checked.errors.slice(0, 3).join('; ')}` };
  return { ok: true, envelope: checked.value as KupEnvelope };
}

/** Parse a stored JSON TEXT column; a value that is not JSON stays the raw string (stored values verbatim, P-25). */
export function parseStoredJson(text: string | null | undefined): { parsed: unknown; raw: string | null; isJson: boolean } {
  if (text === null || text === undefined) return { parsed: null, raw: null, isJson: false };
  try {
    return { parsed: JSON.parse(text), raw: text, isJson: true };
  } catch {
    return { parsed: text, raw: text, isJson: false };
  }
}

/** Kriya's serializer keys (kriya/workflow/run_events.py::RunEvent.to_dict) - the only event shape stored. */
export const RUN_EVENT_KEYS = ['kind', 'attempt', 'source', 'authority', 'message', 'failure_type', 'operation', 'details', 'created_at'] as const;
const KNOWN_EVENT_KEYS = new Set<string>(RUN_EVENT_KEYS);

/** Keys of an event that this UI does not know: shown literally, never dropped. */
export function unknownEventKeys(event: RunEvent): string[] {
  return Object.keys(event).filter((k) => !KNOWN_EVENT_KEYS.has(k)).sort();
}

/** Keys EVERY production gate-outcome writer records (kriya/workflow/failure.py::Failure.to_gate_outcome for each failed gate;
 * dict literals in attempt.py, workflow.py and verification_coordinator.py for successful ones): attempt, type, success, output.
 * The optional keys are the inventoried per-site additions (ui/fixtures/serializer_gates.py). Anything else is unknown: kept and
 * shown literally, never interpreted. No writer records `gate`, `passed` or `name`. */
export const GATE_OUTCOME_COMMON_KEYS = ['attempt', 'type', 'success', 'output'] as const;
export const GATE_OUTCOME_OPTIONAL_KEYS = [
  'mode', 'likely_files', 'file_locations', 'failed_content', 'attempted_edits', 'self_correction_attempt', 'attribution_tier', 'attribution_confidence', 'attribution_reasoning', 'attribution_kind',
  'subtask_id', 'plan_id', 'milestone_id', 'planned_files', 'verification_target', 'authoritative_files', // Failure.to_gate_outcome
  'commands', 'steps', 'managed_service_outcome', 'graded_by', 'deterministic_result', 'runtime_disposition', 'verifier_evidence', // runtime verification sites
  'toolchain_identity', 'egress', 'resources', 'runtime_artifacts', // execution_evidence
  'self_corrected', 'self_correction_turns', 'self_correction_transcript', 'selection_fallback', 'target_test', 'recovered_by', 'deferred_to_future_owner',
  'status', 'reason_code', 'arbitrated_contradictions', 'planner_only_requirements', // goal_spec_compliance
] as const;
const KNOWN_GATE_KEYS = new Set<string>([...GATE_OUTCOME_COMMON_KEYS, ...GATE_OUTCOME_OPTIONAL_KEYS]);
/** The recorded result: `success` is the only documented result field. A record without it is "not recorded"; a non-boolean is
 * shown literally; a legacy `passed` boolean that disagrees with `success` makes the result ambiguous - never resolved by picking one.
 * Nothing is ever inferred from `output` text. */
export type GateResult = 'success' | 'failure' | 'not_recorded' | 'not_boolean' | 'ambiguous';
export const GATE_RESULT_LABEL: Record<GateResult, string> = { success: 'success', failure: 'failure', not_recorded: 'result not recorded', not_boolean: 'result not a boolean', ambiguous: 'result ambiguous (conflicting fields)' };
export interface GateOutcomeView {
  record: Record<string, unknown> | null; attempt: unknown; type: string | null; result: GateResult; success: unknown; output: string | null;
  status: string | null; reason_code: string | null; graded_by: string | null; deterministic_result: string | null;
  conflicts: { field: string; value: unknown }[]; unknownKeys: string[];
}
export function gateOutcomeView(value: unknown): GateOutcomeView {
  const str = (v: unknown) => (typeof v === 'string' ? v : null);
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return { record: null, attempt: undefined, type: null, result: 'not_recorded', success: undefined, output: null, status: null, reason_code: null, graded_by: null, deterministic_result: null, conflicts: [], unknownKeys: [] };
  const o = value as Record<string, unknown>;
  const conflicts: { field: string; value: unknown }[] = [];
  let result: GateResult;
  if (!('success' in o)) result = 'not_recorded';
  else if (typeof o.success !== 'boolean') result = 'not_boolean';
  else {
    if (typeof o.passed === 'boolean' && o.passed !== o.success) conflicts.push({ field: 'passed', value: o.passed });
    result = conflicts.length ? 'ambiguous' : o.success ? 'success' : 'failure';
  }
  return { record: o, attempt: o.attempt, type: str(o.type), result, success: o.success, output: str(o.output), status: str(o.status), reason_code: str(o.reason_code), graded_by: str(o.graded_by), deterministic_result: str(o.deterministic_result), conflicts, unknownKeys: Object.keys(o).filter((k) => !KNOWN_GATE_KEYS.has(k)).sort() };
}

/** created_at is recorded as Unix epoch seconds (time.time()); render it as UTC, labelled, never as local time. */
export function formatEventTime(createdAt: unknown): string {
  if (typeof createdAt !== 'number' || !Number.isFinite(createdAt)) return 'time not recorded';
  const d = new Date(createdAt * 1000);
  return Number.isNaN(d.getTime()) ? 'time not recorded' : `${d.toISOString().replace('T', ' ').replace('Z', '')} UTC`;
}

/** The event's `details` as an object, or an empty object when details are absent or not an object. */
export function eventDetails(event: RunEvent): Record<string, unknown> {
  const d = (event as { details?: unknown }).details;
  return typeof d === 'object' && d !== null && !Array.isArray(d) ? (d as Record<string, unknown>) : {};
}

export function asSection<T>(value: unknown): Section<T> {
  if (typeof value === 'object' && value !== null && 'availability' in (value as object)) {
    return value as Section<T>;
  }
  return missingSection('section absent from the response');
}

/** Fill every optional section of a detail payload so panels can always ask "recorded?" (D-5). */
export function normalizeDetail(data: unknown): RunDetail | null {
  if (typeof data !== 'object' || data === null) return null;
  const d = data as Record<string, unknown>;
  const run = d.run as RunSummary | undefined;
  if (!run || typeof run.run_id !== 'string') return null;
  const section = <T,>(key: string): Section<T> => asSection<T>(d[key]);
  return {
    ...d,
    run,
    fields: (d.fields as Record<string, Section<string>> | undefined) ?? {},
    run_events: section('run_events'),
    evidence_records: section('evidence_records'),
    gate_outcomes: section('gate_outcomes'),
    model_hops: section('model_hops'),
    generation_metrics: section('generation_metrics'),
    failure_report: section('failure_report'),
    context: section('context'),
    attribution: section('attribution'),
    diagnostics: section('diagnostics'),
    comparisons: section('comparisons'),
    output: section('output'),
  } as RunDetail;
}

/** Group recorded events by attempt, preserving recorded order (P-30: no fabricated stage completion). */
export function eventsByAttempt(events: RunEvent[]): Map<string, RunEvent[]> {
  const groups = new Map<string, RunEvent[]>();
  for (const event of events) {
    const key = typeof event.attempt === 'number' ? `attempt ${event.attempt}` : 'no attempt recorded';
    const list = groups.get(key) ?? [];
    list.push(event);
    groups.set(key, list);
  }
  return groups;
}
