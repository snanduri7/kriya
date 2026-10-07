#!/usr/bin/env node
/**
 * kup-evidence-check: offline structural-consistency check of EXPLICITLY SUPPLIED KUP v1 JSON files.
 *
 * Data-quality observations only. Nothing here is a workflow gate verdict, changes a Kriya verdict, reconstructs
 * missing evidence or infers a failure cause, a timeline, success or qualification. Every diagnostic carries a stable
 * rule id, a classification (structural_error | consistency_conflict | unresolved_reference | ambiguity |
 * informational), the input file and JSON pointer, the observed value (absent, null and unavailable sections kept
 * distinct), related pointers, a plain explanation and the rule's evidence basis (the schema or serializer it rests
 * on). Rules exist only where a contract or serializer documents the invariant (RULES below); useful checks without
 * one are listed as not checked (UNSUPPORTED_CHECKS), never strengthened silently.
 *
 * Built on tools/run_report.mjs (generated validators, JSON pointers, Markdown cell escaping, sorted output) and the
 * KUP schemas. Reads only the files named on the command line (no directory walk, no store, no database, no snapshot
 * acquisition, no Kriya, no network); writes only evidence-check.json and evidence-check.md into the explicit --out
 * directory, refusing an existing output without --overwrite and never writing onto an input. Deterministic: same
 * inputs, byte-identical outputs (sorted keys, stable diagnostic order, no clock).
 *
 * Exit codes: 0 completed, no actionable diagnostic (informational only); 1 completed, actionable diagnostics
 * written (a malformed input is one of them, visible in coverage and diagnostics); 2 usage or output-write failure
 * (nothing written).
 *
 * usage: node tools/evidence_check.mjs --out <dir> [--overwrite] <kup-envelope.json>...
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as validators from '../generated/validators.mjs';
import { DETAIL_SECTIONS, EVENT_KEYS, GATE_OUTCOME_KEYS, PROMPT_COMPOSITION_FIELDS, cell, classifyGateRecord, pointer, stableStringify, utcOf } from './run_report.mjs';
export { cell, pointer, stableStringify } from './run_report.mjs';

export const TOOL = 'kup-evidence-check';
export const TOOL_VERSION = '0.1.0';
export const LABEL = 'Structural consistency of the supplied KUP records: data-quality observations only. Not a workflow gate verdict; never changes a Kriya verdict, reconstructs missing evidence or infers a failure cause, a timeline, success or qualification. Every diagnostic names its input file, JSON pointer and the contract or serializer it rests on.';
export const CLASSIFICATIONS = Object.freeze(['structural_error', 'consistency_conflict', 'unresolved_reference', 'ambiguity', 'informational']);
export const DETECTS = Object.freeze(['malformed_input', 'contradiction', 'incomplete_coverage']);
export const EXIT_CODES = Object.freeze({ no_actionable_diagnostics: 0, diagnostics: 1, usage_or_output_failure: 2 });

// ---- evidence basis (where each invariant is written down) --------------------------------------------------------
const B = Object.freeze({
  envelope: 'ui/kup/schema/common.schema.json#/$defs/Envelope (one JSON object; schema_version const 1; on error data is null)',
  section: 'ui/kup/schema/common.schema.json#/$defs/Section ("data: present and non-null only when availability is recorded")',
  consistency: 'ui/kup/schema/common.schema.json#/$defs/Consistency (kind per operation; source_metadata_changed semantics)',
  source: 'ui/kup/schema/common.schema.json#/$defs/Source',
  runEvent: 'ui/kup/schema/history.schema.json#/$defs/RunEvent (created_at: Unix epoch seconds; authority shown literally)',
  attribution: 'ui/kup/schema/history.schema.json#/$defs/AttributionRecord (evidence_ids: strings; no namespace is defined)',
  capabilities: 'ui/kup/schema/capabilities.schema.json; kriya/kup/inspect.py::capabilities (kup_versions = [KUP_SCHEMA_VERSION])',
  policy: 'kriya/kup/policy.py (KUP_SCHEMA_VERSION, OPERATIONS, CONSISTENCY_* assignments)',
  cliOps: 'kriya/kup/cli_ops.py (_history, _acquire, _verify: one Snapshot feeds source, consistency and data; _workspace_status: run_active and exit_code from status)',
  inspect: 'kriya/kup/inspect.py (envelope: null source/consistency/data on error; source_block: snapshot_directory = <dir>/<snapshot_id>; snapshot_consistency; history_detail: fields.<col> and <col> from one row; history_list: ORDER BY timestamp DESC, run_id DESC)',
  store: 'kriya/kup/store.py (list_snapshots: directory name is the id; snapshot_summary reads the manifest; metadata_differs; sha256_file hexdigest)',
  acquire: 'kriya/kup/acquire.py (manifest.json written once at publish; prune_snapshots: kept = listing after removal)',
  trace: 'kriya/core/trace.py (runs.run_id TEXT PRIMARY KEY)',
  runEvents: 'kriya/workflow/run_events.py (RunEvent.to_dict; EventAuthority = authoritative | advisory | auxiliary)',
  promptComposition: 'kriya/workflow/prompt_composition.py (token counts: non-negative integers, len//4 or provider-reported; prefill/load: seconds; prefix_break; token_counts note)',
  evidence: 'kriya/workflow/evidence.py::EvidenceRecord.to_dict (kind, source, attempt, payload, sensitivity, created_at: no identifier field)',
  evidenceKeys: 'kriya/workflow/evidence.py::EvidenceRecord.to_dict keys (kind, source, attempt, payload, sensitivity, created_at); writers state.py::record_failure and workflow.py active_skills',
  gates: 'gate_outcomes writer inventory (ui/fixtures/serializer_gates.py; ui/docs/GATE_OUTCOME_SHAPES.md): Failure.to_gate_outcome and every successful-gate literal record attempt, type, success (boolean), output; KUP defines no gate record',
  recovery: 'kriya/control/recovery.py (STATUS_CLEAN = "CLEAN", STATUS_RUN_ACTIVE = "RUN_ACTIVE")',
  compat: 'KUP compatibility policy: additionalProperties true on every record; unknown fields and values are preserved and shown literally, never interpreted or discarded',
});

const rule = (classification, detects, title, basis, applies_to, requires) => Object.freeze({ title, classification, detects, basis: Object.freeze(basis), applies_to, requires });
/** The rule registry: every emitted rule id, with its contract/serializer basis, applicability, required inputs and
 * whether it detects an actual contradiction, malformed input, or merely incomplete coverage. */
