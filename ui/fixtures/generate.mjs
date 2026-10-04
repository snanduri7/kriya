#!/usr/bin/env node
/**
 * Deterministic synthetic KUP v1 fixtures (06 §A2). Used by BOTH hosts: the browser test host serves them as static
 * files and the Electron shell's fake `kriya` (standalone/fake-kriya) answers the P-25 grammar from them.
 * Synthetic content only. Exercises: a 2,000-line diff, a >4 MiB event payload with heavy escaping, unknown event
 * fields, incomplete context, every availability state, every error code, pagination with equal timestamps.
 */
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const out = join(dirname(fileURLToPath(import.meta.url)), 'generated');
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, 'errors'), { recursive: true });

let seed = 20261004;
const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const pick = (xs) => xs[Math.floor(rand() * xs.length)];
const OBSERVED = '2026-10-04T09:30:00Z';
const SOURCE = { state_directory: '/fixture/.kriya/state', trace_database: '/fixture/.kriya/state/traces.db' };
const envelope = (operation, data, error = null) => ({ schema_version: 1, operation, request_id: 'fixture', observed_at: OBSERVED, source: error ? null : SOURCE, consistency: error ? null : { kind: 'sqlite_transaction_snapshot', live_stream: false }, data: error ? null : data, error });
const sec = (availability, data, reason = null, provenance = 'fixture:trace_row') => ({ availability, provenance, reason: availability === 'recorded' ? reason : (reason ?? `fixture ${availability}`), data: availability === 'recorded' ? data : null });
const write = (name, obj) => writeFileSync(join(out, name), JSON.stringify(obj));

const AVAIL = ['recorded', 'not_recorded', 'unreadable', 'excluded', 'unsupported'];
const STATUS = ['SUCCESS', 'FAILED', 'FAILED', 'NEEDS_REVIEW', 'SUCCESS'];
const CATS = ['quality_gate_failed', 'time_budget_exhausted', 'fallback_model_incompatible', null, 'final_review_refused'];
const EVENTS = ['retrieval.expansion_seeds', 'context.known_target_package', 'context.retry_target_source', 'developer.prompt_composition', 'model.transition', 'model.role_metrics', 'gate.compile', 'gate.tests', 'approval.decision', 'commit.verified'];

function events(n, { unknownFields = false, big = false, attempts = 2 } = {}) {
  const list = [];
  for (let k = 0; k < n; k++) {
    const e = { event: pick(EVENTS), attempt: (k % attempts) + 1, source: 'workflow', authority: pick(['deterministic', 'model', 'verifier']), at: `2026-09-12 10:${String(Math.floor(k / 60) % 60).padStart(2, '0')}:${String(k % 60).padStart(2, '0')}`, payload: { k } };
    if (e.event === 'model.transition') e.payload = { from: 'qwen2.5-coder:7b', to: 'qwen2.5-coder:14b', model: 'qwen2.5-coder:14b', qualification: 'QUALIFIED', changes: { context_window: [8192, 16384] } };
    if (e.event === 'model.role_metrics') e.payload = { rows: [{ role: 'developer', model: 'qwen2.5-coder:7b', calls: 3 }] };
    if (e.event === 'context.known_target_package') e.payload = { path: `src/mod${k % 7}/a.py`, tier: pick(['full', 'skeleton', 'signatures', 'member_exact']), member_ids: [`Mod${k % 7}.run`], omissions: k % 3 === 0 ? [{ path: 'src/big.py', reason: 'budget_exhausted' }] : [] };
    if (e.event === 'developer.prompt_composition') e.payload = { sections: { skills_prompt: 812, graph_context: 2048, known_target: 1400 }, estimated_total: 4260, provider_prompt_tokens: 4190, prompt_eval_ms: 1830 };
    if (big) e.payload = { ...e.payload, blob: 'quoted "json" \\ back\\slash \n newline \t tab é—中😀 </script> <img onerror=x> '.repeat(40) };
    if (unknownFields) { e[`extra_${k % 5}`] = { nested: [k, 'unknown', null] }; e.severity_v9 = 'novel'; }
    list.push(e);
  }
  return list;
}

