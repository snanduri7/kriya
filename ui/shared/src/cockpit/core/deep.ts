/**
 * Narrow projections for the deep-inspection modules (Context, Plan, Attempts, Evidence). Everything is read from what the recorder and the trace row
 * persisted, verbatim; a field that is not recorded is null or absent, never inferred. The shapes below are those MEASURED on real stores (and the
 * producers in kriya/core/attempt_evidence/scope.py), not the design documents'.
 */
import type { RunDetail } from '../../model/kup';
import { ofKind, payloadOf, type EvidenceRecord } from './records';
import { gateRows, recoveries, fallbacks, type FallbackFacts, type RecoveryFacts } from './selectors';

const str = (v: unknown): string | null => (typeof v === 'string' && v.length ? v : null);
const num = (v: unknown): number | null => (typeof v === 'number' && Number.isFinite(v) ? v : null);
const bool = (v: unknown): boolean | null => (typeof v === 'boolean' ? v : null);
const obj = (v: unknown): Record<string, unknown> | null => (v !== null && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
const list = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);
const strings = (v: unknown): string[] => list(v).filter((x): x is string => typeof x === 'string');

// ------------------------------------------------------------------ S1: the trace row's run events and evidence records

export interface S1Event { kind: string; attempt: number | null; source: string | null; authority: string | null; message: string | null; details: Record<string, unknown> }

/** The trace row's run_events as recorded; null when the section is not recorded (distinct from "recorded, no events"). */
export function runEventsOf(detail: RunDetail | null): S1Event[] | null {
  const section = detail?.run_events;
  if (!section || section.availability !== 'recorded' || !Array.isArray(section.data)) return null;
  return section.data.map((raw) => {
    const e = obj(raw) ?? {};
    return { kind: str(e.kind) ?? '(kind not recorded)', attempt: num(e.attempt), source: str(e.source), authority: str(e.authority), message: str(e.message), details: obj(e.details) ?? {} };
  });
}

export function activeSkills(detail: RunDetail | null): string[] | null {
  const section = detail?.evidence_records;
  if (!section || section.availability !== 'recorded' || !Array.isArray(section.data)) return null;
  const record = section.data.map(obj).find((r) => r?.kind === 'active_skills');
  return record ? strings(obj(record.payload)?.skills) : null;
}

// ------------------------------------------------------------------ Context: what a model invocation saw

export interface CallRow {
  /** The model.result record (the logical call's outcome). */
  seq: number;
  attempt: number | null;
  unitId: string | null;
  role: string | null;
  callSeq: number | null;
  model: string | null;
  runtimeFingerprint: string | null;
  finishReason: string | null;
  backendStatus: string | null;
  backendError: string | null;
  elapsedSeconds: number | null;
  maxTokens: number | null;
  /** Estimated by Kriya vs reported by the provider: distinct facts, never merged. */
  promptTokensEstimated: number | null;
  promptTokensReported: number | null;
  completionTokens: number | null;
  requiredTokens: number | null;
  selectedContextWindow: number | null;
  servedContextWindow: number | null;
  servedContextProvenance: string | null;
  /** The model.request record of the same call, when the wire request was recorded; and which parts it kept as bytes or only as digests. */
  requestSeq: number | null;
  requestKept: readonly string[];
  requestDigestOnly: readonly string[];
}

export function callRows(records: readonly EvidenceRecord[]): CallRow[] {
  const requests = ofKind(records, 'model.request');
  return ofKind(records, 'model.result').map((r) => {
    const p = payloadOf(r);
    const budget = obj(p.budget) ?? {};
    const state = obj(budget.context_state) ?? {};
    const request = requests.find((q) => q.call_seq === r.call_seq && q.call_seq != null);
    const kept = Object.keys(request?.blobs ?? {});
    return {
      seq: r.seq, attempt: r.attempt_number ?? null, unitId: r.unit_id ?? null, role: r.role ?? null, callSeq: r.call_seq ?? null, model: str(p.model), runtimeFingerprint: str(p.runtime_fingerprint),
      finishReason: str(p.finish_reason), backendStatus: str(p.backend_status), backendError: str(p.backend_error), elapsedSeconds: num(p.elapsed_seconds), maxTokens: num(p.max_tokens),
      promptTokensEstimated: num(p.prompt_tokens), promptTokensReported: num(p.prompt_tokens_reported), completionTokens: num(p.completion_tokens), requiredTokens: num(budget.required_tokens),
      selectedContextWindow: num(budget.selected_context_window), servedContextWindow: num(state.served_context_window), servedContextProvenance: str(state.served_context_provenance),
      requestSeq: request?.seq ?? null, requestKept: kept, requestDigestOnly: Object.keys(request?.content_digests ?? {}).filter((k) => !kept.includes(k)),
    };
  });
}

