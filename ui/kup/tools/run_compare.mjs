#!/usr/bin/env node
/**
 * kup-run-compare: offline, field-by-field comparison of EXACTLY TWO explicitly supplied KUP history.detail JSON files,
 * labelled Left and Right (caller-selected historical records). Built on tools/run_report.mjs: the same validators, the
 * same sourced facts (file + JSON pointer), the same Markdown cell escaping and the same documented token-field mapping.
 *
 * Compares recorded facts only: run identity/goal/status/attempts; context paths, tiers and omission reasons; the
 * documented developer.prompt_composition fields; gate outcomes; recorded failures. Every value is shown with its own
 * source pointer on both sides, with the outcome equal / changed / added (only in Right) / removed (only in Left) /
 * unavailable (a side's section is not recorded). Records are matched only through explicit identifiers or unambiguous
 * keys ((attempt, path), (attempt, field), (attempt, gate), ...); a key that occurs more than once on a side is listed
 * under "ambiguous" with every pointer, never matched by guess. Original attempt and event identities are kept.
 * Numeric differences (Right minus Left) are computed only for documented fields with a stated unit when both values are
 * finite numbers; unknown fields are listed uninterpreted and never compared. The two runs are NOT a controlled
 * experiment: a difference is an observation, never a cause, a ranking or a verdict on success.
 *
 * Writes compare.md and compare.json into an explicit --out directory; refuses to overwrite an existing comparison
 * without --overwrite and never writes onto an input. Deterministic: sorted keys, stable item order, no clock. Reads only
 * the two named files; invokes nothing.
 *
 * usage: node tools/run_compare.mjs --left <left.json> --right <right.json> --out <dir> [--overwrite]
 */
