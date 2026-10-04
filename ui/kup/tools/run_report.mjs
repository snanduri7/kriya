#!/usr/bin/env node
/**
 * kup-run-report: offline run-diagnostics report from EXPLICITLY SUPPLIED KUP v1 JSON files (history.detail /
 * history.list envelopes as `kriya traces --json` prints them, or as a host saved them).
 *
 * Evidence tooling only: it reads only the files named on the command line (no store discovery, no acquisition, no
 * directory walk), invokes nothing (no Kriya, no model), imports nothing from kriya/, and writes only `report.md` and
 * `report.json` into the explicitly given output directory - refusing to overwrite an existing report without
 * --overwrite and never writing onto an input. Deterministic: the same inputs give byte-identical output (sorted keys,
 * supplied order, no clock).
 *
 * Every reported fact carries its source: the input file and a JSON pointer into it. Fields are Kriya's own
 * (kriya/workflow/run_events.py::RunEvent.to_dict: kind, attempt, source, authority, message, failure_type, operation,
 * details, created_at). Unknown fields are preserved and listed. Missing data stays "not recorded"; malformed data gets
 * an explicit diagnostic. The report never infers causal attribution, qualification, success or chronology: `status`
 * is the literal stored value, events are listed in recorded order, created_at is rendered as UTC and labelled.
 * Provider-reported token counts and Kriya's estimates are never mixed (developer.prompt_composition:
 * prompt_tokens_reported is the provider's; every other *_tokens field is an estimate; generation_metrics is shown as
 * recorded without interpreting its keys).
 *
 * usage: node tools/run_report.mjs --out <dir> [--overwrite] <kup-envelope.json>...
 */
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as validators from '../generated/validators.mjs';

export const TOOL = 'kup-run-report';
export const TOOL_VERSION = '0.1.0';
export const LABEL = 'Historical observations from the supplied KUP records, as recorded at their acquisition; not current state. Every fact names its input file and JSON pointer; nothing is inferred.';
export const EVENT_KEYS = ['kind', 'attempt', 'source', 'authority', 'message', 'failure_type', 'operation', 'details', 'created_at'];
export const DETAIL_SECTIONS = ['run_events', 'evidence_records', 'gate_outcomes', 'model_hops', 'generation_metrics', 'failure_report', 'context', 'attribution', 'diagnostics', 'comparisons', 'output'];
const DETAIL_KEYS = new Set(['run', 'fields', ...DETAIL_SECTIONS]);
const SUMMARY_KEYS = new Set(['run_id', 'timestamp', 'goal', 'duration_sec', 'attempts', 'status', 'failure_category', 'files_modified', 'milestone_group_id', 'milestone_index', 'milestone_total']);