export interface PromptSegment { name: string; chars: number | null; digest: string | null; mandatory: boolean | null; outcome: string | null; offset: number | null }
export type PromptSections =
  | { kind: 'request'; seq: number; attempt: number | null; request: string | null; capacityTokens: number | null; requestTokens: number | null; fitted: boolean | null; segments: PromptSegment[]; optionalUnchanged: string[]; unlocated: string[] }
  | { kind: 'variable'; seq: number; attempt: number | null; section: string | null; builds: number | null; keptChars: number | null; keptDigest: string | null; omitted: boolean | null; roomTokens: number | null; usedTokens: number | null };

export function promptSections(records: readonly EvidenceRecord[]): PromptSections[] {
  return ofKind(records, 'prompt.sections').map((r) => {
    const p = payloadOf(r);
    const attempt = r.attempt_number ?? null;
    if (p.fitter === 'fit_variable_section') return { kind: 'variable' as const, seq: r.seq, attempt, section: str(p.section), builds: num(p.builds), keptChars: num(p.kept_chars), keptDigest: str(p.kept_digest), omitted: bool(p.omitted), roomTokens: num(p.room_tokens), usedTokens: num(p.used_tokens) };
    return {
      kind: 'request' as const, seq: r.seq, attempt, request: str(p.request), capacityTokens: num(p.capacity_tokens), requestTokens: num(p.request_tokens), fitted: bool(p.fitted),
      segments: list(p.segments).map(obj).filter((s): s is Record<string, unknown> => s !== null).map((s) => ({ name: str(s.name) ?? '(unnamed)', chars: num(s.chars), digest: str(s.digest), mandatory: bool(s.mandatory), outcome: str(s.outcome), offset: num(s.offset) })),
      optionalUnchanged: strings(p.optional_sections_unchanged), unlocated: strings(p.unlocated_sections),
    };
  });
}

export interface ContextFacts {
  knownTargets: { files: string[]; tiers: Array<{ path: string; memberId: string | null; tier: string | null }>; omitted: Array<{ path: string; reason: string | null; estimatedTokens: number | null; rank: number | null }>; packageHash: string | null } | null;
  editCapability: { model: string | null; budgetChars: number | null; targets: Array<{ path: string; operations: string[]; fullFile: boolean | null }> } | null;
  composition: Record<string, number | string> | null;
}

/** The recorded context events of one attempt (S1 run events). Absent events stay null. */
export function contextFacts(events: readonly S1Event[] | null, attempt: number | null): ContextFacts {
  const mine = (kind: string) => (events ?? []).filter((e) => e.kind === kind && (attempt === null || e.attempt === attempt)).pop() ?? null;
  const known = mine('context.known_target_package');
  const edit = mine('context.edit_capability');
  const comp = mine('developer.prompt_composition');
  return {
    knownTargets: known ? {
      files: strings(known.details.known_target_files),
      tiers: list(known.details.tiers).map(obj).filter((t): t is Record<string, unknown> => t !== null).map((t) => ({ path: str(t.path) ?? '(path not recorded)', memberId: str(t.member_id), tier: str(t.tier) })),
      omitted: list(known.details.omitted).map(obj).filter((o): o is Record<string, unknown> => o !== null).map((o) => ({ path: str(o.path) ?? '(path not recorded)', reason: str(o.reason), estimatedTokens: num(o.estimated_tokens), rank: num(o.rank) })),
      packageHash: str(known.details.package_hash),
    } : null,
    editCapability: edit ? {
      model: str(edit.details.model), budgetChars: num(edit.details.budget_chars),
      targets: list(edit.details.targets).map(obj).filter((t): t is Record<string, unknown> => t !== null).map((t) => ({ path: str(t.path) ?? '(path not recorded)', operations: strings(t.operations), fullFile: bool(t.full_file) })),
    } : null,
    composition: comp ? Object.fromEntries(Object.entries(comp.details).filter(([k, v]) => (k.endsWith('_tokens') || k === 'token_counts') && (typeof v === 'number' || typeof v === 'string')) as Array<[string, number | string]>) : null,
  };
}

// ------------------------------------------------------------------ Plan: only what is durably recorded