export const RULES = Object.freeze({
  'EVC-ENV-001': rule('structural_error', 'malformed_input', 'input is not a JSON object', [B.envelope], 'every input', 'the file'),
  'EVC-ENV-002': rule('structural_error', 'malformed_input', 'schema_version is not the supported KUP version 1', [B.envelope, B.policy], 'every JSON object input', 'the file'),
  'EVC-ENV-003': rule('structural_error', 'malformed_input', 'envelope does not validate against KUP v1 (generated validator)', [B.envelope], 'every JSON object input with schema_version 1', 'the file'),
  'EVC-ENV-004': rule('structural_error', 'malformed_input', 'operation payload does not validate against its KUP v1 schema (generated validator)', ['ui/kup/schema/*.schema.json per operation'], 'every valid non-error envelope', 'the file'),
  'EVC-ENV-005': rule('structural_error', 'contradiction', 'error envelope carries non-null data', [B.envelope, B.inspect], 'error envelopes', 'the file'),
  'EVC-ENV-006': rule('informational', 'contradiction', 'error envelope carries a non-null source or consistency (the serializer records null; the schema permits either)', [B.inspect, B.envelope], 'error envelopes', 'the file'),
  'EVC-ID-001': rule('consistency_conflict', 'contradiction', 'source.snapshot_id and consistency.snapshot_id differ', [B.cliOps, B.inspect], 'envelopes recording both ids', 'the file'),
  'EVC-ID-002': rule('consistency_conflict', 'contradiction', 'source.snapshot_directory does not end in source.snapshot_id', [B.inspect, B.store], 'envelopes recording both', 'the file'),
  'EVC-ID-003': rule('consistency_conflict', 'contradiction', 'payload snapshot_id differs from the envelope source/consistency snapshot_id', [B.cliOps], 'snapshot.acquire, snapshot.verify', 'the file'),
  'EVC-ID-004': rule('consistency_conflict', 'contradiction', 'consistency.kind does not match the operation (history.*, snapshot.acquire: snapshot_copy; snapshot.list/prune/verify, workspace.status: live_observation; capabilities: not_applicable)', [B.policy, B.cliOps, B.consistency], 'non-error envelopes with a consistency block', 'the file'),
  'EVC-ID-005': rule('informational', 'incomplete_coverage', 'snapshot_copy consistency without snapshot_id (the serializer always records it; the schema does not require it)', [B.inspect, B.consistency], 'consistency.kind = snapshot_copy', 'the file'),
  'EVC-ID-006': rule('consistency_conflict', 'contradiction', 'source_metadata_changed disagrees with metadata_differs(source_metadata_at_acquisition, source_metadata_now)', [B.store, B.consistency], 'consistency blocks and snapshot summaries', 'the file'),
  'EVC-ID-007': rule('consistency_conflict', 'contradiction', 'workspace.status run_active or exit_code disagrees with status (run_active = status == RUN_ACTIVE; exit_code 0 for CLEAN, 3 for RUN_ACTIVE, else 1)', [B.cliOps, B.recovery], 'workspace.status', 'the file'),
  'EVC-ID-008': rule('consistency_conflict', 'contradiction', 'capabilities.kup_versions does not contain the envelope schema_version', [B.capabilities, B.policy], 'capabilities', 'the file'),
  'EVC-SEC-001': rule('structural_error', 'contradiction', 'section not recorded but data is non-null', [B.section], 'every Section (run detail sections, fields.<col>)', 'the file'),
  'EVC-SEC-002': rule('consistency_conflict', 'contradiction', 'fields.<col> and the <col> section disagree (availability, or the stored text does not parse to the section data)', [B.inspect], 'history.detail with a column in both fields and sections', 'the file'),
  'EVC-SEC-003': rule('informational', 'incomplete_coverage', 'section recorded with null data (permitted only as a recorded JSON null)', [B.section, B.inspect], 'every Section', 'the file'),
  'EVC-EVT-001': rule('structural_error', 'malformed_input', 'run event does not match the serializer shape (kind and created_at required)', [B.runEvent, B.runEvents], 'history.detail run_events items', 'the file'),
  'EVC-EVT-002': rule('structural_error', 'malformed_input', 'created_at is outside the representable epoch-seconds range', [B.runEvent], 'run events with numeric created_at', 'the file'),
  'EVC-EVT-003': rule('informational', 'incomplete_coverage', 'authority outside the EventAuthority vocabulary (shown literally, never interpreted)', [B.runEvents, B.runEvent], 'run events', 'the file'),
  'EVC-TOK-001': rule('structural_error', 'malformed_input', 'documented developer.prompt_composition field has an undocumented type or domain (counts: non-negative integer or null; seconds: non-negative number or null; prefix_break and token_counts: string or null)', [B.promptComposition], 'developer.prompt_composition events', 'the file'),
  'EVC-UNK-001': rule('informational', 'incomplete_coverage', 'field outside the documented shape: preserved and uninterpreted', [B.compat, B.evidenceKeys], 'envelope, source, consistency, run rows, run detail, sections, run events, gate records, evidence records, prompt_composition details, snapshot summaries', 'the file'),
  'EVC-REF-001': rule('unresolved_reference', 'incomplete_coverage', 'attribution.evidence_ids cannot be resolved from supplied inputs: the contract defines no namespace and this Kriya version persists no evidence identifier', [B.attribution, B.evidence], 'history.detail with a recorded attribution carrying evidence_ids', 'the file'),
  'EVC-REF-002': rule('unresolved_reference', 'incomplete_coverage', 'snapshot id not present in the supplied listing of its snapshot directory (a listing is a point-in-time observation: pruned later or acquired after; not proof that Kriya lost it)', [B.store, B.acquire], 'history.*, snapshot.acquire, snapshot.verify with a supplied snapshot.list of the same directory', 'the referencing file plus a snapshot.list'),
  'EVC-REF-003': rule('informational', 'incomplete_coverage', 'snapshot id cannot be resolved from supplied inputs (no snapshot.list of its directory was supplied, or the directory is not recorded)', [B.store], 'history.*, snapshot.acquire, snapshot.verify', 'the referencing file'),
  'EVC-DUP-001': rule('consistency_conflict', 'contradiction', 'snapshot_id repeated within one snapshot.list listing (snapshot directory names are unique)', [B.store], 'snapshot.list', 'the file'),
  'EVC-DUP-002': rule('consistency_conflict', 'contradiction', 'run_id repeated within one history.list page (runs.run_id is the primary key of one committed image)', [B.trace, B.inspect], 'history.list', 'the file'),
  'EVC-DUP-003': rule('consistency_conflict', 'contradiction', 'a snapshot id is listed as both removed/pruned and kept/published in one response', [B.acquire], 'snapshot.prune, snapshot.acquire', 'the file'),
  'EVC-AMB-001': rule('ambiguity', 'incomplete_coverage', 'run_id recorded in several records whose snapshot scope is unknown; same snapshot or progression cannot be told apart', [B.trace, B.inspect], 'history records without a snapshot_id', 'two or more files'),
  'EVC-AMB-002': rule('ambiguity', 'incomplete_coverage', 'path repeated within one context.known_target_package tiers or omitted list: path-keyed matching is ambiguous (not an error; paths carry no unique id)', ['context.known_target_package details as serialized (ui/fixtures/serializer/run_events.json)'], 'history.detail run_events', 'the file'),
  'EVC-ORD-001': rule('informational', 'contradiction', 'history.list page is not in the serializer order (timestamp DESC, run_id DESC, null timestamps last; the schema documents no order)', [B.inspect], 'history.list', 'the file'),
  'EVC-VER-001': rule('structural_error', 'malformed_input', 'snapshot.verify sha256 is not a 64-character lowercase hex digest', [B.store, B.acquire], 'snapshot.verify', 'the file'),
  'EVC-CONF-001': rule('consistency_conflict', 'contradiction', 'the same (snapshot_id, run_id) is recorded with different documented run fields (one committed image, one row)', [B.inspect, B.trace], 'history.list rows and history.detail runs of the same snapshot', 'two or more records'),
  'EVC-CONF-002': rule('consistency_conflict', 'contradiction', 'the same snapshot_id is recorded with different manifest facts (acquisition timestamps, source, metadata at acquisition, rows, size, sqlite_version, sha256)', [B.store, B.acquire], 'snapshot.list items, snapshot.acquire, snapshot.verify, history consistency blocks', 'two or more records'),
  'EVC-GATE-001': rule('informational', 'incomplete_coverage', 'gate record outside the inventoried writer shape (object with attempt, string type, boolean success, string output): no field is interpreted', [B.gates], 'history.detail gate_outcomes items', 'the file'),
  'EVC-GATE-002': rule('ambiguity', 'contradiction', 'gate record carries conflicting result fields (success and a legacy passed boolean disagree): the result is ambiguous and is never resolved by choosing one', [B.gates], 'history.detail gate_outcomes items', 'the file'),
  'EVC-INFO-001': rule('informational', 'incomplete_coverage', 'the same run_id in different snapshots is recorded with different fields: not a contradiction (the run may have progressed between acquisitions)', [B.inspect], 'history records of different snapshots', 'two or more records'),
});
/** Useful checks WITHOUT a documented invariant in this Kriya version: listed, never performed. */
export const UNSUPPORTED_CHECKS = Object.freeze([
  'history.list next_cursor binding to its snapshot: the cursor is opaque by contract and is not decoded',
  'event attempt numbers against run.attempts: no documented invariant (events record presence per attempt, not completeness)',
  'gate_outcomes semantics: KUP defines no gate record; the writer inventory (attempt, type, success, output) is checked only as informational shape (EVC-GATE-001) and conflicting result fields as ambiguity (EVC-GATE-002); deterministic_result, graded_by, status and per-type meaning are not interpreted',
  'evidence_records linkage: EvidenceRecord.to_dict carries no identifier, so nothing can reference an evidence record',
  'generation_metrics keys, model_hops entries, failure_report rows against gate_outcomes: undocumented shapes, uninterpreted',
  'chronology or ordering of events from created_at: never inferred',
  'status, failure_category and consistency vocabularies beyond the schema enums: literal values by contract',
  'prompt_rendered (history.prompt): never read',
  'snapshot.verify sha256 against the snapshot file: this tool opens no store or snapshot by design',
  'ContextItem.member_ids: Code Intelligence symbol ids with no in-record target',
]);