const RUNS = [];
function addRun(id, over = {}, detailOver = {}, opts = {}) {
  const i = RUNS.length;
  const summary = { run_id: id, timestamp: over.timestamp ?? `2026-09-${String(28 - (i % 28)).padStart(2, '0')} 1${i % 10}:00:00`, goal: over.goal ?? `Fixture goal ${i}: add an audit log to module ${i % 7} and cover it with tests`, duration_sec: 40 + i * 3.25, attempts: (i % 4) + 1, status: STATUS[i % 5], failure_category: CATS[i % 5], files_modified: `src/mod${i % 7}/a.py,src/mod${i % 7}/b.py,tests/test_mod${i % 7}.py`, milestone_group_id: i % 3 === 0 ? `group-${Math.floor(i / 3)}` : null, milestone_index: i % 3 === 0 ? i % 5 : null, milestone_total: i % 3 === 0 ? 5 : null, ...over };
  RUNS.push(summary);
  const av = opts.availability ?? 'recorded';
  const evs = events(opts.eventCount ?? 60, opts);
  const detail = {
    run: summary,
    fields: { files_modified: sec('recorded', summary.files_modified, null, 'runs.files_modified (comma-joined, raw)') },
    run_events: sec(av, evs, null, 'runs.run_events'),
    evidence_records: sec(av, [{ evidence_id: `ev-${id}-1`, revision: 'sha256:' + 'ab'.repeat(32), kind: 'gate_output' }], null, 'runs.evidence_records'),
    gate_outcomes: sec(av, [{ attempt: 1, gate: 'compile', passed: false, reason_code: 'COMPILE_FAILED' }, { attempt: 2, gate: 'compile', passed: true }, { attempt: 2, gate: 'tests', passed: summary.status === 'SUCCESS' }], null, 'runs.gate_outcomes'),
    model_hops: sec(av, [{ from: 'qwen2.5-coder:7b', to: 'qwen2.5-coder:14b', attempt: 2, reason: 'quality_gate_failed' }], null, 'runs.model_hops'),
    generation_metrics: sec(av, { prompt_tokens_estimated: 4260, prompt_tokens_provider: 4190, output_tokens: 812, wall_ms: 61230 }, null, 'runs.generation_metrics'),
    failure_report: sec(av, summary.failure_category ? [{ failure_type: 'compile_error', category: summary.failure_category, attribution_tier: 'locator' }] : [], null, 'runs.failure_report'),
    context: sec(av, { items: [{ path: `src/mod${i % 7}/a.py`, tier: 'member_exact', member_ids: [`Mod${i % 7}.run`, `Mod${i % 7}.__init__`] }, { path: `src/mod${i % 7}/b.py`, tier: 'skeleton', member_ids: [] }, { path: 'src/big.py', tier: null, omitted: true, omission_reason: 'budget_exhausted' }], tokens: { estimated: { total: 4260, known_target: 1400 }, provider_reported: { prompt: 4190 } }, package_hash: 'sha256:' + 'cd'.repeat(32) }, null, 'run_events:context.known_target_package'),
    attribution: sec(av === 'recorded' && i % 2 === 0 ? 'recorded' : av === 'recorded' ? 'not_recorded' : av, { first_incorrect_state: 'CONTEXT', cause: 'the target member was omitted from the Developer request', category: 'CONTEXT', evidence_ids: [`ev-${id}-1`] }, av === 'recorded' && i % 2 ? 'baseline persists failure categories, not causal attribution (P-30)' : null, 'fixture:attribution'),
    diagnostics: sec(av === 'recorded' ? 'not_recorded' : av, null, 'no diagnostics record persisted for this run (P-30)'),
    comparisons: sec(av === 'recorded' && i % 2 === 0 ? 'recorded' : av === 'recorded' ? 'not_recorded' : av, [{ path: `src/mod${i % 7}/a.py`, before: { text: 'def run():\n    return 1\n', provenance: 'fixture:commit_evidence', revision: 'r1' }, after: { text: 'def run():\n    audit("run")\n    return 1\n', provenance: 'fixture:commit_evidence', revision: 'r2' } }], av === 'recorded' && i % 2 ? 'trace stores modified paths, not before/after text (P-30)' : null, 'fixture:commit_evidence'),
    output: sec(av === 'recorded' ? 'not_recorded' : av, null, 'Developer output is not persisted by the baseline (P-30)'),
    ...detailOver,
  };
  write(`history.detail.${id}.json`, envelope('history.detail', detail));
  write(`history.prompt.${id}.json`, envelope('history.prompt', { prompt_rendered: opts.noPrompt ? null : `PLANNING PROMPT (fixture, ${id})\n` + 'You are the Planner. Goal: ...\n'.repeat(30), role: 'planner', scope: 'plan_prompt (one per run; not every Developer request)' }));
  return summary;
}