// ---- helpers -------------------------------------------------------------------------------------------------------
export const pointer = (...parts) => (parts.length ? '/' + parts.map((p) => String(p).replace(/~/g, '~0').replace(/\//g, '~1')).join('/') : '');
const fact = (file, ptr, value) => ({ value, source: { file, pointer: ptr } });
const validatorErrors = (v) => (v.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? ''}`.trim());
const isObj = (x) => typeof x === 'object' && x !== null && !Array.isArray(x);
export function stableStringify(value, indent = 2) {
  const sort = (x) => Array.isArray(x) ? x.map(sort) : isObj(x) ? Object.fromEntries(Object.keys(x).sort().map((k) => [k, sort(x[k])])) : x;
  return JSON.stringify(sort(value), null, indent);
}
export function utcOf(createdAt) {
  if (typeof createdAt !== 'number' || !Number.isFinite(createdAt)) return null;
  const d = new Date(createdAt * 1000);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
const unknownKeys = (obj, known) => Object.keys(obj).filter((k) => !known.has(k)).sort();

// ---- one input file ------------------------------------------------------------------------------------------------
export function analyzeFile(file, text) {
  const diagnostics = [];
  const diag = (severity, ptr, message) => diagnostics.push({ file, pointer: ptr, severity, message });
  const input = { file, bytes: Buffer.byteLength(text, 'utf8'), sha256: createHash('sha256').update(text, 'utf8').digest('hex'), status: 'unread', operation: null, schema_version: null };
  let doc;
  try { doc = JSON.parse(text); } catch (e) { input.status = 'malformed_json'; diag('error', '', `not JSON: ${e instanceof Error ? e.message : String(e)}`); return { input, diagnostics }; }
  if (!isObj(doc)) { input.status = 'malformed_json'; diag('error', '', 'top-level JSON value is not an object'); return { input, diagnostics }; }
  input.operation = typeof doc.operation === 'string' ? doc.operation : null;
  input.schema_version = typeof doc.schema_version === 'number' ? doc.schema_version : null;
  if (!validators.validateEnvelope(doc)) {
    input.status = input.schema_version !== null && input.schema_version !== 1 ? 'unsupported_schema_version' : 'not_a_kup_envelope';
    for (const m of validatorErrors(validators.validateEnvelope)) diag('error', '', `envelope does not match KUP v1: ${m}`);
    return { input, diagnostics };
  }
  const envelope = { request_id: fact(file, pointer('request_id'), doc.request_id), observed_at: fact(file, pointer('observed_at'), doc.observed_at), source: fact(file, pointer('source'), doc.source), consistency: fact(file, pointer('consistency'), doc.consistency) };
  if (doc.error) {
    input.status = 'kup_error';
    return { input, diagnostics, error: { code: fact(file, pointer('error', 'code'), doc.error.code), message: fact(file, pointer('error', 'message'), doc.error.message), snapshot_id: fact(file, pointer('error', 'snapshot_id'), doc.error.snapshot_id ?? null), database_state: fact(file, pointer('error', 'database_state'), doc.error.database_state ?? null), envelope } };
  }
  if (doc.operation === 'history.detail') {
    if (!validators.validateRunDetail(doc.data)) {
      input.status = 'malformed_run_detail';
      for (const m of validatorErrors(validators.validateRunDetail)) diag('error', pointer('data'), `history.detail data does not match KUP v1 RunDetail: ${m}`);
      const runId = isObj(doc.data) && isObj(doc.data.run) && typeof doc.data.run.run_id === 'string' ? doc.data.run.run_id : null;
      return { input, diagnostics, run: { run_id: fact(file, pointer('data', 'run', 'run_id'), runId), malformed: true, envelope } };
    }
    input.status = 'run_detail';
    return { input, diagnostics, run: analyzeDetail(file, doc.data, envelope, diag) };
  }
  if (doc.operation === 'history.list') {
    if (!validators.validateHistoryList(doc.data)) { input.status = 'malformed_history_list'; for (const m of validatorErrors(validators.validateHistoryList)) diag('error', pointer('data'), `history.list data does not match KUP v1: ${m}`); return { input, diagnostics }; }
    input.status = 'run_list';
    return { input, diagnostics, summaries: doc.data.runs.map((r, i) => summaryOf(file, pointer('data', 'runs', i), r, envelope)) };
  }
  input.status = 'not_a_run_record';
  diag('info', pointer('operation'), `operation ${JSON.stringify(doc.operation)} carries no run records; listed only`);
  return { input, diagnostics };
}

function summaryOf(file, base, r, envelope) {
  const f = (k) => fact(file, `${base}${pointer(k)}`, r[k] === undefined ? null : r[k]);
  return {
    run_id: f('run_id'), status_as_recorded: f('status'), failure_category: f('failure_category'), attempts: f('attempts'), duration_sec: f('duration_sec'),
    timestamp_as_stored: f('timestamp'), goal: f('goal'), files_modified_raw: f('files_modified'),
    milestone: { group_id: f('milestone_group_id'), index: f('milestone_index'), total: f('milestone_total') },
    unknown_fields: unknownKeys(r, SUMMARY_KEYS).map((k) => f(k)),
    envelope,
  };
}

function sectionOf(file, base, section) {
  const recorded = section.availability === 'recorded';
  return { availability: fact(file, `${base}${pointer('availability')}`, section.availability), provenance: fact(file, `${base}${pointer('provenance')}`, section.provenance ?? null), reason: fact(file, `${base}${pointer('reason')}`, section.reason ?? null), recorded, data_pointer: recorded ? `${base}${pointer('data')}` : null };
}

function analyzeDetail(file, data, envelope, diag) {
  const run = summaryOf(file, pointer('data', 'run'), data.run, envelope);
  const sections = Object.fromEntries(DETAIL_SECTIONS.map((name) => [name, sectionOf(file, pointer('data', name), data[name])]));
  const fields = Object.fromEntries(Object.keys(data.fields).sort().map((name) => [name, sectionOf(file, pointer('data', 'fields', name), data.fields[name])]));
  const unknown_sections = unknownKeys(data, DETAIL_KEYS).map((k) => fact(file, pointer('data', k), data[k]));
  const out = { ...run, malformed: false, sections, fields, unknown_sections, events: null, attempts_in_events: null, context: { tiers: [], omissions: [], packages: [], section_items: null }, token_accounting: { prompt_compositions: [], context_section_tokens: null, generation_metrics_as_recorded: null }, gates: null, failures: { failure_category: run.failure_category, failure_report: null, events_with_failure_type: [] } };

  // events (recorded order; original index is the identity)
  if (sections.run_events.recorded) {
    const list = Array.isArray(data.run_events.data) ? data.run_events.data : null;
    if (!list) { diag('error', pointer('data', 'run_events', 'data'), 'run_events is recorded but its data is not a list'); }
    else {
      out.events = [];
      const perAttempt = new Map();
      list.forEach((e, i) => {
        const base = pointer('data', 'run_events', 'data', i);
        if (!isObj(e)) { diag('error', base, 'event is not an object'); out.events.push({ index: i, malformed: true, raw: fact(file, base, e) }); return; }
        const valid = validators.validateRunEvent(e);
        if (!valid) for (const m of validatorErrors(validators.validateRunEvent)) diag('error', base, `event does not match the serializer shape (kind, created_at required): ${m}`);
        const f = (k) => fact(file, `${base}${pointer(k)}`, e[k] === undefined ? null : e[k]);
        const createdAtUtc = utcOf(e.created_at);
        if (typeof e.created_at === 'number' && createdAtUtc === null) diag('warning', `${base}${pointer('created_at')}`, 'created_at is a number outside the representable date range');
        out.events.push({ index: i, malformed: !valid, kind: f('kind'), attempt: f('attempt'), source: f('source'), authority: f('authority'), message: f('message'), failure_type: f('failure_type'), operation: f('operation'), created_at: f('created_at'), created_at_utc: createdAtUtc, details_pointer: `${base}${pointer('details')}`, unknown_fields: unknownKeys(e, new Set(EVENT_KEYS)).map((k) => f(k)) });
        const key = typeof e.attempt === 'number' ? String(e.attempt) : 'not recorded';
        perAttempt.set(key, (perAttempt.get(key) ?? 0) + 1);
        if (e.failure_type !== null && e.failure_type !== undefined) out.failures.events_with_failure_type.push({ index: i, kind: f('kind'), attempt: f('attempt'), failure_type: f('failure_type'), message: f('message') });
        const d = isObj(e.details) ? e.details : null;
        if (e.kind === 'context.known_target_package' && d) {
          const dp = (...k) => `${base}${pointer('details', ...k)}`;
          out.context.packages.push({ event_index: i, attempt: f('attempt'), known_target_files: fact(file, dp('known_target_files'), d.known_target_files ?? null), unit_count: fact(file, dp('unit_count'), d.unit_count ?? null), package_hash: fact(file, dp('package_hash'), d.package_hash ?? null), member_hint_paths: fact(file, dp('member_hint_paths'), d.member_hint_paths ?? null) });
          if (Array.isArray(d.tiers)) d.tiers.forEach((t, j) => out.context.tiers.push({ event_index: i, attempt: f('attempt'), path: fact(file, dp('tiers', j, 'path'), isObj(t) ? t.path ?? null : null), member_id: fact(file, dp('tiers', j, 'member_id'), isObj(t) ? t.member_id ?? null : null), tier: fact(file, dp('tiers', j, 'tier'), isObj(t) ? t.tier ?? null : null), raw: isObj(t) ? undefined : fact(file, dp('tiers', j), t) }));
          else if (d.tiers !== undefined) diag('warning', dp('tiers'), 'details.tiers is not a list; shown raw');
          if (Array.isArray(d.omitted)) d.omitted.forEach((o, j) => out.context.omissions.push({ event_index: i, attempt: f('attempt'), path: fact(file, dp('omitted', j, 'path'), isObj(o) ? o.path ?? null : null), reason: fact(file, dp('omitted', j, 'reason'), isObj(o) ? o.reason ?? null : null), raw: isObj(o) ? undefined : fact(file, dp('omitted', j), o) }));
        }
        if (e.kind === 'developer.prompt_composition' && d) {
          const dp = (k) => `${base}${pointer('details', k)}`;
          const estimated = {}; const other = {};
          for (const k of Object.keys(d).sort()) {
            if (k === 'prompt_tokens_reported' || k === 'token_counts') continue;
            if (k.endsWith('_tokens')) estimated[k] = fact(file, dp(k), d[k]); else other[k] = fact(file, dp(k), d[k]);
          }
          out.token_accounting.prompt_compositions.push({ event_index: i, attempt: f('attempt'), provider_reported: { prompt_tokens_reported: fact(file, dp('prompt_tokens_reported'), d.prompt_tokens_reported ?? null) }, estimated_by_kriya: estimated, other_recorded: other, token_counts_note: fact(file, dp('token_counts'), d.token_counts ?? null) });
        }
      });
      out.attempts_in_events = Object.fromEntries([...perAttempt.entries()].sort(([a], [b]) => a.localeCompare(b, 'en', { numeric: true })));
    }
  }
  if (sections.context.recorded && isObj(data.context.data)) {
    const c = data.context.data; const cp = (...k) => pointer('data', 'context', 'data', ...k);
    out.context.section_items = Array.isArray(c.items) ? c.items.map((it, j) => ({ path: fact(file, cp('items', j, 'path'), isObj(it) ? it.path ?? null : null), tier: fact(file, cp('items', j, 'tier'), isObj(it) ? it.tier ?? null : null), member_ids: fact(file, cp('items', j, 'member_ids'), isObj(it) ? it.member_ids ?? null : null), omitted: fact(file, cp('items', j, 'omitted'), isObj(it) ? it.omitted ?? null : null), omission_reason: fact(file, cp('items', j, 'omission_reason'), isObj(it) ? it.omission_reason ?? null : null) })) : null;
    if (isObj(c.tokens)) out.token_accounting.context_section_tokens = { estimated: fact(file, cp('tokens', 'estimated'), c.tokens.estimated ?? null), provider_reported: fact(file, cp('tokens', 'provider_reported'), c.tokens.provider_reported ?? null) };
  }
  if (sections.generation_metrics.recorded) out.token_accounting.generation_metrics_as_recorded = fact(file, pointer('data', 'generation_metrics', 'data'), data.generation_metrics.data);
  if (sections.gate_outcomes.recorded) {
    const g = data.gate_outcomes.data;
    out.gates = Array.isArray(g) ? g.map((o, j) => { const b = pointer('data', 'gate_outcomes', 'data', j); const f = (k) => fact(file, `${b}${pointer(k)}`, isObj(o) ? (o[k] === undefined ? null : o[k]) : null); return { index: j, attempt: f('attempt'), gate: fact(file, `${b}${pointer(isObj(o) && o.gate !== undefined ? 'gate' : 'name')}`, isObj(o) ? o.gate ?? o.name ?? null : null), passed: f('passed'), reason_code: f('reason_code'), raw: fact(file, b, o) }; }) : [];
    if (!Array.isArray(g)) diag('warning', pointer('data', 'gate_outcomes', 'data'), 'gate_outcomes is recorded but not a list; shown raw');
  }
  if (sections.failure_report.recorded) out.failures.failure_report = fact(file, pointer('data', 'failure_report', 'data'), data.failure_report.data);
  return out;
}

// ---- the report ----------------------------------------------------------------------------------------------------
export function buildReport(files) {
  const results = files.map(({ file, text }) => analyzeFile(file, text));
  return {
    tool: TOOL, tool_version: TOOL_VERSION, label: LABEL,
    inputs: results.map((r) => r.input),
    runs: results.filter((r) => r.run).map((r) => r.run),
    run_summaries: results.flatMap((r) => r.summaries ?? []),
    errors: results.filter((r) => r.error).map((r) => ({ file: r.input.file, ...r.error })),
    diagnostics: results.flatMap((r) => r.diagnostics),
  };
}

const show = (f) => f && f.value !== undefined && f.value !== null ? (typeof f.value === 'string' ? f.value : JSON.stringify(f.value)) : 'not recorded';
const src = (f) => f ? `\`${f.source.file}#${f.source.pointer || '/'}\`` : '';
const cell = (s) => String(s).replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
export function renderMarkdown(report) {
  const L = [];
  L.push(`# ${TOOL} ${TOOL_VERSION}`, '', `> ${LABEL}`, '', '## Inputs (as supplied, in order)', '', '| file | bytes | sha256 | operation | schema_version | status |', '|---|---|---|---|---|---|');
  for (const i of report.inputs) L.push(`| ${cell(i.file)} | ${i.bytes} | ${i.sha256.slice(0, 16)}… | ${cell(i.operation ?? 'n/a')} | ${i.schema_version ?? 'n/a'} | ${i.status} |`);
  for (const run of report.runs) {
    L.push('', `## Run ${cell(show(run.run_id))}`, '', `Source record: ${src(run.run_id)}`);
    if (run.malformed) { L.push('', '**Malformed history.detail payload** - see Diagnostics; nothing below is reported for it.'); continue; }
    L.push('', '### Recorded outcome', '', '| fact | value (as recorded) | source |', '|---|---|---|');
    L.push(`| status (literal; not mapped to success or failure) | ${cell(show(run.status_as_recorded))} | ${src(run.status_as_recorded)} |`);
    L.push(`| failure_category | ${cell(show(run.failure_category))} | ${src(run.failure_category)} |`);
    L.push(`| attempts | ${cell(show(run.attempts))} | ${src(run.attempts)} |`);
    L.push(`| duration_sec | ${cell(show(run.duration_sec))} | ${src(run.duration_sec)} |`);
    L.push(`| timestamp (as stored; timezone not recorded) | ${cell(show(run.timestamp_as_stored))} | ${src(run.timestamp_as_stored)} |`);
    L.push(`| files_modified (raw, comma-joined) | ${cell(show(run.files_modified_raw))} | ${src(run.files_modified_raw)} |`);
    L.push(`| goal | ${cell(show(run.goal))} | ${src(run.goal)} |`);
    L.push(`| record consistency (envelope) | ${cell(show(run.envelope.consistency))} | ${src(run.envelope.consistency)} |`);
    if (run.unknown_fields.length) L.push('', `Unknown run fields preserved: ${run.unknown_fields.map((f) => `${f.source.pointer.split('/').pop()}=${cell(show(f))} (${src(f)})`).join('; ')}`);
    L.push('', '### Section availability', '', '| section | availability | provenance | reason |', '|---|---|---|---|');
    const reason = (s) => (s.recorded && (s.reason.value === null || s.reason.value === undefined) ? '-' : cell(show(s.reason)));
    for (const [name, s] of Object.entries(run.sections)) L.push(`| ${name} | ${cell(show(s.availability))} | ${cell(show(s.provenance))} | ${reason(s)} |`);
    for (const [name, s] of Object.entries(run.fields)) L.push(`| fields.${name} | ${cell(show(s.availability))} | ${cell(show(s.provenance))} | ${reason(s)} |`);
    if (run.unknown_sections.length) L.push('', `Unknown detail sections preserved: ${run.unknown_sections.map((f) => `${f.source.pointer.split('/').pop()} (${src(f)})`).join('; ')}`);
    L.push('', '### Attempts');
    L.push('', `Attempts as recorded on the run row: ${cell(show(run.attempts))} (${src(run.attempts)}).`);
    L.push(run.attempts_in_events ? `Attempt values present in recorded events (count of events per value; presence only, not completeness): ${Object.entries(run.attempts_in_events).map(([k, n]) => `${k}: ${n}`).join(', ')}.` : 'Run events: not recorded, so no per-attempt event counts.');
    L.push('', '### Recorded events (recorded order; created_at rendered as UTC)');
    if (!run.events) L.push('', 'not recorded');
    else {
      L.push('', '| # | kind | attempt | source / authority | created_at (UTC) | failure_type | unknown fields | source |', '|---|---|---|---|---|---|---|---|');
      for (const e of run.events) {
        if (e.raw) { L.push(`| ${e.index + 1} | (malformed, see Diagnostics) | | | | | | ${src(e.raw)} |`); continue; }
        const label = e.malformed && e.kind.value === null ? '(malformed, see Diagnostics)' : `${cell(show(e.kind))}${e.malformed ? ' (malformed)' : ''}`;
        L.push(`| ${e.index + 1} | ${label} | ${cell(show(e.attempt))} | ${cell(show(e.source))} / ${cell(show(e.authority))} | ${e.created_at_utc ?? 'not recorded'} | ${cell(show(e.failure_type))} | ${e.unknown_fields.length ? e.unknown_fields.map((f) => f.source.pointer.split('/').pop()).join(', ') : '-'} | ${src(e.kind)} |`);
      }
    }
    L.push('', '### Context tiers and omissions (from context.known_target_package events and the context section)');
    if (!run.context.tiers.length && !run.context.omissions.length && !run.context.section_items) L.push('', 'not recorded');
    else {
      if (run.context.tiers.length) { L.push('', '| event # | attempt | path | member_id | tier | source |', '|---|---|---|---|---|---|'); for (const t of run.context.tiers) L.push(`| ${t.event_index + 1} | ${cell(show(t.attempt))} | ${cell(show(t.path))} | ${cell(show(t.member_id))} | ${cell(show(t.tier))} | ${src(t.tier)} |`); }
      if (run.context.omissions.length) { L.push('', '| event # | attempt | omitted path | recorded reason | source |', '|---|---|---|---|---|'); for (const o of run.context.omissions) L.push(`| ${o.event_index + 1} | ${cell(show(o.attempt))} | ${cell(show(o.path))} | ${cell(show(o.reason))} | ${src(o.reason)} |`); }
      for (const p of run.context.packages) L.push('', `Package (event #${p.event_index + 1}): known_target_files=${cell(show(p.known_target_files))}, unit_count=${cell(show(p.unit_count))}, package_hash=${cell(show(p.package_hash))}, member_hint_paths=${cell(show(p.member_hint_paths))} (${src(p.package_hash)})`);
      if (run.context.section_items) { L.push('', '| context section item | tier | member_ids | omitted | omission_reason | source |', '|---|---|---|---|---|---|'); for (const it of run.context.section_items) L.push(`| ${cell(show(it.path))} | ${cell(show(it.tier))} | ${cell(show(it.member_ids))} | ${cell(show(it.omitted))} | ${cell(show(it.omission_reason))} | ${src(it.path)} |`); }
    }
    L.push('', '### Token accounting (provider-reported and Kriya estimates kept apart)');
    const ta = run.token_accounting;
    if (!ta.prompt_compositions.length && !ta.context_section_tokens && !ta.generation_metrics_as_recorded) L.push('', 'not recorded');
    for (const pc of ta.prompt_compositions) {
      L.push('', `developer.prompt_composition, event #${pc.event_index + 1}, attempt ${cell(show(pc.attempt))}:`, '', '| kind | field | value | source |', '|---|---|---|---|');
      L.push(`| provider-reported | prompt_tokens_reported | ${cell(show(pc.provider_reported.prompt_tokens_reported))} | ${src(pc.provider_reported.prompt_tokens_reported)} |`);
      for (const [k, f] of Object.entries(pc.estimated_by_kriya)) L.push(`| estimated (Kriya) | ${k} | ${cell(show(f))} | ${src(f)} |`);
      for (const [k, f] of Object.entries(pc.other_recorded)) L.push(`| other recorded | ${k} | ${cell(show(f))} | ${src(f)} |`);
      L.push(`| recorded note | token_counts | ${cell(show(pc.token_counts_note))} | ${src(pc.token_counts_note)} |`);
    }
    if (ta.context_section_tokens) L.push('', `Context section tokens: estimated=${cell(show(ta.context_section_tokens.estimated))} (${src(ta.context_section_tokens.estimated)}); provider_reported=${cell(show(ta.context_section_tokens.provider_reported))} (${src(ta.context_section_tokens.provider_reported)})`);
    if (ta.generation_metrics_as_recorded) L.push('', `generation_metrics as recorded (keys not interpreted): ${cell(show(ta.generation_metrics_as_recorded))} (${src(ta.generation_metrics_as_recorded)})`);
    L.push('', '### Gates');
    if (!run.gates) L.push('', 'not recorded');
    else if (!run.gates.length) L.push('', 'recorded: empty list');
    else { L.push('', '| # | attempt | gate | passed | reason_code | source |', '|---|---|---|---|---|---|'); for (const g of run.gates) L.push(`| ${g.index + 1} | ${cell(show(g.attempt))} | ${cell(show(g.gate))} | ${cell(show(g.passed))} | ${cell(show(g.reason_code))} | ${src(g.raw)} |`); }
    L.push('', '### Failures (recorded; no causal attribution)');
    L.push('', `failure_category: ${cell(show(run.failures.failure_category))} (${src(run.failures.failure_category)})`);
    L.push(run.failures.failure_report ? `failure_report as recorded: ${cell(show(run.failures.failure_report))} (${src(run.failures.failure_report)})` : 'failure_report: not recorded');
    if (run.failures.events_with_failure_type.length) { L.push('', '| event # | kind | attempt | failure_type | message | source |', '|---|---|---|---|---|---|'); for (const e of run.failures.events_with_failure_type) L.push(`| ${e.index + 1} | ${cell(show(e.kind))} | ${cell(show(e.attempt))} | ${cell(show(e.failure_type))} | ${cell(show(e.message))} | ${src(e.failure_type)} |`); }
    else L.push('Events carrying a failure_type: none recorded.');
  }
  if (report.run_summaries.length) {
    L.push('', '## Run summaries (history.list records; outcomes only)', '', '| run_id | status (literal) | failure_category | attempts | timestamp (as stored) | source |', '|---|---|---|---|---|---|');
    for (const s of report.run_summaries) L.push(`| ${cell(show(s.run_id))} | ${cell(show(s.status_as_recorded))} | ${cell(show(s.failure_category))} | ${cell(show(s.attempts))} | ${cell(show(s.timestamp_as_stored))} | ${src(s.run_id)} |`);
  }
  if (report.errors.length) { L.push('', '## KUP error envelopes (typed refusals recorded by Kriya; not run records)', '', '| file | code | message | snapshot_id | database_state |', '|---|---|---|---|---|'); for (const e of report.errors) L.push(`| ${cell(e.file)} | ${cell(show(e.code))} | ${cell(show(e.message))} | ${cell(show(e.snapshot_id))} | ${cell(show(e.database_state))} |`); }
  L.push('', '## Diagnostics');
  if (!report.diagnostics.length) L.push('', 'none');
  else { L.push('', '| severity | file | pointer | message |', '|---|---|---|---|'); for (const d of report.diagnostics) L.push(`| ${d.severity} | ${cell(d.file)} | ${cell(d.pointer || '/')} | ${cell(d.message)} |`); }
  L.push('');
  return L.join('\n');
}

// ---- CLI -----------------------------------------------------------------------------------------------------------
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
  if (!files.length) return { error: 'name at least one KUP JSON file explicitly (directories are not read)' };
  return { out, overwrite, files };
}

export function main(argv) {
  const args = parseArgs(argv);
  if (args.error) return { exitCode: 2, message: `${args.error}\nusage: node tools/run_report.mjs --out <dir> [--overwrite] <kup-envelope.json>...` };
  const outDir = resolve(args.out);
  const outputs = [join(outDir, 'report.md'), join(outDir, 'report.json')];
  const inputs = [];
  for (const file of args.files) {
    const path = resolve(file);
    if (!existsSync(path)) return { exitCode: 2, message: `input does not exist: ${file}` };
    if (statSync(path).isDirectory()) return { exitCode: 2, message: `input is a directory, not a file (directories are never read): ${file}` };
    const real = realpathSync(path);
    if (outputs.some((o) => existsSync(o) && realpathSync(o) === real) || outputs.includes(path)) return { exitCode: 2, message: `refusing: an output path would overwrite the input ${file}` };
    inputs.push({ file, text: readFileSync(path, 'utf8') });
  }
  for (const o of outputs) if (existsSync(o) && !args.overwrite) return { exitCode: 2, message: `refusing to overwrite existing ${o} (pass --overwrite to replace it)` };
  if (existsSync(outDir) && !statSync(outDir).isDirectory()) return { exitCode: 2, message: `--out is not a directory: ${args.out}` };
  const report = buildReport(inputs);
  mkdirSync(outDir, { recursive: true });
  writeFileSync(outputs[1], stableStringify(report) + '\n');
  writeFileSync(outputs[0], renderMarkdown(report));
  return { exitCode: 0, message: `wrote ${outputs[0]} and ${outputs[1]}: ${report.runs.length} run record(s), ${report.run_summaries.length} summary row(s), ${report.errors.length} error envelope(s), ${report.diagnostics.length} diagnostic(s)` };
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  const r = main(process.argv.slice(2));
  (r.exitCode === 0 ? process.stdout : process.stderr).write(r.message + '\n');
  process.exit(r.exitCode);
}