// ---- helpers --------------------------------------------------------------------------------------------------------
const isObj = (x) => typeof x === 'object' && x !== null && !Array.isArray(x);
const same = (a, b) => stableStringify(a ?? null, 0) === stableStringify(b ?? null, 0);
const sortStr = (a, b) => a.localeCompare(b, 'en', { numeric: true });
const ENVELOPE_KEYS = new Set(['schema_version', 'operation', 'request_id', 'observed_at', 'source', 'consistency', 'data', 'error']);
const SOURCE_KEYS = new Set(['state_directory', 'trace_database', 'snapshot_directory', 'snapshot_id']);
const CONSISTENCY_KEYS = new Set(['kind', 'live_stream', 'snapshot_id', 'acquisition_started_at', 'acquisition_completed_at', 'source_metadata_at_acquisition', 'source_metadata_now', 'source_metadata_changed']);
const SECTION_KEYS = new Set(['availability', 'provenance', 'reason', 'data']);
export const SUMMARY_KEYS = Object.freeze(['run_id', 'timestamp', 'goal', 'duration_sec', 'attempts', 'status', 'failure_category', 'files_modified', 'milestone_group_id', 'milestone_index', 'milestone_total']);
const DETAIL_KEYS = new Set(['run', 'fields', ...DETAIL_SECTIONS]);
const SNAPSHOT_SUMMARY_KEYS = new Set(['snapshot_id', 'acquisition_started_at', 'acquisition_completed_at', 'source', 'source_metadata_at_acquisition', 'source_metadata_now', 'source_metadata_changed', 'rows', 'size', 'sqlite_version', 'digest_verified']);
const MANIFEST_FACTS = ['acquisition_started_at', 'acquisition_completed_at', 'source', 'source_metadata_at_acquisition', 'rows', 'size', 'sqlite_version', 'sha256'];
const AUTHORITIES = new Set(['authoritative', 'advisory', 'auxiliary']);
const META_KEYS = ['size', 'mtime_ns', 'inode', 'wal_size', 'shm_size', 'journal_size'];
const EXPECTED_KIND = Object.freeze({ capabilities: 'not_applicable', 'history.list': 'snapshot_copy', 'history.detail': 'snapshot_copy', 'history.prompt': 'snapshot_copy', 'snapshot.acquire': 'snapshot_copy', 'snapshot.list': 'live_observation', 'snapshot.prune': 'live_observation', 'snapshot.verify': 'live_observation', 'workspace.status': 'live_observation' });
const DATA_VALIDATORS = Object.freeze({ capabilities: validators.validateCapabilities, 'history.list': validators.validateHistoryList, 'history.detail': validators.validateRunDetail, 'history.prompt': validators.validatePrompt, 'workspace.status': validators.validateWorkspaceStatus, 'snapshot.acquire': validators.validateSnapshotAcquireResult, 'snapshot.list': validators.validateSnapshotList, 'snapshot.prune': validators.validateSnapshotPrune, 'snapshot.verify': validators.validateSnapshotVerify });

/** Resolve an RFC 6901 pointer against a parsed document. */
export function resolvePointer(doc, ptr) {
  if (ptr === '') return { found: true, value: doc };
  if (!ptr.startsWith('/')) return { found: false };
  let cur = doc;
  for (const raw of ptr.split('/').slice(1)) {
    const key = raw.replace(/~1/g, '/').replace(/~0/g, '~');
    if (Array.isArray(cur)) { if (!/^(0|[1-9]\d*)$/.test(key) || Number(key) >= cur.length) return { found: false }; cur = cur[Number(key)]; }
    else if (isObj(cur) && Object.prototype.hasOwnProperty.call(cur, key)) cur = cur[key];
    else return { found: false };
  }
  return { found: true, value: cur };
}
/** Port of kriya/kup/store.py::metadata_differs: null when either side is missing or carries an error. */
export function metadataDiffers(before, now) {
  if (!isObj(before) || !isObj(now) || 'error' in before || 'error' in now) return null;
  return META_KEYS.some((k) => !same(before[k] ?? null, now[k] ?? null));
}
/** The observed value at a pointer: absent, null, or the value (long text and large structures are not copied; the
 * pointer keeps them reachable in the input). */
const bounded = (v) => {
  if (typeof v === 'string') return v.length <= 256 ? v : { omitted_text: true, length: v.length };
  if (isObj(v) || Array.isArray(v)) { const s = stableStringify(v, 0); return s.length <= 512 ? v : { omitted: true, type: Array.isArray(v) ? 'array' : 'object', size: Array.isArray(v) ? v.length : Object.keys(v).length }; }
  return v;
};
export function observe(doc, ptr) {
  const r = resolvePointer(doc, ptr);
  if (!r.found) return { kind: 'absent' };
  if (r.value === null) return { kind: 'null' };
  return { kind: 'value', value: bounded(r.value) };
}

// ---- diagnostics ----------------------------------------------------------------------------------------------------
function diag(ctx, ruleId, ptr, { observed, related = [], explanation }) {
  const r = RULES[ruleId];
  if (!r) throw new Error(`unregistered rule ${ruleId}`);
  const rel = related.map((x) => ({ file: x.file ?? ctx.file, pointer: x.pointer, observed: x.observed ?? observe((x.ctx ?? ctx).doc, x.pointer) }));
  ctx.diagnostics.push({ rule_id: ruleId, classification: r.classification, detects: r.detects, file: ctx.file, pointer: ptr, observed: observed ?? observe(ctx.doc, ptr), related: rel, explanation, basis: r.basis });
}
function validatorDiagnostics(ctx, ruleId, validator, base, what) {
  const errors = validator.errors ?? [];
  if (!errors.length) diag(ctx, ruleId, base, { explanation: `${what} does not validate (no detail reported by the validator)` });
  for (const e of errors) {
    const parent = `${base}${e.instancePath ?? ''}`;
    if (e.keyword === 'required') diag(ctx, ruleId, `${parent}${pointer(e.params.missingProperty)}`, { observed: { kind: 'absent' }, explanation: `${what}: required property "${e.params.missingProperty}" is absent` });
    else diag(ctx, ruleId, parent, { explanation: `${what}: ${e.message ?? e.keyword}${e.params && 'allowedValues' in e.params ? ` (${JSON.stringify(e.params.allowedValues)})` : ''}` });
  }
}
/** Unknown fields are aggregated per (container, field): one diagnostic naming the first occurrence, the others related. */
function unknown(ctx, container, base, obj, known) {
  if (!isObj(obj)) return;
  for (const k of Object.keys(obj).filter((k) => !known.has(k)).sort(sortStr)) {
    const key = `${container}:${k}`;
    const ptr = `${base}${pointer(k)}`;
    ctx.input.unknown_fields += 1;
    if (ctx.unknown.has(key)) ctx.unknown.get(key).related.push({ pointer: ptr });
    else ctx.unknown.set(key, { ptr, related: [], explanation: `${container} field "${k}" is outside the documented shape; preserved in the input, not interpreted${k.endsWith('_tokens') ? ' (a name ending in _tokens proves nothing about its meaning)' : ''}` });
  }
}
function flushUnknown(ctx) {
  for (const [, u] of [...ctx.unknown.entries()].sort(([a], [b]) => sortStr(a, b))) diag(ctx, 'EVC-UNK-001', u.ptr, { related: u.related, explanation: u.explanation + (u.related.length ? `; ${u.related.length + 1} occurrences in this file` : '') });
}
const typeName = (v) => (v === null ? 'null' : Array.isArray(v) ? 'array' : typeof v);