export interface UnitRow {
  seq: number;
  unitId: string;
  unitKind: string | null;
  invocationSeq: number | null;
  planId: string | null;
  planFingerprint: string | null;
  plannedFiles: string[];
  authorizedWriteScope: string[] | null;
  writeScopeMode: string | null;
  currentSubtaskId: string | null;
  milestoneGroupId: string | null;
  milestoneIndex: number | null;
  resume: boolean | null;
  closed: { seq: number; outcome: string | null; failureCategory: string | null; qualityGatesPassed: boolean | null } | null;
}

export function planUnits(records: readonly EvidenceRecord[]): UnitRow[] {
  const closes = ofKind(records, 'unit.closed');
  const used = new Set<number>();
  return ofKind(records, 'unit.opened').map((r) => {
    const p = payloadOf(r);
    const unitId = r.unit_id ?? str(p.unit_id) ?? '(unit not recorded)';
    const close = closes.find((c) => c.unit_id === r.unit_id && c.seq > r.seq && !used.has(c.seq));
    if (close) used.add(close.seq);
    const cp = close ? payloadOf(close) : null;
    return {
      seq: r.seq, unitId, unitKind: r.unit_kind ?? null, invocationSeq: r.invocation_seq ?? num(p.invocation_seq), planId: str(p.plan_id), planFingerprint: str(p.plan_fingerprint), plannedFiles: strings(p.planned_source_files),
      authorizedWriteScope: Array.isArray(p.authorized_write_scope) ? strings(p.authorized_write_scope) : null, writeScopeMode: str(p.write_scope_mode), currentSubtaskId: str(p.current_subtask_id),
      milestoneGroupId: str(p.milestone_group_id), milestoneIndex: num(p.milestone_index), resume: bool(p.resume),
      closed: close && cp ? { seq: close.seq, outcome: str(cp.outcome), failureCategory: str(cp.failure_category), qualityGatesPassed: bool(cp.quality_gates_passed) } : null,
    };
  });
}

export interface TargetRow { path: string; operations: string[]; requestedOperation: string | null; inWriteScope: boolean | null; wholeFileAuthority: boolean | null; shownTier: string | null; shownIsExact: boolean | null; loci: number; spans: number }

/** The authority snapshot of one attempt (the last one it recorded): the files the invocation was allowed to change and how. */
export function authorityTargets(records: readonly EvidenceRecord[], attempt: number | null): { seq: number; writeScopeMode: string | null; writeScope: string[]; targets: TargetRow[] } | null {
  const snap = ofKind(records, 'authority.snapshot').filter((r) => attempt === null || r.attempt_number === attempt).pop();
  if (!snap) return null;
  const p = payloadOf(snap);
  return {
    seq: snap.seq, writeScopeMode: str(p.write_scope_mode), writeScope: strings(p.authorized_write_scope),
    targets: list(p.targets).map(obj).filter((t): t is Record<string, unknown> => t !== null).map((t) => ({
      path: str(t.path) ?? '(path not recorded)', operations: strings(t.operations), requestedOperation: str(t.requested_operation), inWriteScope: bool(t.in_write_scope), wholeFileAuthority: bool(t.whole_file_authority),
      shownTier: str(t.shown_tier), shownIsExact: bool(t.shown_is_exact), loci: list(t.loci).length, spans: list(t.spans).length,
    })),
  };
}

export interface RequirementsFacts {
  derived: Array<{ id: string; kind: string | null; source: string | null; text: string | null }> | null;
  lineage: Array<{ stage: string | null; cited: string[]; omitted: string[] }>;
  planEvents: Array<{ kind: string; reasonCodes: string[]; classification: string | null; accepted: boolean | null; valid: boolean | null; repairAttempt: number | null; message: string | null }>;
}

export function requirementsFacts(events: readonly S1Event[] | null): RequirementsFacts {
  const all = events ?? [];
  const derived = all.find((e) => e.kind === 'requirement.derived');
  return {
    derived: derived ? list(derived.details.requirements).map(obj).filter((r): r is Record<string, unknown> => r !== null).map((r) => ({ id: str(r.id) ?? '(id not recorded)', kind: str(r.kind), source: str(r.source), text: str(r.text) })) : null,
    lineage: all.filter((e) => e.kind === 'requirement.lineage').map((e) => ({ stage: str(e.details.stage), cited: strings(e.details.cited), omitted: strings(e.details.omitted) })),
    planEvents: all.filter((e) => e.kind.startsWith('structured_plan_')).map((e) => ({ kind: e.kind, reasonCodes: strings(e.details.reason_codes), classification: str(e.details.classification), accepted: bool(e.details.accepted), valid: bool(e.details.valid), repairAttempt: num(e.details.repair_attempt ?? e.details.repair_attempts), message: e.message })),
  };
}