// Specials first so they are on page 1.
const before2000 = Array.from({ length: 2000 }, (_, n) => `line ${n}: value = compute(${n})`).join('\n');
const after2000 = Array.from({ length: 2000 }, (_, n) => (n % 25 === 0 ? `line ${n}: value = compute_audited(${n})  # changed` : n % 400 === 0 ? `line ${n}: inserted()\nline ${n}: value = compute(${n})` : `line ${n}: value = compute(${n})`)).join('\n');
addRun('run-diff-2000', { goal: 'FIXTURE: 2,000-line recorded diff' }, { comparisons: sec('recorded', [{ path: 'src/large_module.py', before: { text: before2000, provenance: 'fixture:commit_evidence', revision: 'r1' }, after: { text: after2000, provenance: 'fixture:commit_evidence', revision: 'r2' } }], null, 'fixture:commit_evidence') });
addRun('run-big-events', { goal: 'FIXTURE: >4 MiB run_events payload with heavy JSON escaping' }, {}, { big: true, eventCount: 1800, attempts: 3 });
addRun('run-unknown-fields', { goal: 'FIXTURE: unknown event fields and an unknown status', status: 'PARTIALLY_SETTLED_v9' }, { novel_top_level_section: { availability: 'recorded', data: { hello: 'future' } } }, { unknownFields: true });
addRun('run-incomplete-context', { goal: 'FIXTURE: incomplete context record' }, { context: sec('recorded', { items: [{ path: 'src/mod1/a.py' }, { path: 'src/mod1/b.py', omitted: true }], tokens: null }, 'package hash and token accounting were not recorded', 'run_events:context.known_target_package') });
for (const a of AVAIL) addRun(`run-avail-${a}`, { goal: `FIXTURE: every section "${a}"`, timestamp: '2026-09-20 12:00:00' }, {}, { availability: a });
addRun('run-no-events', { goal: 'FIXTURE: a trace row with an empty event list', timestamp: '2026-09-20 12:00:00' }, {}, { eventCount: 0 });
addRun('run-no-prompt', { goal: 'FIXTURE: prompt column empty', timestamp: '2026-09-20 12:00:00' }, {}, { noPrompt: true });
for (let i = RUNS.length; i < 120; i++) addRun(`run-${String(i).padStart(4, '0')}`);

// Pagination: ordered by (timestamp DESC, run_id DESC), pages of 50, cursor = base64 of the last (timestamp, run_id).
const ordered = [...RUNS].sort((a, b) => (a.timestamp === b.timestamp ? (a.run_id < b.run_id ? 1 : -1) : a.timestamp < b.timestamp ? 1 : -1));
const pages = [];
for (let p = 0; p < ordered.length; p += 50) pages.push(ordered.slice(p, p + 50));
pages.forEach((page, n) => {
  const last = page[page.length - 1];
  const next = n + 1 < pages.length ? Buffer.from(JSON.stringify([last.timestamp, last.run_id])).toString('base64') : null;
  write(`history.list.page${n + 1}.json`, envelope('history.list', { runs: page, next_cursor: next }));
  if (next) write(`history.list.cursor.${next}.json`, envelope('history.list', { runs: pages[n + 1], next_cursor: n + 2 < pages.length ? Buffer.from(JSON.stringify([pages[n + 1].at(-1).timestamp, pages[n + 1].at(-1).run_id])).toString('base64') : null }));
});
write('capabilities.json', envelope('capabilities', { kup_versions: [1], operations: ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status'], identity: { kriya_version: '0.1.0+fixture', commit: 'fixture0000000000000000000000000000000000', provenance: 'fixture' }, limits: { list_default: 50, list_max: 200, max_response_bytes: 8 * 1024 * 1024 }, features: { prompt: true, comparisons: 'fixture_only', attribution: 'fixture_only' } }));
write('workspace.status.json', envelope('workspace.status', { workspace: '/fixture/workspace', run_active: false, status: 'NO_RECOVERY_REQUIRED', exit_code: 0, assessment: { reason: 'fixture', checkpoints: 0 } }));
for (const [code, message] of [['UNSUPPORTED_SCHEMA_VERSION', 'the host requested KUP 1; this Kriya speaks 3'], ['INVALID_RESPONSE', 'response was not a KUP envelope'], ['STORE_BUSY', 'a hot rollback journal needs recovery (SQLITE_READONLY_ROLLBACK)'], ['READ_ONLY_UNAVAILABLE', 'WAL store cannot be read without creating -wal/-shm (A1 C03)'], ['RESPONSE_TOO_LARGE', 'history.detail exceeds 8 MiB; use --include-prompt only on demand'], ['CONFIG_AUTHORITY_REFUSED', 'kriya.yaml sets mcp.* without an approval (SEC-009)']]) {
  write(`errors/${code}.json`, envelope('history.list', null, { code, message }));
}
write('index.json', { generated_at: 'deterministic', runs: ordered.map((r) => r.run_id), specials: ['run-diff-2000', 'run-big-events', 'run-unknown-fields', 'run-incomplete-context', ...AVAIL.map((a) => `run-avail-${a}`), 'run-no-events', 'run-no-prompt'], pages: pages.length, errors: ['UNSUPPORTED_SCHEMA_VERSION', 'INVALID_RESPONSE', 'STORE_BUSY', 'READ_ONLY_UNAVAILABLE', 'RESPONSE_TOO_LARGE', 'CONFIG_AUTHORITY_REFUSED'] });
import { statSync } from 'node:fs';
const bigBytes = statSync(join(out, 'history.detail.run-big-events.json')).size;
console.log(`fixtures: ${RUNS.length} runs, ${pages.length} pages, big-events detail = ${(bigBytes / 1048576).toFixed(2)} MiB -> ${out}`);