// ---- one input --------------------------------------------------------------------------------------------------------
export function loadInput(file, text, index = 0) {
  const input = { index, file, bytes: Buffer.byteLength(text, 'utf8'), sha256: createHash('sha256').update(text, 'utf8').digest('hex'), status: 'unread', operation: null, schema_version: null, snapshot_id: null, run_id: null, sections: null, unknown_fields: 0, diagnostics: 0 };
  const ctx = { file, index, input, doc: null, diagnostics: [], unknown: new Map(), payloadValid: false };
  let doc;
  try { doc = JSON.parse(text); } catch (e) { input.status = 'malformed_json'; diag(ctx, 'EVC-ENV-001', '', { observed: { kind: 'unparsable', detail: e instanceof Error ? e.message : String(e) }, explanation: 'the file is not JSON; nothing in it can be checked' }); return finish(ctx); }
  if (!isObj(doc)) { input.status = 'malformed_json'; ctx.doc = doc; diag(ctx, 'EVC-ENV-001', '', { explanation: `top-level JSON value is ${typeName(doc)}, not an object` }); return finish(ctx); }
  ctx.doc = doc;
  input.operation = typeof doc.operation === 'string' ? doc.operation : null;
  input.schema_version = 'schema_version' in doc ? doc.schema_version : null;
  if (doc.schema_version !== 1) { input.status = 'unsupported_schema_version'; diag(ctx, 'EVC-ENV-002', pointer('schema_version'), { explanation: 'only KUP schema_version 1 is supported by this contract; the file is not checked further' }); return finish(ctx); }
  if (!validators.validateEnvelope(doc)) { input.status = 'not_a_kup_envelope'; validatorDiagnostics(ctx, 'EVC-ENV-003', validators.validateEnvelope, '', 'envelope'); return finish(ctx); }
  unknown(ctx, 'envelope', '', doc, ENVELOPE_KEYS);
  if (doc.error) {
    input.status = 'kup_error';
    if (doc.data !== null) diag(ctx, 'EVC-ENV-005', pointer('data'), { related: [{ pointer: pointer('error', 'code') }], explanation: 'an error envelope has data null by contract; a payload beside an error is contradictory' });
    for (const k of ['source', 'consistency']) if (doc[k] !== null) diag(ctx, 'EVC-ENV-006', pointer(k), { related: [{ pointer: pointer('error', 'code') }], explanation: `the serializer records ${k} as null on every error; the schema permits a value, so this is noted, not an error` });
    return finish(ctx);
  }
  checkEnvelope(ctx);
  const v = DATA_VALIDATORS[doc.operation];
  if (!v(doc.data)) { input.status = 'malformed_payload'; validatorDiagnostics(ctx, 'EVC-ENV-004', v, pointer('data'), `${doc.operation} payload`); return finish(ctx); }
  ctx.payloadValid = true;
  input.status = 'checked';
  checkPayload(ctx);
  return finish(ctx);
}
function finish(ctx) {
  flushUnknown(ctx);
  ctx.input.diagnostics = ctx.diagnostics.length;
  return ctx;
}

function checkEnvelope(ctx) {
  const d = ctx.doc;
  const src = isObj(d.source) ? d.source : null, con = isObj(d.consistency) ? d.consistency : null;
  if (src) unknown(ctx, 'source', pointer('source'), src, SOURCE_KEYS);
  if (con) unknown(ctx, 'consistency', pointer('consistency'), con, CONSISTENCY_KEYS);
  if (src && typeof src.snapshot_id === 'string') ctx.input.snapshot_id = src.snapshot_id; else if (con && typeof con.snapshot_id === 'string') ctx.input.snapshot_id = con.snapshot_id;
  if (src && con && 'snapshot_id' in src && 'snapshot_id' in con && !same(src.snapshot_id, con.snapshot_id)) diag(ctx, 'EVC-ID-001', pointer('source', 'snapshot_id'), { related: [{ pointer: pointer('consistency', 'snapshot_id') }], explanation: 'the serializer fills source and consistency from the one Snapshot the history was read from; the two ids must be equal' });
  if (src && typeof src.snapshot_directory === 'string' && typeof src.snapshot_id === 'string' && basename(src.snapshot_directory) !== src.snapshot_id) diag(ctx, 'EVC-ID-002', pointer('source', 'snapshot_directory'), { related: [{ pointer: pointer('source', 'snapshot_id') }], explanation: 'a published snapshot lives in <snapshot directory>/<snapshot_id>; the directory name must be the id' });
  if (con && typeof con.kind === 'string' && EXPECTED_KIND[d.operation] && con.kind !== EXPECTED_KIND[d.operation]) diag(ctx, 'EVC-ID-004', pointer('consistency', 'kind'), { related: [{ pointer: pointer('operation') }], explanation: `operation ${d.operation} records consistency.kind ${EXPECTED_KIND[d.operation]} (policy.py, cli_ops.py); ${JSON.stringify(con.kind)} was recorded` });
  if (con && con.kind === 'snapshot_copy' && (!('snapshot_id' in con) || con.snapshot_id === null)) diag(ctx, 'EVC-ID-005', pointer('consistency', 'snapshot_id'), { explanation: 'snapshot_consistency always records the snapshot id; without it the record cannot be placed in a snapshot scope' });
  if (con) checkMetadataChanged(ctx, pointer('consistency'), con);
}
function checkMetadataChanged(ctx, base, obj) {
  if (!('source_metadata_changed' in obj)) return;
  const expected = metadataDiffers(obj.source_metadata_at_acquisition, obj.source_metadata_now);
  if (!same(expected, obj.source_metadata_changed)) diag(ctx, 'EVC-ID-006', `${base}${pointer('source_metadata_changed')}`, { related: [{ pointer: `${base}${pointer('source_metadata_at_acquisition')}` }, { pointer: `${base}${pointer('source_metadata_now')}` }], explanation: `metadata_differs(at_acquisition, now) over size, mtime_ns, inode, wal_size, shm_size, journal_size gives ${JSON.stringify(expected)} (null when a side is missing or carries an error); ${JSON.stringify(obj.source_metadata_changed)} was recorded` });
}

function checkPayload(ctx) {
  const d = ctx.doc, data = d.data, op = d.operation;
  const src = isObj(d.source) ? d.source : null, con = isObj(d.consistency) ? d.consistency : null;
  const payloadIdCheck = (ptr) => {
    for (const [side, obj] of [['source', src], ['consistency', con]]) if (obj && 'snapshot_id' in obj && !same(obj.snapshot_id, data.snapshot_id)) diag(ctx, 'EVC-ID-003', ptr, { related: [{ pointer: pointer(side, 'snapshot_id') }], explanation: `${op} answers for exactly one snapshot; data.snapshot_id and ${side}.snapshot_id come from the same Snapshot and must be equal` });
  };
  switch (op) {
    case 'history.detail': checkDetail(ctx, data); break;
    case 'history.list': checkList(ctx, data); break;
    case 'history.prompt': unknown(ctx, 'payload', pointer('data'), data, new Set(['prompt_rendered', 'role', 'scope'])); break;
    case 'snapshot.list': {
      unknown(ctx, 'payload', pointer('data'), data, new Set(['snapshot_directory', 'snapshots', 'retain']));
      const seen = new Map();
      data.snapshots.forEach((s, i) => {
        const base = pointer('data', 'snapshots', i);
        unknown(ctx, 'snapshot_summary', base, s, SNAPSHOT_SUMMARY_KEYS);
        checkMetadataChanged(ctx, base, s);
        (seen.get(s.snapshot_id) ?? seen.set(s.snapshot_id, []).get(s.snapshot_id)).push(i);
      });
      for (const [id, idx] of [...seen.entries()].sort(([a], [b]) => sortStr(a, b))) if (idx.length > 1) diag(ctx, 'EVC-DUP-001', pointer('data', 'snapshots', idx[0], 'snapshot_id'), { related: idx.slice(1).map((i) => ({ pointer: pointer('data', 'snapshots', i, 'snapshot_id') })), explanation: `snapshot ${id} is listed ${idx.length} times in one directory listing; directory names are unique, so one listing cannot hold it twice` });
      break;
    }
    case 'snapshot.acquire': {
      unknown(ctx, 'snapshot_summary', pointer('data'), data, new Set([...SNAPSHOT_SUMMARY_KEYS, 'orphans_removed', 'pruned', 'backup_steps', 'duration_ms']));
      payloadIdCheck(pointer('data', 'snapshot_id'));
      checkMetadataChanged(ctx, pointer('data'), data);
      if (Array.isArray(data.pruned)) data.pruned.forEach((p, i) => { if (same(p, data.snapshot_id)) diag(ctx, 'EVC-DUP-003', pointer('data', 'pruned', i), { related: [{ pointer: pointer('data', 'snapshot_id') }], explanation: 'retention prunes older snapshots after the new one is published; the acquired snapshot cannot be in its own pruned list' }); });
      break;
    }
    case 'snapshot.verify':
      unknown(ctx, 'payload', pointer('data'), data, new Set(['snapshot_id', 'digest_verified', 'sha256', 'size', 'verified_at', 'duration_ms', 'guarantee']));
      payloadIdCheck(pointer('data', 'snapshot_id'));
      if (typeof data.sha256 === 'string' && !/^[0-9a-f]{64}$/.test(data.sha256)) diag(ctx, 'EVC-VER-001', pointer('data', 'sha256'), { explanation: 'the manifest digest is hashlib.sha256().hexdigest(): 64 lowercase hex characters' });
      break;
    case 'snapshot.prune': {
      unknown(ctx, 'payload', pointer('data'), data, new Set(['removed', 'orphans_removed', 'kept']));
      const kept = new Map(); data.kept.forEach((k, i) => kept.set(k, i));
      data.removed.forEach((r, i) => { if (kept.has(r)) diag(ctx, 'EVC-DUP-003', pointer('data', 'removed', i), { related: [{ pointer: pointer('data', 'kept', kept.get(r)) }], explanation: 'kept is the directory listing taken after removal; a removed snapshot cannot still be listed as kept' }); });
      break;
    }
    case 'workspace.status': {
      unknown(ctx, 'payload', pointer('data'), data, new Set(['workspace', 'run_active', 'status', 'exit_code', 'assessment']));
      if (typeof data.status === 'string') {
        const active = data.status === 'RUN_ACTIVE', exit = data.status === 'CLEAN' ? 0 : active ? 3 : 1;
        if (data.run_active !== null && data.run_active !== active) diag(ctx, 'EVC-ID-007', pointer('data', 'run_active'), { related: [{ pointer: pointer('data', 'status') }], explanation: `run_active is status == RUN_ACTIVE (${active}) in cli_ops._workspace_status; ${JSON.stringify(data.run_active)} was recorded` });
        if (data.exit_code !== null && data.exit_code !== exit) diag(ctx, 'EVC-ID-007', pointer('data', 'exit_code'), { related: [{ pointer: pointer('data', 'status') }], explanation: `exit_code is 0 for CLEAN, 3 for RUN_ACTIVE, else 1 (${exit} for status ${JSON.stringify(data.status)}); ${JSON.stringify(data.exit_code)} was recorded` });
      }
      break;
    }
    case 'capabilities':
      unknown(ctx, 'payload', pointer('data'), data, new Set(['kup_versions', 'operations', 'identity', 'limits', 'features']));
      if (!data.kup_versions.some((v) => same(v, d.schema_version))) diag(ctx, 'EVC-ID-008', pointer('data', 'kup_versions'), { related: [{ pointer: pointer('schema_version') }], explanation: 'capabilities answers kup_versions = [KUP_SCHEMA_VERSION], the same constant the envelope carries' });
      break;
    default: break;
  }
}