import { existsSync, mkdirSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { PROMPT_COMPOSITION_FIELDS, analyzeFile, cell, stableStringify } from './run_report.mjs';
export { stableStringify } from './run_report.mjs';

export const TOOL = 'kup-run-compare';
export const TOOL_VERSION = '0.1.0';
export const LABEL = 'Two caller-selected historical KUP records (Left, Right) compared field by field, as recorded at their acquisition. Not a controlled experiment: a difference is an observation, never a cause, a ranking or a verdict on success. Every value names its input file and JSON pointer; nothing is inferred.';
export const OUTCOMES = ['equal', 'changed', 'added', 'removed', 'unavailable'];
/** Units of the numeric fields a difference is computed for (Right minus Left). Anything else is never subtracted. */
export const NUMERIC_UNITS = Object.freeze({
  attempts: 'attempts (run row count)', duration_sec: 'seconds (run row duration_sec)',
  ...Object.fromEntries(PROMPT_COMPOSITION_FIELDS.estimated_by_kriya.map((k) => [k, 'tokens, estimated by Kriya (len/4)'])),
  prompt_tokens_reported: 'tokens, provider-reported', prefill_seconds: 'seconds, provider-reported', load_seconds: 'seconds, provider-reported',
});

const same = (a, b) => stableStringify(a, 0) === stableStringify(b, 0);
const has = (f) => f && f.value !== null && f.value !== undefined;
const finite = (x) => typeof x === 'number' && Number.isFinite(x);
const diffOf = (field, l, r) => (field in NUMERIC_UNITS && has(l) && has(r) && finite(l.value) && finite(r.value) ? { right_minus_left: r.value - l.value, unit: NUMERIC_UNITS[field] } : null);

/** One compared value. */
function compareFacts(key, l, r, { field = null } = {}) {
  if (!has(l) && !has(r)) return { key, outcome: 'equal', both_not_recorded: true, left: l ?? null, right: r ?? null, difference: null };
  if (has(l) && !has(r)) return { key, outcome: 'removed', left: l, right: r ?? null, difference: null };
  if (!has(l) && has(r)) return { key, outcome: 'added', left: l ?? null, right: r, difference: null };
  return { key, outcome: same(l.value, r.value) ? 'equal' : 'changed', left: l, right: r, difference: field ? diffOf(field, l, r) : null };
}

/** Match two lists by an explicit key; keys seen more than once on a side are ambiguous and never matched. */
export function matchByKey(left, right, keyOf) {
  const group = (items) => { const m = new Map(); items.forEach((it, i) => { const k = keyOf(it); if (k === null) return; (m.get(k) ?? m.set(k, []).get(k)).push({ it, i }); }); return m; };
  const L = group(left), R = group(right);
  const ambiguous = [];
  for (const [side, m] of [['left', L], ['right', R]]) for (const [k, xs] of m) if (xs.length > 1) ambiguous.push({ side, key: k, count: xs.length, items: xs.map((x) => x.it) });
  const ambL = new Set([...L].filter(([, xs]) => xs.length > 1).map(([k]) => k)), ambR = new Set([...R].filter(([, xs]) => xs.length > 1).map(([k]) => k));
  const keys = [...new Set([...L.keys(), ...R.keys()])].sort((a, b) => a.localeCompare(b, 'en', { numeric: true }));
  const matched = [], onlyLeft = [], onlyRight = [];
  for (const k of keys) {
    if (ambL.has(k) || ambR.has(k)) continue;
    const l = L.get(k)?.[0]?.it, r = R.get(k)?.[0]?.it;
    if (l && r) matched.push({ key: k, left: l, right: r }); else if (l) onlyLeft.push({ key: k, left: l }); else onlyRight.push({ key: k, right: r });
  }
  return { matched, onlyLeft, onlyRight, ambiguous, unkeyed: { left: left.filter((it) => keyOf(it) === null).length, right: right.filter((it) => keyOf(it) === null).length } };
}

const attemptKey = (f) => (has(f) ? String(f.value) : 'attempt not recorded');
const sideAvail = (run, section) => ({ availability: run.sections[section].availability, reason: run.sections[section].reason });
const unavailable = (key, leftRun, rightRun, section) => ({ key, outcome: 'unavailable', section, left: sideAvail(leftRun, section), right: sideAvail(rightRun, section) });

export function compareRuns(left, right) {
  const out = { identity: [], context: null, tokens: null, gates: null, failures: null, events: null };
  for (const [key, field] of [['run_id', null], ['goal', null], ['status (literal)', null], ['attempts', 'attempts'], ['failure_category', null], ['timestamp (as stored)', null], ['duration_sec', 'duration_sec'], ['files_modified (raw)', null]]) {
    const prop = { 'run_id': 'run_id', 'goal': 'goal', 'status (literal)': 'status_as_recorded', 'attempts': 'attempts', 'failure_category': 'failure_category', 'timestamp (as stored)': 'timestamp_as_stored', 'duration_sec': 'duration_sec', 'files_modified (raw)': 'files_modified_raw' }[key];
    out.identity.push(compareFacts(key, left[prop], right[prop], { field }));
  }
  // ---- events: counts and attempt identities per side (never matched event-to-event: no explicit event identifier exists)
  const evAvail = [left.sections.run_events.recorded, right.sections.run_events.recorded];
  out.events = evAvail.every(Boolean)
    ? { outcome: left.events.length === right.events.length ? 'equal' : 'changed', left_count: left.events.length, right_count: right.events.length, attempts_in_events: { left: left.attempts_in_events, right: right.attempts_in_events }, kinds: compareKindCounts(left.events, right.events), note: 'events are not matched one to one: Kriya records no per-event identifier; counts per kind and per attempt are compared, event identities stay the recorded indices' }
    : unavailable('run_events', left, right, 'run_events');
  // ---- context: (attempt, path) from context.known_target_package details; the fixture-only context section by path
  if (!evAvail.every(Boolean)) out.context = { tiers: unavailable('context tiers', left, right, 'run_events'), omissions: unavailable('context omissions', left, right, 'run_events'), ambiguous: [], section_items: compareSectionItems(left, right) };
  else {
    const tierKey = (t) => (t.path && has(t.path) ? `attempt ${attemptKey(t.attempt)} | ${t.path.value}` : null);
    const tiers = matchByKey(left.context.tiers, right.context.tiers, tierKey);
    const omissions = matchByKey(left.context.omissions, right.context.omissions, (o) => (o.path && has(o.path) ? `attempt ${attemptKey(o.attempt)} | ${o.path.value}` : null));
    const tierItems = [...tiers.matched.map((m) => ({ ...compareFacts(m.key, m.left.tier, m.right.tier), member_id: compareFacts(m.key, m.left.member_id, m.right.member_id) })), ...tiers.onlyLeft.map((m) => ({ ...compareFacts(m.key, m.left.tier, null), member_id: compareFacts(m.key, m.left.member_id, null) })), ...tiers.onlyRight.map((m) => ({ ...compareFacts(m.key, null, m.right.tier), member_id: compareFacts(m.key, null, m.right.member_id) }))];
    const omissionItems = [...omissions.matched.map((m) => compareFacts(m.key, m.left.reason, m.right.reason)), ...omissions.onlyLeft.map((m) => compareFacts(m.key, m.left.reason, null)), ...omissions.onlyRight.map((m) => compareFacts(m.key, null, m.right.reason))];
    // a path tiered on one side and omitted on the other is a recorded difference in treatment, shown as such
    const crossed = [];
    for (const t of [...tiers.matched, ...tiers.onlyLeft, ...tiers.onlyRight]) for (const o of [...omissions.matched, ...omissions.onlyLeft, ...omissions.onlyRight]) if (t.key === o.key) crossed.push({ key: t.key, left: { tier: t.left?.tier ?? null, omission_reason: o.left?.reason ?? null }, right: { tier: t.right?.tier ?? null, omission_reason: o.right?.reason ?? null } });
    out.context = { tiers: sortItems(tierItems), omissions: sortItems(omissionItems), tiered_on_one_side_omitted_on_the_other: crossed, ambiguous: [...tiers.ambiguous.map((a) => ({ ...a, what: 'context tier' })), ...omissions.ambiguous.map((a) => ({ ...a, what: 'context omission' }))], unkeyed: { tiers: tiers.unkeyed, omissions: omissions.unkeyed }, section_items: compareSectionItems(left, right) };
  }
  // ---- tokens: documented prompt_composition fields matched by (attempt, field); several compositions in one attempt are ambiguous
  if (!evAvail.every(Boolean)) out.tokens = unavailable('token accounting', left, right, 'run_events');
  else {
    const byAttempt = matchByKey(left.token_accounting.prompt_compositions, right.token_accounting.prompt_compositions, (pc) => `attempt ${attemptKey(pc.attempt)}`);
    const PC = PROMPT_COMPOSITION_FIELDS;
    const groups = [['provider_reported', PC.provider_reported], ['estimated_by_kriya', PC.estimated_by_kriya], ['provider_timing', PC.provider_timing], ['recorded_other', PC.recorded_other]];
    const items = [];
    const push = (attempt, l, r) => { for (const [g, names] of groups) for (const name of names) items.push({ ...compareFacts(`attempt ${attempt} | ${name}`, l?.[g]?.[name] ?? null, r?.[g]?.[name] ?? null, { field: name }), classification: g }); };
    for (const m of byAttempt.matched) push(m.key.slice(8), m.left, m.right);
    for (const m of byAttempt.onlyLeft) push(m.key.slice(8), m.left, null);
    for (const m of byAttempt.onlyRight) push(m.key.slice(8), null, m.right);
    const unknown = (side, pcs) => pcs.flatMap((pc) => Object.entries(pc.unknown_uninterpreted).map(([name, f]) => ({ side, attempt: attemptKey(pc.attempt), field: name, fact: f })));
    out.tokens = { items: items.filter((it) => !(it.both_not_recorded && !it.left && !it.right)), ambiguous: byAttempt.ambiguous.map((a) => ({ ...a, what: 'developer.prompt_composition' })), unknown_uninterpreted: [...unknown('left', left.token_accounting.prompt_compositions), ...unknown('right', right.token_accounting.prompt_compositions)], generation_metrics_as_recorded: { left: left.token_accounting.generation_metrics_as_recorded, right: right.token_accounting.generation_metrics_as_recorded, note: 'shown as recorded on each side; keys are not interpreted, so no comparison is made' } };
  }
  // ---- gates: (attempt, gate) when unique per side
  if (!(left.sections.gate_outcomes.recorded && right.sections.gate_outcomes.recorded)) out.gates = unavailable('gate outcomes', left, right, 'gate_outcomes');
  else {
    const m = matchByKey(left.gates, right.gates, (g) => (has(g.gate) ? `attempt ${attemptKey(g.attempt)} | ${g.gate.value}` : null));
    const item = (key, l, r) => ({ key, passed: compareFacts(key, l?.passed ?? null, r?.passed ?? null), reason_code: compareFacts(key, l?.reason_code ?? null, r?.reason_code ?? null), outcome: l && r ? (same(l.passed.value, r.passed.value) && same(l.reason_code.value, r.reason_code.value) ? 'equal' : 'changed') : l ? 'removed' : 'added', left_source: l?.raw ?? null, right_source: r?.raw ?? null });
    out.gates = { items: sortItems([...m.matched.map((x) => item(x.key, x.left, x.right)), ...m.onlyLeft.map((x) => item(x.key, x.left, null)), ...m.onlyRight.map((x) => item(x.key, null, x.right))]), ambiguous: m.ambiguous.map((a) => ({ ...a, what: 'gate outcome' })), unkeyed: m.unkeyed };
  }
  // ---- failures
  const fr = left.sections.failure_report.recorded && right.sections.failure_report.recorded ? compareFacts('failure_report (whole list; entries carry no identifier)', left.failures.failure_report, right.failures.failure_report) : unavailable('failure_report', left, right, 'failure_report');
  let evFail;
  if (evAvail.every(Boolean)) {
    const m = matchByKey(left.failures.events_with_failure_type, right.failures.events_with_failure_type, (e) => `attempt ${attemptKey(e.attempt)} | ${has(e.kind) ? e.kind.value : 'kind not recorded'} | ${e.failure_type.value}`);
    evFail = { items: sortItems([...m.matched.map((x) => ({ key: x.key, outcome: 'equal', left: x.left.failure_type, right: x.right.failure_type })), ...m.onlyLeft.map((x) => ({ key: x.key, outcome: 'removed', left: x.left.failure_type, right: null })), ...m.onlyRight.map((x) => ({ key: x.key, outcome: 'added', left: null, right: x.right.failure_type }))]), ambiguous: m.ambiguous.map((a) => ({ ...a, what: 'event with failure_type' })) };
  } else evFail = unavailable('events with failure_type', left, right, 'run_events');
  out.failures = { failure_category: compareFacts('failure_category', left.failures.failure_category, right.failures.failure_category), failure_report: fr, events_with_failure_type: evFail };
  return out;
}

const sortItems = (items) => [...items].sort((a, b) => a.key.localeCompare(b.key, 'en', { numeric: true }));
function compareKindCounts(l, r) {
  const count = (events) => { const m = {}; for (const e of events) { const k = has(e.kind) ? e.kind.value : '(malformed: kind missing)'; m[k] = (m[k] ?? 0) + 1; } return m; };
  const L = count(l), R = count(r);
  return [...new Set([...Object.keys(L), ...Object.keys(R)])].sort().map((kind) => ({ kind, left_count: L[kind] ?? 0, right_count: R[kind] ?? 0, outcome: (L[kind] ?? 0) === (R[kind] ?? 0) ? 'equal' : !L[kind] ? 'added' : !R[kind] ? 'removed' : 'changed' }));
}
function compareSectionItems(left, right) {
  if (!(left.sections.context.recorded && right.sections.context.recorded)) return unavailable('context section items', left, right, 'context');
  const m = matchByKey(left.context.section_items ?? [], right.context.section_items ?? [], (it) => (it.path && has(it.path) ? it.path.value : null));
  const item = (key, l, r) => ({ key, tier: compareFacts(key, l?.tier ?? null, r?.tier ?? null), omitted: compareFacts(key, l?.omitted ?? null, r?.omitted ?? null), omission_reason: compareFacts(key, l?.omission_reason ?? null, r?.omission_reason ?? null), outcome: l && r ? (same([l.tier.value, l.omitted.value, l.omission_reason.value], [r.tier.value, r.omitted.value, r.omission_reason.value]) ? 'equal' : 'changed') : l ? 'removed' : 'added' });
  return { items: sortItems([...m.matched.map((x) => item(x.key, x.left, x.right)), ...m.onlyLeft.map((x) => item(x.key, x.left, null)), ...m.onlyRight.map((x) => item(x.key, null, x.right))]), ambiguous: m.ambiguous.map((a) => ({ ...a, what: 'context section item' })) };
}

export function buildComparison(leftInput, rightInput) {
  const L = analyzeFile(leftInput.file, leftInput.text), R = analyzeFile(rightInput.file, rightInput.text);
  const problems = [];
  for (const [side, a] of [['Left', L], ['Right', R]]) if (a.input.status !== 'run_detail') problems.push(`${side} (${a.input.file}) is not a valid KUP history.detail record: status ${a.input.status}${a.diagnostics.length ? ' - ' + a.diagnostics.map((d) => d.message).join('; ') : ''}`);
  if (problems.length) return { ok: false, problems, inputs: [L.input, R.input], diagnostics: [...L.diagnostics, ...R.diagnostics] };
  return { ok: true, report: { tool: TOOL, tool_version: TOOL_VERSION, label: LABEL, left: { ...L.input, run_id: L.run.run_id, consistency: L.run.envelope.consistency }, right: { ...R.input, run_id: R.run.run_id, consistency: R.run.envelope.consistency }, comparison: compareRuns(L.run, R.run), diagnostics: [...L.diagnostics, ...R.diagnostics] } };
}

// ---- Markdown ------------------------------------------------------------------------------------------------------
const show = (f) => (has(f) ? (typeof f.value === 'string' ? f.value : JSON.stringify(f.value)) : f && f.source && f.source.absent ? 'not recorded (field absent)' : f && f.source ? 'not recorded' : '(no record)');
const src = (f) => (f && f.source ? `\`${cell(f.source.file)}#${cell(f.source.pointer || '/')}\`` : '');
const diffCell = (d) => (d ? `${d.right_minus_left > 0 ? '+' : ''}${d.right_minus_left} ${cell(d.unit)}` : '-');
const row = (it, note = '') => `| ${cell(it.key)} | ${cell(show(it.left))} | ${cell(show(it.right))} | ${it.outcome}${it.both_not_recorded ? ' (both not recorded)' : ''}${note} | ${diffCell(it.difference)} | ${src(it.left)} | ${src(it.right)} |`;
const HEAD = ['| key | Left (as recorded) | Right (as recorded) | outcome | Right - Left (unit) | Left source | Right source |', '|---|---|---|---|---|---|---|'];
const unavailableRow = (u) => `| ${cell(u.key)} | ${cell(u.left.availability.value ?? 'n/a')}${has(u.left.reason) ? ` (${cell(u.left.reason.value)})` : ''} | ${cell(u.right.availability.value ?? 'n/a')}${has(u.right.reason) ? ` (${cell(u.right.reason.value)})` : ''} | unavailable | - | ${src(u.left.availability)} | ${src(u.right.availability)} |`;
const ambiguousRows = (L, list) => { if (!list.length) return; L.push('', 'Ambiguous (same key more than once on one side; listed, never matched):', '', '| side | what | key | occurrences | sources |', '|---|---|---|---|---|'); for (const a of list) L.push(`| ${a.side} | ${cell(a.what)} | ${cell(a.key)} | ${a.count} | ${a.items.map((it) => src(it.raw ?? it.path ?? it.tier ?? it.reason ?? it.attempt ?? it.failure_type ?? it.kind ?? it.provider_reported?.prompt_tokens_reported)).join(' ')} |`); };

export function renderMarkdown(report) {
  const c = report.comparison;
  const L = [`# ${TOOL} ${TOOL_VERSION}`, '', `> ${LABEL}`, '', '| side | file | sha256 | run_id | record consistency |', '|---|---|---|---|---|'];
  for (const [side, s] of [['Left', report.left], ['Right', report.right]]) L.push(`| ${side} | ${cell(s.file)} | ${s.sha256.slice(0, 16)}… | ${cell(show(s.run_id))} | ${cell(show(s.consistency))} |`);
  L.push('', 'Outcomes: equal / changed / added (present only in Right) / removed (present only in Left) / unavailable (a side has not recorded the section). Differences are Right minus Left, only for documented numeric fields with the stated unit.');
  L.push('', '## Run identity and recorded outcome', '', ...HEAD); for (const it of c.identity) L.push(row(it));
  L.push('', '## Recorded events (counts; events carry no identifier and are not matched one to one)');
  if (c.events.outcome === 'unavailable') L.push('', ...HEAD, unavailableRow(c.events));
  else { L.push('', `Left: ${c.events.left_count} events; Right: ${c.events.right_count} events (${c.events.outcome}). Attempt values in events - Left: ${cell(JSON.stringify(c.events.attempts_in_events.left))}; Right: ${cell(JSON.stringify(c.events.attempts_in_events.right))}.`, '', '| kind | Left count | Right count | outcome |', '|---|---|---|---|'); for (const k of c.events.kinds) L.push(`| ${cell(k.kind)} | ${k.left_count} | ${k.right_count} | ${k.outcome} |`); }
  L.push('', '## Context tiers (key: attempt | path, from context.known_target_package details.tiers)', '', ...HEAD);
  if (c.context.tiers.outcome === 'unavailable') L.push(unavailableRow(c.context.tiers)); else if (!c.context.tiers.length) L.push('| (no tiers recorded on either side) | | | | | | |'); else for (const it of c.context.tiers) L.push(row(it, it.member_id && it.member_id.outcome !== 'equal' ? `; member_id ${it.member_id.outcome}` : ''));
  L.push('', '## Context omissions (key: attempt | path, from details.omitted; value = recorded reason)', '', ...HEAD);
  if (c.context.omissions.outcome === 'unavailable') L.push(unavailableRow(c.context.omissions)); else if (!c.context.omissions.length) L.push('| (no omissions recorded on either side) | | | | | | |'); else for (const it of c.context.omissions) L.push(row(it));
  if (c.context.tiered_on_one_side_omitted_on_the_other?.length) { L.push('', 'Paths tiered on one side and omitted on the other (recorded treatment differs):', '', '| key | Left tier | Left omission reason | Right tier | Right omission reason |', '|---|---|---|---|---|'); for (const x of c.context.tiered_on_one_side_omitted_on_the_other) L.push(`| ${cell(x.key)} | ${cell(show(x.left.tier))} | ${cell(show(x.left.omission_reason))} | ${cell(show(x.right.tier))} | ${cell(show(x.right.omission_reason))} |`); }
  ambiguousRows(L, c.context.ambiguous ?? []);
  const si = c.context.section_items;
  L.push('', '## Context section items (key: path; fixture-only section in this Kriya version)', '', ...HEAD);
  if (si.outcome === 'unavailable') L.push(unavailableRow(si)); else if (!si.items.length) L.push('| (no items on either side) | | | | | | |'); else for (const it of si.items) L.push(`| ${cell(it.key)} | tier ${cell(show(it.tier.left))}, omitted ${cell(show(it.omitted.left))}, reason ${cell(show(it.omission_reason.left))} | tier ${cell(show(it.tier.right))}, omitted ${cell(show(it.omitted.right))}, reason ${cell(show(it.omission_reason.right))} | ${it.outcome} | - | ${src(it.tier.left)} | ${src(it.tier.right)} |`);
  if (si.ambiguous) ambiguousRows(L, si.ambiguous);
  L.push('', '## Token accounting (documented developer.prompt_composition fields; key: attempt | field)', '', ...HEAD);
  if (c.tokens.outcome === 'unavailable') L.push(unavailableRow(c.tokens));
  else {
    for (const it of c.tokens.items) L.push(row(it));
    ambiguousRows(L, c.tokens.ambiguous);
    if (c.tokens.unknown_uninterpreted.length) { L.push('', 'Unknown prompt_composition fields (uninterpreted, not compared):', '', '| side | attempt | field | value (as recorded) | source |', '|---|---|---|---|---|'); for (const u of c.tokens.unknown_uninterpreted) L.push(`| ${u.side} | ${cell(u.attempt)} | ${cell(u.field)} | ${cell(show(u.fact))} | ${src(u.fact)} |`); }
    L.push('', `generation_metrics as recorded (keys not interpreted; not compared) - Left: ${cell(show(c.tokens.generation_metrics_as_recorded.left))} ${src(c.tokens.generation_metrics_as_recorded.left)}; Right: ${cell(show(c.tokens.generation_metrics_as_recorded.right))} ${src(c.tokens.generation_metrics_as_recorded.right)}`);
  }
  L.push('', '## Gate outcomes (key: attempt | gate)', '', '| key | Left passed / reason_code | Right passed / reason_code | outcome | Left source | Right source |', '|---|---|---|---|---|---|');
  if (c.gates.outcome === 'unavailable') { const av = (s) => `${cell(s.availability.value ?? 'n/a')}${has(s.reason) ? ` (${cell(s.reason.value)})` : ''}`; L.push(`| ${cell(c.gates.key)} | ${av(c.gates.left)} | ${av(c.gates.right)} | unavailable | ${src(c.gates.left.availability)} | ${src(c.gates.right.availability)} |`); }
  else { if (!c.gates.items.length) L.push('| (no gate outcomes recorded on either side) | | | | | |'); for (const g of c.gates.items) L.push(`| ${cell(g.key)} | ${cell(show(g.passed.left))} / ${cell(show(g.reason_code.left))} | ${cell(show(g.passed.right))} / ${cell(show(g.reason_code.right))} | ${g.outcome} | ${src(g.left_source)} | ${src(g.right_source)} |`); ambiguousRows(L, c.gates.ambiguous); }
  L.push('', '## Recorded failures (no causal attribution)', '', ...HEAD, row(c.failures.failure_category));
  L.push(c.failures.failure_report.outcome === 'unavailable' ? unavailableRow(c.failures.failure_report) : row(c.failures.failure_report));
  L.push('', 'Events carrying a failure_type (key: attempt | kind | failure_type):', '', ...HEAD);
  if (c.failures.events_with_failure_type.outcome === 'unavailable') L.push(unavailableRow(c.failures.events_with_failure_type)); else if (!c.failures.events_with_failure_type.items.length) L.push('| (none recorded on either side) | | | | | | |'); else { for (const it of c.failures.events_with_failure_type.items) L.push(row({ ...it, difference: null })); ambiguousRows(L, c.failures.events_with_failure_type.ambiguous); }
  L.push('', '## Diagnostics');
  if (!report.diagnostics.length) L.push('', 'none'); else { L.push('', '| severity | file | pointer | message |', '|---|---|---|---|'); for (const d of report.diagnostics) L.push(`| ${d.severity} | ${cell(d.file)} | ${cell(d.pointer || '/')} | ${cell(d.message)} |`); }
  L.push('');
  return L.join('\n');
}

// ---- CLI -----------------------------------------------------------------------------------------------------------
export function parseArgs(argv) {
  const o = { left: null, right: null, out: null, overwrite: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--left' || a === '--right' || a === '--out') { const v = argv[++i]; if (v === undefined) return { error: `${a} needs a value` }; o[a.slice(2)] = v; }
    else if (a === '--overwrite') o.overwrite = true;
    else return { error: `unexpected argument ${a}: exactly two inputs are compared, named with --left and --right` };
  }
  if (!o.left || !o.right) return { error: 'both --left <file> and --right <file> are required (exactly two inputs)' };
  if (!o.out) return { error: 'an explicit output directory is required: --out <dir>' };
  return o;
}

export function main(argv) {
  const usage = '\nusage: node tools/run_compare.mjs --left <left.json> --right <right.json> --out <dir> [--overwrite]';
  const args = parseArgs(argv);
  if (args.error) return { exitCode: 2, message: args.error + usage };
  const outDir = resolve(args.out);
  const outputs = [join(outDir, 'compare.md'), join(outDir, 'compare.json')];
  const inputs = [];
  for (const file of [args.left, args.right]) {
    const path = resolve(file);
    if (!existsSync(path)) return { exitCode: 2, message: `input does not exist: ${file}` };
    if (statSync(path).isDirectory()) return { exitCode: 2, message: `input is a directory, not a file: ${file}` };
    const real = realpathSync(path);
    if (outputs.some((o) => existsSync(o) && realpathSync(o) === real) || outputs.includes(path)) return { exitCode: 2, message: `refusing: an output path would overwrite the input ${file}` };
    inputs.push({ file, text: readFileSync(path, 'utf8') });
  }
  for (const o of outputs) if (existsSync(o) && !args.overwrite) return { exitCode: 2, message: `refusing to overwrite existing ${o} (pass --overwrite to replace it)` };
  if (existsSync(outDir) && !statSync(outDir).isDirectory()) return { exitCode: 2, message: `--out is not a directory: ${args.out}` };
  const built = buildComparison(inputs[0], inputs[1]);
  if (!built.ok) return { exitCode: 2, message: `no comparison written:\n - ${built.problems.join('\n - ')}` };
  mkdirSync(outDir, { recursive: true });
  writeFileSync(outputs[1], stableStringify(built.report) + '\n');
  writeFileSync(outputs[0], renderMarkdown(built.report));
  const c = built.report.comparison;
  const changed = c.identity.filter((i) => i.outcome !== 'equal').length;
  return { exitCode: 0, message: `wrote ${outputs[0]} and ${outputs[1]}: identity facts not equal: ${changed}; diagnostics: ${built.report.diagnostics.length}` };
}

if (process.argv[1] && realpathSync(process.argv[1]) === realpathSync(fileURLToPath(import.meta.url))) {
  const r = main(process.argv.slice(2));
  (r.exitCode === 0 ? process.stdout : process.stderr).write(r.message + '\n');
  process.exit(r.exitCode);
}