export function obligations(records: readonly EvidenceRecord[], attempt: number | null): { seq: number; ledgerRevision: number | null; ledgerDigest: string | null; unresolvedTerminal: number } | null {
  const r = ofKind(records, 'obligations.snapshot').filter((x) => attempt === null || x.attempt_number === attempt).pop();
  if (!r) return null;
  const p = payloadOf(r);
  return { seq: r.seq, ledgerRevision: num(p.ledger_revision), ledgerDigest: str(p.ledger_digest), unresolvedTerminal: Object.keys(obj(p.unresolved_terminal) ?? {}).length };
}

// ------------------------------------------------------------------ Attempts: the table and the A/B comparison

export interface RetryDelta { seq: number; previousAttempt: number | null; attempt: number | null; informationGain: string | null; changed: string[]; dimensions: Record<string, { previous: unknown; current: unknown }>; requestDigests: { previous: string | null; current: string | null } | null; reason: string | null }

export function retryDeltas(records: readonly EvidenceRecord[]): RetryDelta[] {
  return ofKind(records, 'retry.delta').map((r) => {
    const p = payloadOf(r);
    const dims = obj(p.dimensions) ?? {};
    const digests = obj(p.request_digests);
    return {
      seq: r.seq, previousAttempt: num(p.previous_attempt), attempt: num(p.attempt) ?? r.attempt_number ?? null, informationGain: str(p.information_gain), changed: strings(p.changed),
      dimensions: Object.fromEntries(Object.entries(dims).map(([k, v]) => [k, { previous: obj(v)?.previous ?? null, current: obj(v)?.current ?? null }])),
      requestDigests: digests ? { previous: str(digests.previous), current: str(digests.current) } : null, reason: str(p.reason),
    };
  });
}

export interface AttemptRow {
  attempt: number;
  unitId: string | null;
  mode: string | null;
  operation: string | null;
  reservedFallback: boolean | null;
  calls: CallRow[];
  models: string[];
  candidateFiles: string[];
  gates: { passed: number; failed: number; unknown: number };
  retryDelta: RetryDelta | null;
  recovery: RecoveryFacts[];
  fallback: FallbackFacts[];
  concluded: boolean | null;
  closedOutcome: string | null;
  candidateGatesPassed: boolean | null;
}

export function attemptRows(records: readonly EvidenceRecord[]): AttemptRow[] {
  const numbers = [...new Set(records.map((r) => r.attempt_number).filter((n): n is number => typeof n === 'number'))].sort((a, b) => a - b);
  const calls = callRows(records);
  const gates = gateRows(records);
  const deltas = retryDeltas(records);
  const rec = recoveries(records);
  const fb = fallbacks(records);
  return numbers.map((n) => {
    const opened = ofKind(records, 'attempt.opened').find((r) => r.attempt_number === n);
    const closed = ofKind(records, 'attempt.closed').find((r) => r.attempt_number === n);
    const concluded = ofKind(records, 'attempt.concluded').find((r) => r.attempt_number === n);
    const op = opened ? payloadOf(opened) : {};
    const mine = calls.filter((c) => c.attempt === n);
    const files = ofKind(records, 'candidate.change').filter((r) => r.attempt_number === n && payloadOf(r).decision === 'STAGED').map((r) => str(payloadOf(r).path)).filter((x): x is string => x !== null);
    const g = gates.filter((x) => x.attempt === n);
    return {
      attempt: n, unitId: (opened ?? closed ?? concluded)?.unit_id ?? null, mode: str(op.mode), operation: str(op.operation), reservedFallback: bool(op.reserved_fallback), calls: mine,
      models: [...new Set(mine.map((c) => c.model).filter((m): m is string => m !== null))], candidateFiles: [...new Set(files)],
      gates: { passed: g.filter((x) => x.success === true).length, failed: g.filter((x) => x.success === false).length, unknown: g.filter((x) => x.success === null).length },
      retryDelta: deltas.find((d) => d.attempt === n) ?? null, recovery: rec.filter((x) => x.attempt === n), fallback: fb.filter((x) => x.attempt === n),
      concluded: concluded ? bool(payloadOf(concluded).succeeded) : null, closedOutcome: closed ? str(payloadOf(closed).outcome) : null, candidateGatesPassed: closed ? bool(payloadOf(closed).candidate_gates_passed) : null,
    };
  });
}

export interface ComparisonDimension { id: string; label: string; a: string | null; b: string | null; same: boolean | null; source: string }

const show = (v: unknown): string | null => (v === null || v === undefined ? null : typeof v === 'string' ? v : JSON.stringify(v));
const join = (xs: readonly string[]) => (xs.length ? xs.join(', ') : '(none)');