function checkSection(ctx, base, sec) {
  if (!isObj(sec)) return;
  unknown(ctx, 'section', base, sec, SECTION_KEYS);
  const recorded = sec.availability === 'recorded';
  if (!recorded && 'data' in sec && sec.data !== null) diag(ctx, 'EVC-SEC-001', `${base}${pointer('data')}`, { related: [{ pointer: `${base}${pointer('availability')}` }], explanation: `data is present and non-null only when availability is recorded; availability is ${JSON.stringify(sec.availability)}` });
  if (recorded && (!('data' in sec) || sec.data === null)) diag(ctx, 'EVC-SEC-003', `${base}${pointer('data')}`, { observed: 'data' in sec ? observe(ctx.doc, `${base}${pointer('data')}`) : { kind: 'absent' }, related: [{ pointer: `${base}${pointer('availability')}` }], explanation: 'recorded with null data: the contract allows it only when the stored JSON text is the literal null' });
}
const sectionAvailability = (sec) => (isObj(sec) && typeof sec.availability === 'string' ? sec.availability : null);

function checkDetail(ctx, data) {
  const run = data.run;
  ctx.input.run_id = typeof run.run_id === 'string' ? run.run_id : null;
  ctx.input.sections = Object.fromEntries(DETAIL_SECTIONS.map((n) => [n, sectionAvailability(data[n])]));
  unknown(ctx, 'run', pointer('data', 'run'), run, new Set(SUMMARY_KEYS));
  unknown(ctx, 'run_detail', pointer('data'), data, DETAIL_KEYS);
  for (const name of DETAIL_SECTIONS) checkSection(ctx, pointer('data', name), data[name]);
  for (const col of Object.keys(data.fields).sort(sortStr)) {
    const base = pointer('data', 'fields', col), f = data.fields[col];
    checkSection(ctx, base, f);
    if (!(col in data) || !isObj(f) || !isObj(data[col])) continue;
    const sec = data[col];
    if (!same(f.availability, sec.availability)) { diag(ctx, 'EVC-SEC-002', `${base}${pointer('availability')}`, { related: [{ pointer: pointer('data', col, 'availability') }], explanation: `fields.${col} and the ${col} section describe one stored column (NULL or not); their availability must agree` }); continue; }
    if (f.availability === 'recorded' && typeof f.data === 'string') {
      let parsed, ok = true; try { parsed = JSON.parse(f.data); } catch { ok = false; }
      const agrees = ok ? same(parsed, sec.data) : same(f.data, sec.data);
      if (!agrees) diag(ctx, 'EVC-SEC-002', `${base}${pointer('data')}`, { observed: { kind: 'value', value: { omitted_text: true, length: f.data.length } }, related: [{ pointer: pointer('data', col, 'data') }], explanation: `the ${col} section is the parsed form of fields.${col} (the stored text itself when it is not JSON); the stored text does not parse to the section data` });
    }
  }
  if (sectionAvailability(data.run_events) === 'recorded' && Array.isArray(data.run_events.data)) checkEvents(ctx, data.run_events.data);
  if (sectionAvailability(data.gate_outcomes) === 'recorded' && Array.isArray(data.gate_outcomes.data)) checkGates(ctx, data.gate_outcomes.data);
  if (sectionAvailability(data.evidence_records) === 'recorded' && Array.isArray(data.evidence_records.data)) data.evidence_records.data.forEach((r, i) => unknown(ctx, 'evidence_record', pointer('data', 'evidence_records', 'data', i), r, EVIDENCE_RECORD_KEYS));
  if (sectionAvailability(data.attribution) === 'recorded' && isObj(data.attribution.data) && Array.isArray(data.attribution.data.evidence_ids) && data.attribution.data.evidence_ids.length) {
    const ids = data.attribution.data.evidence_ids;
    const strings = ids.filter((x) => typeof x === 'string');
    const repeated = [...new Set(strings.filter((x, i) => strings.indexOf(x) !== i))].sort(sortStr);
    const extra = `${repeated.length ? `; repeated within the list: ${repeated.map((x) => JSON.stringify(x)).join(', ')}` : ''}${strings.length !== ids.length ? `; ${ids.length - strings.length} entr${ids.length - strings.length === 1 ? 'y is' : 'ies are'} not a string` : ''}`;
    diag(ctx, 'EVC-REF-001', pointer('data', 'attribution', 'data', 'evidence_ids'), { related: [{ pointer: pointer('data', 'evidence_records', 'availability') }], explanation: `cannot resolve from supplied inputs: ${ids.length} evidence id(s) are recorded, but the KUP contract defines no namespace they resolve in and this Kriya version persists no evidence identifier (EvidenceRecord.to_dict); a field named evidence_id in a fixture is not a documented target${extra}` });
  }
}
const KNOWN_GATE_KEYS = new Set([...GATE_OUTCOME_KEYS.common, ...GATE_OUTCOME_KEYS.optional]);
const EVIDENCE_RECORD_KEYS = new Set(['kind', 'source', 'attempt', 'payload', 'sensitivity', 'created_at']); // EvidenceRecord.to_dict; no identifier
function checkGates(ctx, list) {
  list.forEach((o, i) => {
    const base = pointer('data', 'gate_outcomes', 'data', i);
    if (!isObj(o)) { diag(ctx, 'EVC-GATE-001', base, { explanation: `gate record is ${typeName(o)}, not an object` }); return; }
    const missing = GATE_OUTCOME_KEYS.common.filter((k) => !(k in o));
    const wrong = [['type', 'string'], ['success', 'boolean'], ['output', 'string']].filter(([k, t]) => k in o && typeof o[k] !== t).map(([k]) => k);
    if (!Number.isInteger(o.attempt) && 'attempt' in o) wrong.push('attempt');
    if (missing.length || wrong.length) diag(ctx, 'EVC-GATE-001', base, { explanation: `every Kriya writer records attempt (integer), type (string), success (boolean), output (string); ${missing.length ? `absent: ${missing.join(', ')}` : ''}${missing.length && wrong.length ? '; ' : ''}${wrong.length ? `unexpected type: ${wrong.join(', ')}` : ''}; no field of this record is interpreted` });
    const cls = classifyGateRecord(o);
    if (cls.result === 'ambiguous') diag(ctx, 'EVC-GATE-002', `${base}${pointer('success')}`, { related: cls.conflicts.map((c) => ({ pointer: `${base}${pointer(c.field)}` })), explanation: 'success and a legacy passed boolean disagree; the result is reported as ambiguous, never resolved by choosing one field' });
    unknown(ctx, 'gate_outcome', base, o, KNOWN_GATE_KEYS);
  });
}
function checkEvents(ctx, list) {
  list.forEach((e, i) => {
    const base = pointer('data', 'run_events', 'data', i);
    if (!isObj(e)) { diag(ctx, 'EVC-EVT-001', base, { explanation: `run event is ${typeName(e)}, not an object` }); return; }
    if (!validators.validateRunEvent(e)) validatorDiagnostics(ctx, 'EVC-EVT-001', validators.validateRunEvent, base, 'run event');
    if (typeof e.created_at === 'number' && utcOf(e.created_at) === null) diag(ctx, 'EVC-EVT-002', `${base}${pointer('created_at')}`, { explanation: 'created_at is Unix epoch seconds (time.time()); this number has no representable date' });
    if (typeof e.authority === 'string' && !AUTHORITIES.has(e.authority)) diag(ctx, 'EVC-EVT-003', `${base}${pointer('authority')}`, { explanation: 'outside EventAuthority (authoritative | advisory | auxiliary); shown literally, never interpreted' });
    unknown(ctx, 'run_event', base, e, new Set(EVENT_KEYS));
    if (e.kind === 'developer.prompt_composition' && isObj(e.details)) checkPromptComposition(ctx, `${base}${pointer('details')}`, e.details);
    if (e.kind === 'context.known_target_package' && isObj(e.details)) for (const listName of ['tiers', 'omitted']) {
      if (!Array.isArray(e.details[listName])) continue;
      const byPath = new Map();
      e.details[listName].forEach((t, j) => { if (isObj(t) && typeof t.path === 'string') (byPath.get(t.path) ?? byPath.set(t.path, []).get(t.path)).push(j); });
      for (const [p, idx] of [...byPath.entries()].sort(([a], [b]) => sortStr(a, b))) if (idx.length > 1) diag(ctx, 'EVC-AMB-002', `${base}${pointer('details', listName, idx[0], 'path')}`, { related: idx.slice(1).map((j) => ({ pointer: `${base}${pointer('details', listName, j, 'path')}` })), explanation: `path ${JSON.stringify(p)} occurs ${idx.length} times in details.${listName} of one event; path-keyed matching (report, compare) treats it as ambiguous; the record itself is not wrong` });
    }
  });
}
function checkPromptComposition(ctx, base, d) {
  const PC = PROMPT_COMPOSITION_FIELDS;
  const known = new Set([...PC.estimated_by_kriya, ...PC.provider_reported, ...PC.provider_timing, ...PC.recorded_other, ...PC.note]);
  const bad = (k, why) => diag(ctx, 'EVC-TOK-001', `${base}${pointer(k)}`, { explanation: `${k}: ${why}` });
  for (const k of [...PC.estimated_by_kriya, ...PC.provider_reported]) if (k in d && d[k] !== null && !(Number.isInteger(d[k]) && d[k] >= 0)) bad(k, `${PC.provider_reported.includes(k) ? 'provider-reported' : 'estimated (len//4)'} token count is a non-negative integer or null`);
  for (const k of PC.provider_timing) if (k in d && d[k] !== null && !(typeof d[k] === 'number' && Number.isFinite(d[k]) && d[k] >= 0)) bad(k, 'provider-reported seconds is a non-negative finite number or null');
  for (const k of [...PC.recorded_other, ...PC.note]) if (k in d && d[k] !== null && typeof d[k] !== 'string') bad(k, 'a string or null');
  unknown(ctx, 'prompt_composition', base, d, known);
}
function checkList(ctx, data) {
  unknown(ctx, 'payload', pointer('data'), data, new Set(['runs', 'next_cursor']));
  const seen = new Map();
  data.runs.forEach((r, i) => { unknown(ctx, 'run', pointer('data', 'runs', i), r, new Set(SUMMARY_KEYS)); (seen.get(r.run_id) ?? seen.set(r.run_id, []).get(r.run_id)).push(i); });
  for (const [id, idx] of [...seen.entries()].sort(([a], [b]) => sortStr(a, b))) if (idx.length > 1) diag(ctx, 'EVC-DUP-002', pointer('data', 'runs', idx[0], 'run_id'), { related: idx.slice(1).map((i) => ({ pointer: pointer('data', 'runs', i, 'run_id') })), explanation: `run ${id} appears ${idx.length} times in one page; run_id is the primary key of the runs table in one committed image` });
  const before = (a, b) => { // a may precede b under ORDER BY timestamp DESC, run_id DESC (NULL timestamps last)
    const ta = a.timestamp ?? null, tb = b.timestamp ?? null;
    if (ta === null && tb !== null) return false;
    if (ta !== null && tb === null) return true;
    if (ta !== tb) return typeof ta === 'string' && typeof tb === 'string' ? ta > tb : true;
    return typeof a.run_id === 'string' && typeof b.run_id === 'string' ? a.run_id >= b.run_id : true;
  };
  for (let i = 1; i < data.runs.length; i++) if (!before(data.runs[i - 1], data.runs[i])) { diag(ctx, 'EVC-ORD-001', pointer('data', 'runs', i, 'run_id'), { related: [{ pointer: pointer('data', 'runs', i - 1, 'run_id') }], explanation: 'history_list selects ORDER BY timestamp DESC, run_id DESC (NULL timestamps last); this row precedes a row that would sort before it (string comparison by code unit; SQLite BINARY collation agrees for ASCII)' }); break; }
}

// ---- across inputs ----------------------------------------------------------------------------------------------------
function checkAcross(ctxs) {
  const valid = ctxs.filter((c) => c.payloadValid);
  const snapshotRecords = new Map(); // id -> [{ctx, base, facts: {name: pointer}}]
  const listings = new Map(); // directory -> [{ctx, ids: Set}]
  const references = new Map(); // id -> [{ctx, pointer, directory}]
  const runRecords = new Map(); // `${sid}|${run_id}` -> [{ctx, base}]
  const scopeless = new Map(); // run_id -> [{ctx, base, sid}] (every record, for AMB-001 / INFO-001)
  const addSnapshot = (id, ctx, base, names) => { if (typeof id !== 'string') return; const facts = {}; const obj = resolvePointer(ctx.doc, base).value; for (const n of names) if (isObj(obj) && n in obj) facts[n] = `${base}${pointer(n)}`; (snapshotRecords.get(id) ?? snapshotRecords.set(id, []).get(id)).push({ ctx, base, facts }); };
  const addRun = (sid, runId, ctx, base) => { if (typeof runId !== 'string') return; const key = `${sid ?? ''}|${runId}`; (runRecords.get(key) ?? runRecords.set(key, []).get(key)).push({ ctx, base }); (scopeless.get(runId) ?? scopeless.set(runId, []).get(runId)).push({ ctx, base, sid: sid ?? null }); };
  for (const ctx of valid) {
    const d = ctx.doc, src = isObj(d.source) ? d.source : null, con = isObj(d.consistency) ? d.consistency : null;
    const sid = ctx.input.snapshot_id;
    const directory = src && typeof src.snapshot_directory === 'string' ? dirname(src.snapshot_directory) : null;
    if (d.operation.startsWith('history.') || d.operation === 'snapshot.acquire' || d.operation === 'snapshot.verify') {
      const refId = d.operation.startsWith('history.') ? sid : typeof d.data.snapshot_id === 'string' ? d.data.snapshot_id : sid;
      const refPtr = d.operation.startsWith('history.') ? (src && 'snapshot_id' in src ? pointer('source', 'snapshot_id') : pointer('consistency', 'snapshot_id')) : pointer('data', 'snapshot_id');
      if (typeof refId === 'string') (references.get(refId) ?? references.set(refId, []).get(refId)).push({ ctx, pointer: refPtr, directory });
      if (con && con.kind === 'snapshot_copy' && typeof con.snapshot_id === 'string') addSnapshot(con.snapshot_id, ctx, pointer('consistency'), ['acquisition_started_at', 'acquisition_completed_at', 'source_metadata_at_acquisition']);
    }
    if (d.operation === 'snapshot.list') { const ids = new Set(); d.data.snapshots.forEach((s, i) => { ids.add(s.snapshot_id); addSnapshot(s.snapshot_id, ctx, pointer('data', 'snapshots', i), MANIFEST_FACTS); }); (listings.get(d.data.snapshot_directory) ?? listings.set(d.data.snapshot_directory, []).get(d.data.snapshot_directory)).push({ ctx, ids }); }
    if (d.operation === 'snapshot.acquire') addSnapshot(d.data.snapshot_id, ctx, pointer('data'), MANIFEST_FACTS);
    if (d.operation === 'snapshot.verify') addSnapshot(d.data.snapshot_id, ctx, pointer('data'), ['size', 'sha256']);
    if (d.operation === 'history.detail') addRun(sid, d.data.run.run_id, ctx, pointer('data', 'run'));
    if (d.operation === 'history.list') d.data.runs.forEach((r, i) => addRun(sid, r.run_id, ctx, pointer('data', 'runs', i)));
  }
  const firstCtx = (recs) => recs.reduce((a, b) => (b.ctx.index < a.ctx.index ? b : a));
  // EVC-REF-002 / EVC-REF-003: snapshot ids against supplied listings of the same directory
  const resolution = {};
  for (const id of [...references.keys()].sort(sortStr)) {
    const refs = references.get(id).sort((a, b) => a.ctx.index - b.ctx.index || sortStr(a.pointer, b.pointer));
    const dirs = [...new Set(refs.map((r) => r.directory).filter(Boolean))];
    const inScope = dirs.flatMap((dir) => listings.get(dir) ?? []);
    const first = refs[0];
    const related = refs.slice(1).map((r) => ({ ctx: r.ctx, file: r.ctx.file, pointer: r.pointer }));
    if (!inScope.length) { resolution[id] = 'no_listing_supplied'; diag(first.ctx, 'EVC-REF-003', first.pointer, { related, explanation: dirs.length ? `cannot resolve from supplied inputs: no snapshot.list of ${dirs.join(', ')} was supplied` : 'cannot resolve from supplied inputs: the record does not name its snapshot directory and no listing scope can be established' }); continue; }
    const listed = inScope.filter((l) => l.ids.has(id));
    if (listed.length) { resolution[id] = 'listed'; continue; }
    resolution[id] = 'not_listed';
    diag(first.ctx, 'EVC-REF-002', first.pointer, { related: [...related, ...inScope.map((l) => ({ ctx: l.ctx, file: l.ctx.file, pointer: pointer('data', 'snapshots') }))], explanation: `snapshot ${id} is not in the supplied listing(s) of its directory; a listing is a point-in-time observation (the snapshot may have been pruned later or acquired after it), so this is an unresolved reference in the supplied export, not proof that Kriya lost evidence` });
  }
  // EVC-CONF-002: manifest facts of one snapshot across records
  for (const id of [...snapshotRecords.keys()].sort(sortStr)) {
    const recs = snapshotRecords.get(id);
    if (recs.length < 2) continue;
    for (const name of MANIFEST_FACTS) {
      const having = recs.filter((r) => name in r.facts).sort((a, b) => a.ctx.index - b.ctx.index || sortStr(a.base, b.base));
      if (having.length < 2) continue;
      const ref = having[0], refVal = resolvePointer(ref.ctx.doc, ref.facts[name]).value;
      const differing = having.slice(1).filter((r) => !same(resolvePointer(r.ctx.doc, r.facts[name]).value, refVal));
      if (differing.length) diag(ref.ctx, 'EVC-CONF-002', ref.facts[name], { related: differing.map((r) => ({ ctx: r.ctx, file: r.ctx.file, pointer: r.facts[name] })), explanation: `snapshot ${id}: ${name} is a manifest fact written once at publish, yet the supplied records disagree` });
    }
  }
  // EVC-CONF-001: one (snapshot, run) row across records
  for (const key of [...runRecords.keys()].sort(sortStr)) {
    const [sid, runId] = [key.slice(0, key.indexOf('|')), key.slice(key.indexOf('|') + 1)];
    if (!sid) continue;
    const recs = runRecords.get(key).sort((a, b) => a.ctx.index - b.ctx.index || sortStr(a.base, b.base));
    if (recs.length < 2) continue;
    const ref = recs[0], refRow = resolvePointer(ref.ctx.doc, ref.base).value;
    for (const k of SUMMARY_KEYS) {
      if (!(k in refRow)) continue;
      const differing = recs.slice(1).filter((r) => { const row = resolvePointer(r.ctx.doc, r.base).value; return k in row && !same(row[k], refRow[k]); });
      if (differing.length) diag(ref.ctx, 'EVC-CONF-001', `${ref.base}${pointer(k)}`, { related: differing.map((r) => ({ ctx: r.ctx, file: r.ctx.file, pointer: `${r.base}${pointer(k)}` })), explanation: `run ${runId} in snapshot ${sid}: ${k} is one stored column of one row in one committed image, yet the supplied records disagree` });
    }
  }
  // EVC-INFO-001 / EVC-AMB-001: the same run_id across snapshots or without a snapshot scope
  for (const runId of [...scopeless.keys()].sort(sortStr)) {
    const recs = scopeless.get(runId).sort((a, b) => a.ctx.index - b.ctx.index || sortStr(a.base, b.base));
    if (recs.length < 2) continue;
    const differingKeys = (a, b) => { const ra = resolvePointer(a.ctx.doc, a.base).value, rb = resolvePointer(b.ctx.doc, b.base).value; return SUMMARY_KEYS.filter((k) => k in ra && k in rb && !same(ra[k], rb[k])); };
    const unknownScope = recs.filter((r) => r.sid === null);
    if (unknownScope.length) {
      const ref = unknownScope[0], others = recs.filter((r) => r !== ref);
      const diff = [...new Set(others.flatMap((o) => differingKeys(ref, o)))];
      if (diff.length) diag(ref.ctx, 'EVC-AMB-001', `${ref.base}${pointer('run_id')}`, { related: others.map((r) => ({ ctx: r.ctx, file: r.ctx.file, pointer: `${r.base}${pointer('run_id')}` })), explanation: `run ${runId} is recorded ${recs.length} times with differing ${diff.join(', ')}, and at least one record carries no snapshot_id: whether these are one image (a contradiction) or different acquisitions (progression) cannot be resolved from the supplied inputs` });
      continue;
    }
    const sids = [...new Set(recs.map((r) => r.sid))].sort(sortStr);
    if (sids.length < 2) continue;
    const ref = recs[0], others = recs.filter((r) => r.sid !== ref.sid);
    const diff = [...new Set(others.flatMap((o) => differingKeys(ref, o)))];
    if (diff.length) diag(ref.ctx, 'EVC-INFO-001', `${ref.base}${pointer('run_id')}`, { related: others.map((r) => ({ ctx: r.ctx, file: r.ctx.file, pointer: `${r.base}${pointer('run_id')}` })), explanation: `run ${runId} appears in snapshots ${sids.join(', ')} with differing ${diff.join(', ')}; different acquisitions of a progressing run are not a contradiction and are not compared as one` });
  }
  const runs = [...scopeless.keys()].sort(sortStr).map((runId) => ({ run_id: runId, snapshot_ids: [...new Set(scopeless.get(runId).map((r) => r.sid))].sort((a, b) => sortStr(a ?? '', b ?? '')), records: scopeless.get(runId).length, files: [...new Set(scopeless.get(runId).map((r) => r.ctx.file))].sort(sortStr) }));
  const snapshots = [...new Set([...references.keys(), ...snapshotRecords.keys()])].sort(sortStr).map((id) => ({ snapshot_id: id, referenced_by: [...new Set((references.get(id) ?? []).map((r) => r.ctx.file))].sort(sortStr), recorded_in: [...new Set((snapshotRecords.get(id) ?? []).map((r) => r.ctx.file))].sort(sortStr), listed_in: [...listings.values()].flat().filter((l) => l.ids.has(id)).map((l) => l.ctx.file).sort(sortStr), resolution: resolution[id] ?? 'not_referenced' }));
  return { snapshots, runs, listings: [...listings.keys()].sort(sortStr).map((dir) => ({ snapshot_directory: dir, files: listings.get(dir).map((l) => l.ctx.file).sort(sortStr) })) };
}