/** A and B side by side from recorded evidence only. A dimension one side did not record is null: shown as Not recorded, never as "same". */
export function compareAttempts(records: readonly EvidenceRecord[], a: number, b: number): { dimensions: ComparisonDimension[]; delta: RetryDelta | null } {
  const rows = attemptRows(records);
  const A = rows.find((r) => r.attempt === a) ?? null;
  const B = rows.find((r) => r.attempt === b) ?? null;
  const sections = promptSections(records).filter((s): s is Extract<PromptSections, { kind: 'request' }> => s.kind === 'request');
  const sectionOf = (n: number) => sections.filter((s) => s.attempt === n).pop() ?? null;
  const dim = (id: string, label: string, av: unknown, bv: unknown, source: string): ComparisonDimension => {
    const x = show(av), y = show(bv);
    return { id, label, a: x, b: y, same: x === null || y === null ? null : x === y, source };
  };
  const callOf = (r: AttemptRow | null) => r?.calls.find((c) => c.role === 'developer') ?? r?.calls[0] ?? null;
  const delta = retryDeltas(records).find((d) => (d.attempt === b && d.previousAttempt === a) || (d.attempt === a && d.previousAttempt === b)) ?? null;
  const segmentDigests = (n: number) => { const s = sectionOf(n); return s ? s.segments.map((x) => `${x.name}:${x.outcome ?? '?'}:${x.digest ?? '-'}`).join(' | ') : null; };
  const dimensions: ComparisonDimension[] = [
    dim('model', 'Model', A ? join(A.models) : null, B ? join(B.models) : null, 'model.result'),
    dim('runtime', 'Runtime fingerprint', callOf(A)?.runtimeFingerprint, callOf(B)?.runtimeFingerprint, 'model.result'),
    dim('mode', 'Attempt mode / operation', A ? `${A.mode ?? '-'} / ${A.operation ?? '-'}` : null, B ? `${B.mode ?? '-'} / ${B.operation ?? '-'}` : null, 'attempt.opened'),
    dim('prompt_sections', 'Prompt sections (name:outcome:digest)', segmentDigests(a), segmentDigests(b), 'prompt.sections'),
    dim('request_tokens', 'Request tokens (estimated) / capacity', sectionOf(a) ? `${sectionOf(a)!.requestTokens ?? '-'} / ${sectionOf(a)!.capacityTokens ?? '-'}` : null, sectionOf(b) ? `${sectionOf(b)!.requestTokens ?? '-'} / ${sectionOf(b)!.capacityTokens ?? '-'}` : null, 'prompt.sections'),
    dim('reported_tokens', 'Prompt tokens reported by the provider', callOf(A)?.promptTokensReported, callOf(B)?.promptTokensReported, 'model.result'),
    dim('changed_files', 'Files changed (staged)', A ? join(A.candidateFiles) : null, B ? join(B.candidateFiles) : null, 'candidate.change'),
    dim('gates', 'Gates (passed / failed / no result)', A ? `${A.gates.passed} / ${A.gates.failed} / ${A.gates.unknown}` : null, B ? `${B.gates.passed} / ${B.gates.failed} / ${B.gates.unknown}` : null, 'gate.result, mirror.gate_outcome'),
    dim('recovery', 'Recovery decision', A ? join(A.recovery.map((r) => `${r.action ?? '?'}${r.retry === null ? '' : r.retry ? ' (retry)' : ' (no retry)'}`)) : null, B ? join(B.recovery.map((r) => `${r.action ?? '?'}${r.retry === null ? '' : r.retry ? ' (retry)' : ' (no retry)'}`)) : null, 'recovery.decision'),
    dim('fallback', 'Model fallback', A ? join(A.fallback.map((f) => (f.fell_back ? `fell back to ${f.selected ?? '?'}` : 'none'))) : null, B ? join(B.fallback.map((f) => (f.fell_back ? `fell back to ${f.selected ?? '?'}` : 'none'))) : null, 'fallback.decision'),
    dim('outcome', 'Outcome', A ? `${A.concluded === null ? 'no conclusion recorded' : A.concluded ? 'succeeded' : 'failed'}${A.closedOutcome ? ` (closed ${A.closedOutcome})` : ''}` : null, B ? `${B.concluded === null ? 'no conclusion recorded' : B.concluded ? 'succeeded' : 'failed'}${B.closedOutcome ? ` (closed ${B.closedOutcome})` : ''}` : null, 'attempt.concluded, attempt.closed'),
  ];
  return { dimensions, delta };
}