// ---- the check ----------------------------------------------------------------------------------------------------------
export function buildCheck(files) {
  const ctxs = files.map(({ file, text }, i) => loadInput(file, text, i));
  const scopes = checkAcross(ctxs);
  const byFile = new Map(ctxs.map((c) => [c.file, c.index]));
  const diagnostics = ctxs.flatMap((c) => c.diagnostics).sort((a, b) => byFile.get(a.file) - byFile.get(b.file) || sortStr(a.pointer, b.pointer) || sortStr(a.rule_id, b.rule_id) || sortStr(a.explanation, b.explanation));
  for (const c of ctxs) c.input.diagnostics = diagnostics.filter((d) => d.file === c.file).length;
  const by = (key) => Object.fromEntries([...new Set(diagnostics.map((d) => d[key]))].sort(sortStr).map((k) => [k, diagnostics.filter((d) => d[key] === k).length]));
  const actionable = diagnostics.filter((d) => d.classification !== 'informational').length;
  const CHECKED = new Set(['checked', 'kup_error']); // an error envelope is fully checked: it has no payload to check
  const summary = { inputs: ctxs.length, checked: ctxs.filter((c) => CHECKED.has(c.input.status)).length, inputs_not_checked: ctxs.filter((c) => !CHECKED.has(c.input.status)).map((c) => ({ file: c.file, status: c.input.status })), diagnostics: diagnostics.length, actionable, by_classification: by('classification'), by_rule: by('rule_id'), exit_code: actionable ? EXIT_CODES.diagnostics : EXIT_CODES.no_actionable_diagnostics };
  return { tool: TOOL, tool_version: TOOL_VERSION, label: LABEL, exit_codes: EXIT_CODES, summary, coverage: { inputs: ctxs.map((c) => c.input), ...scopes }, diagnostics, rules: RULES, unsupported_checks: UNSUPPORTED_CHECKS };
}

// ---- Markdown -------------------------------------------------------------------------------------------------------------
const showObserved = (o) => (!o ? '' : o.kind === 'absent' ? '(absent)' : o.kind === 'null' ? 'null' : o.kind === 'unparsable' ? `(unparsable: ${o.detail})` : typeof o.value === 'string' ? JSON.stringify(o.value) : JSON.stringify(o.value));
const src = (file, ptr) => `\`${cell(file)}#${cell(ptr || '/')}\``;
export function renderMarkdown(report) {
  const L = [`# ${TOOL} ${TOOL_VERSION}`, '', `> ${LABEL}`, '', 'Classifications are data-quality observations, never workflow gate verdicts. Exit codes: 0 no actionable diagnostic (informational only), 1 actionable diagnostics written, 2 usage or output-write failure.', ''];
  const s = report.summary;
  L.push('## Summary', '', `Inputs: ${s.inputs}; checked: ${s.checked}; not checked: ${s.inputs_not_checked.length}${s.inputs_not_checked.length ? ` (${s.inputs_not_checked.map((x) => `${cell(x.file)}: ${x.status}`).join('; ')})` : ''}. Diagnostics: ${s.diagnostics}; actionable: ${s.actionable}. Exit code: ${s.exit_code}.`);
  if (s.diagnostics) { L.push('', '| classification | count |', '|---|---|'); for (const [k, n] of Object.entries(s.by_classification)) L.push(`| ${k} | ${n} |`); }
  L.push('', '## Inputs (as supplied, in order)', '', '| file | bytes | sha256 | operation | schema_version | status | snapshot_id | run_id | unknown fields | diagnostics |', '|---|---|---|---|---|---|---|---|---|---|');
  for (const i of report.coverage.inputs) L.push(`| ${cell(i.file)} | ${i.bytes} | ${i.sha256.slice(0, 16)}… | ${cell(i.operation ?? 'n/a')} | ${cell(i.schema_version === null ? 'n/a' : JSON.stringify(i.schema_version))} | ${i.status} | ${cell(i.snapshot_id ?? '-')} | ${cell(i.run_id ?? '-')} | ${i.unknown_fields} | ${i.diagnostics} |`);
  L.push('', '## Coverage: snapshot scopes', '');
  if (!report.coverage.snapshots.length) L.push('no snapshot id recorded in the supplied inputs');
  else { L.push('| snapshot_id | referenced by | recorded in | listed in | resolution |', '|---|---|---|---|---|'); for (const sn of report.coverage.snapshots) L.push(`| ${cell(sn.snapshot_id)} | ${cell(sn.referenced_by.join(', ') || '-')} | ${cell(sn.recorded_in.join(', ') || '-')} | ${cell(sn.listed_in.join(', ') || '-')} | ${sn.resolution} |`); }
  L.push('', '## Coverage: runs', '');
  if (!report.coverage.runs.length) L.push('no run record in the supplied inputs');
  else { L.push('| run_id | snapshot_ids | records | files |', '|---|---|---|---|'); for (const r of report.coverage.runs) L.push(`| ${cell(r.run_id)} | ${cell(r.snapshot_ids.map((x) => x ?? '(unknown)').join(', '))} | ${r.records} | ${cell(r.files.join(', '))} |`); }
  L.push('', '## Diagnostics', '');
  if (!report.diagnostics.length) L.push('none');
  else {
    L.push('| rule | classification | detects | file | pointer | observed | related | explanation |', '|---|---|---|---|---|---|---|---|');
    for (const d of report.diagnostics) L.push(`| ${d.rule_id} | ${d.classification} | ${d.detects} | ${cell(d.file)} | ${cell(d.pointer || '/')} | ${cell(showObserved(d.observed))} | ${d.related.map((r) => `${src(r.file, r.pointer)} = ${cell(showObserved(r.observed))}`).join('; ')} | ${cell(d.explanation)} |`);
  }
  L.push('', '## Rule registry (every rule this tool can emit)', '', '| rule | title | classification | detects | applies to | requires | evidence basis |', '|---|---|---|---|---|---|---|');
  for (const [id, r] of Object.entries(report.rules)) L.push(`| ${id} | ${cell(r.title)} | ${r.classification} | ${r.detects} | ${cell(r.applies_to)} | ${cell(r.requires)} | ${r.basis.map(cell).join('; ')} |`);
  L.push('', '## Not checked (no documented invariant; never strengthened silently)', '');
  for (const u of report.unsupported_checks) L.push(`- ${cell(u)}`);
  L.push('', '## Limitations', '', '- Only the files named on the command line are read; a reference that resolves nowhere in them is "cannot resolve from supplied inputs", never a statement about the store.', '- Checks rest on the KUP v1 schemas and the Kriya serializers named in the registry; fixture-shaped fields outside them are preserved and uninterpreted.', '- No verdict, cause, timeline, success or qualification is derived from these records.', '');
  return L.join('\n');
}

// ---- CLI ------------------------------------------------------------------------------------------------------------------
export function parseArgs(argv) {
  const files = []; let out = null; let overwrite = false;
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--out') { out = argv[++i] ?? null; if (out === null) return { error: '--out needs a directory' }; }
    else if (a === '--overwrite') overwrite = true;
    else if (a.startsWith('-')) return { error: `unknown option ${a}` };
    else files.push(a);
  }
  if (!out) return { error: 'an explicit output directory is required: --out <dir>' };
  if (!files.length) return { error: 'name at least one KUP JSON file explicitly (directories are never read)' };
  return { out, overwrite, files };
}

export function main(argv) {
  const usage = '\nusage: node tools/evidence_check.mjs --out <dir> [--overwrite] <kup-envelope.json>...';
  const args = parseArgs(argv);
  if (args.error) return { exitCode: EXIT_CODES.usage_or_output_failure, message: args.error + usage };
  const outDir = resolve(args.out);
  const outputs = [join(outDir, 'evidence-check.md'), join(outDir, 'evidence-check.json')];
  const inputs = [];
  for (const file of args.files) {
    const path = resolve(file);
    if (!existsSync(path)) return { exitCode: EXIT_CODES.usage_or_output_failure, message: `input does not exist: ${file}` };
    if (statSync(path).isDirectory()) return { exitCode: EXIT_CODES.usage_or_output_failure, message: `input is a directory, not a file (directories are never read): ${file}` };
    const real = realpathSync(path);
    if (outputs.some((o) => existsSync(o) && realpathSync(o) === real) || outputs.includes(path)) return { exitCode: EXIT_CODES.usage_or_output_failure, message: `refusing: an output path would overwrite the input ${file}` };
    inputs.push({ file, text: readFileSync(path, 'utf8') });
  }
  for (const o of outputs) if (existsSync(o) && !args.overwrite) return { exitCode: EXIT_CODES.usage_or_output_failure, message: `refusing to overwrite existing ${o} (pass --overwrite to replace it)` };
  if (existsSync(outDir) && !statSync(outDir).isDirectory()) return { exitCode: EXIT_CODES.usage_or_output_failure, message: `--out is not a directory: ${args.out}` };
  const report = buildCheck(inputs);
  try {
    mkdirSync(outDir, { recursive: true });
    writeFileSync(outputs[1], stableStringify(report) + '\n');
    writeFileSync(outputs[0], renderMarkdown(report));
  } catch (e) { return { exitCode: EXIT_CODES.usage_or_output_failure, message: `output could not be written: ${e instanceof Error ? e.message : String(e)}` }; }
  const s = report.summary;
  return { exitCode: s.exit_code, message: `wrote ${outputs[0]} and ${outputs[1]}: ${s.inputs} input(s), ${s.checked} checked, ${s.inputs_not_checked.length} not checked; ${s.diagnostics} diagnostic(s), ${s.actionable} actionable${s.diagnostics ? ` (${Object.entries(s.by_classification).map(([k, n]) => `${k}: ${n}`).join(', ')})` : ''}` };
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  const r = main(process.argv.slice(2));
  (r.exitCode === EXIT_CODES.usage_or_output_failure ? process.stderr : process.stdout).write(r.message + '\n');
  process.exit(r.exitCode);
}
