#!/usr/bin/env node
/**
 * Deterministic synthetic KUP v1 fixtures (06 §A2). Used by BOTH hosts: the browser test host serves them as static
 * files and the Electron shell's fake `kriya` (standalone/fake-kriya) answers the P-25 grammar from them.
 * Synthetic content only. Exercises: a 2,000-line diff, a >4 MiB event payload with heavy escaping, unknown event
 * fields, incomplete context, every availability state, every error code, pagination with equal timestamps.
 */
import { mkdirSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, 'generated');
// run_events produced by Kriya's OWN serializer through the KUP adapter (fixtures/serializer_events.py, committed):
// the shape every synthetic event below follows, and the content of the run-serializer-events fixture.
const SERIALIZER = JSON.parse(readFileSync(join(here, 'serializer', 'run_events.json'), 'utf8'));
rmSync(out, { recursive: true, force: true });
mkdirSync(join(out, 'errors'), { recursive: true });

let seed = 20261004;
const rand = () => { seed = (seed * 1103515245 + 12345) & 0x7fffffff; return seed / 0x7fffffff; };
const pick = (xs) => xs[Math.floor(rand() * xs.length)];
const OBSERVED = '2026-10-04T09:30:00Z';
const SOURCE = { state_directory: '/fixture/.kriya/state', trace_database: '/fixture/.kriya/state/traces.db' };
// Snapshot identities (gate C-2/C-3): history fixtures are served under a pinned snapshot id; the fake kriya and the
// browser host mint further ids at runtime for "Acquire new snapshot".
const META = (size, wal) => ({ size, mtime_ns: 1759561200000000000 + size, inode: 7781234, wal_size: wal, shm_size: wal === null ? null : 32768, journal_size: null });
export const SNAPSHOTS = [
  { snapshot_id: '20261004T093000000000Z-f1c70001', acquisition_started_at: '2026-10-04T09:30:00.000000Z', acquisition_completed_at: '2026-10-04T09:30:00.014000Z', source: SOURCE.trace_database, source_metadata_at_acquisition: META(2969600, null), source_metadata_now: META(2969600, null), source_metadata_changed: false, rows: 120, size: 2969600, sqlite_version: '3.53.4' },
  { snapshot_id: '20261004T083000000000Z-f1c70000', acquisition_started_at: '2026-10-04T08:30:00.000000Z', acquisition_completed_at: '2026-10-04T08:30:00.012000Z', source: SOURCE.trace_database, source_metadata_at_acquisition: META(2850000, 4120), source_metadata_now: META(2969600, null), source_metadata_changed: true, rows: 118, size: 2850000, sqlite_version: '3.53.4' },
];
const SNAP = SNAPSHOTS[0];
const snapshotConsistency = (snap = SNAP) => ({ kind: 'snapshot_copy', live_stream: false, snapshot_id: snap.snapshot_id, acquisition_started_at: snap.acquisition_started_at, acquisition_completed_at: snap.acquisition_completed_at, source_metadata_at_acquisition: snap.source_metadata_at_acquisition, source_metadata_now: snap.source_metadata_now, source_metadata_changed: snap.source_metadata_changed });
const consistencyFor = (operation) => operation === 'capabilities' ? { kind: 'not_applicable', live_stream: false } : operation.startsWith('history.') || operation === 'snapshot.acquire' ? snapshotConsistency() : { kind: 'live_observation', live_stream: false };
const sourceFor = (operation) => operation.startsWith('history.') || operation === 'snapshot.verify' ? { ...SOURCE, snapshot_directory: `${SOURCE.state_directory}/kup-snapshots/${SNAP.snapshot_id}`, snapshot_id: SNAP.snapshot_id } : operation === 'workspace.status' ? null : SOURCE;
const envelope = (operation, data, error = null) => ({ schema_version: 1, operation, request_id: 'fixture', observed_at: OBSERVED, source: error ? null : sourceFor(operation), consistency: error ? null : consistencyFor(operation), data: error ? null : data, error });
const sec = (availability, data, reason = null, provenance = 'fixture:trace_row') => ({ availability, provenance, reason: availability === 'recorded' ? reason : (reason ?? `fixture ${availability}`), data: availability === 'recorded' ? data : null });
const write = (name, obj) => writeFileSync(join(out, name), JSON.stringify(obj));

const AVAIL = ['recorded', 'not_recorded', 'unreadable', 'excluded', 'unsupported'];
const STATUS = ['SUCCESS', 'FAILED', 'FAILED', 'NEEDS_REVIEW', 'SUCCESS'];
const CATS = ['quality_gate_failed', 'time_budget_exhausted', 'fallback_model_incompatible', null, 'final_review_refused'];
// Real Kriya event kinds (grep kind="..." in kriya/); the shape is RunEvent.to_dict's: kind, attempt, source, authority,
// message, failure_type, operation, details, created_at (epoch seconds). Details of the kinds the UI interprets follow
// the serializer fixture's real shapes.
const EVENTS = ['attempt.started', 'context.known_target_package', 'context.retry_target_source', 'context.request_fit', 'developer.prompt_composition', 'model.transition', 'model.role_metrics', 'generation.completed', 'approval.decision', 'candidate_gates.passed'];
const AUTHORITIES = ['authoritative', 'advisory', 'auxiliary'];
const REAL = Object.fromEntries(SERIALIZER.run_events.map((e) => [e.kind, e]));

function events(n, { unknownFields = false, big = false, attempts = 2 } = {}) {
  const list = [];
  for (let k = 0; k < n; k++) {
    const e = { kind: pick(EVENTS), attempt: (k % attempts) + 1, source: 'workflow', authority: pick(AUTHORITIES), message: `fixture event ${k}`, failure_type: null, operation: null, details: { k }, created_at: 1757671200 + k * 1.5 };
    if (e.kind === 'model.transition') e.details = { ...REAL['model.transition'].details, changes: ['model', 'context_window'] };
    if (e.kind === 'model.role_metrics') e.details = { rows: REAL['model.role_metrics'].details.rows.map((r) => ({ ...r, calls: r.calls + (k % 3) })) };
    if (e.kind === 'context.known_target_package') e.details = { ...REAL['context.known_target_package'].details, known_target_files: [`src/mod${k % 7}/a.py`], tiers: [{ path: `src/mod${k % 7}/a.py`, member_id: `Mod${k % 7}.run`, tier: pick(['full', 'skeleton', 'signatures', 'member_exact']) }], omitted: k % 3 === 0 ? [{ path: 'src/big.py', reason: 'budget_exhausted' }] : [] };
    if (e.kind === 'developer.prompt_composition') e.details = { ...REAL['developer.prompt_composition'].details, prompt_tokens_reported: 4190 + k };
    if (big) e.details = { ...e.details, blob: 'quoted "json" \\ back\\slash \n newline \t tab é—中😀 </script> <img onerror=x> '.repeat(40) };
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
// run_events exactly as Kriya serializes them, read back through the KUP adapter (fixtures/serializer_events.py).
addRun('run-serializer-events', { goal: 'FIXTURE: run_events exactly as Kriya serializes them (RunEvent.to_dict through the KUP adapter)', status: 'SUCCESS', failure_category: null, attempts: 2 },
  { run_events: { availability: SERIALIZER.section.availability, provenance: SERIALIZER.section.provenance, reason: SERIALIZER.section.reason, data: SERIALIZER.run_events } });
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
write('capabilities.json', envelope('capabilities', { kup_versions: [1], operations: ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify'], identity: { kriya_version: '0.1.0+fixture', commit: 'fixture0000000000000000000000000000000000', provenance: 'fixture', implementation: 'kriya-kup/1', sqlite_version: '3.53.4' }, limits: { list_default: 50, list_max: 200, max_response_bytes: 8 * 1024 * 1024, max_snapshot_bytes: 536870912, retain_snapshots: 3, acquisition_deadline_seconds: 45 }, features: { snapshot: true, prompt: true, comparisons: 'fixture_only', attribution: 'fixture_only' } }));
write('snapshot.list.json', envelope('snapshot.list', { snapshot_directory: `${SOURCE.state_directory}/kup-snapshots`, snapshots: SNAPSHOTS, retain: 3 }));
write('snapshot.acquire.json', envelope('snapshot.acquire', { ...SNAP, orphans_removed: [], pruned: [], backup_steps: 1, duration_ms: 14.0 }));
write('snapshot.prune.json', envelope('snapshot.prune', { removed: [SNAPSHOTS[1].snapshot_id], orphans_removed: [], kept: [SNAP.snapshot_id] }));
// digest verified at pin (08 review F-4): exactly one snapshot's SHA-256; per-query checks stay size/mtime.
write('snapshot.verify.json', envelope('snapshot.verify', { snapshot_id: SNAP.snapshot_id, digest_verified: true, sha256: SNAP.snapshot_id.slice(-8).repeat(8), size: SNAP.size, verified_at: OBSERVED, duration_ms: 3.1, guarantee: 'digest verified at this request; size and mtime are checked on every later query' }));
write('workspace.status.json', envelope('workspace.status', { workspace: '/fixture/workspace', run_active: false, status: 'NO_RECOVERY_REQUIRED', exit_code: 0, assessment: { reason: 'fixture', checkpoints: 0 } }));
const ERRORS = [['UNSUPPORTED_SCHEMA_VERSION', 'the host requested KUP 1; this Kriya speaks 3', null], ['INVALID_RESPONSE', 'response was not a KUP envelope', null], ['INVALID_REQUEST', 'snapshot_id is required: history is read from a published snapshot only', null],
  ['STORE_BUSY', 'the store has a hot rollback journal; acquisition never recovers another writer\'s transaction', 'hot_journal'], ['READ_ONLY_UNAVAILABLE', 'no trace database exists at the resolved store path', 'missing'],
  ['RESPONSE_TOO_LARGE', 'the response would exceed 8388608 bytes', null], ['CONFIG_AUTHORITY_REFUSED', 'Configuration-authority denied (SEC-009)', null], ['CONFIG_LOAD_FAILED', 'paths.state must be an absolute path', null],
  ['SNAPSHOT_MISSING', 'no published snapshot exists; acquire one first', null], ['SNAPSHOT_UNAVAILABLE', 'snapshot 20260101T000000000000Z-00000000 is not published (pruned or never existed)', null],
  ['SNAPSHOT_FAILED', 'insufficient free space', null], ['SNAPSHOT_TOO_LARGE', 'the store image is 600000000 bytes; the per-snapshot bound is 536870912 bytes', null], ['SNAPSHOT_CORRUPT', 'snapshot file size or mtime differs from its manifest', null],
  ['ACQUISITION_IN_PROGRESS', 'another acquisition or prune holds the snapshot directory', null], ['ACQUISITION_REFUSED_RUN_ACTIVE', 'a run is active for the selected workspace; acquisition is refused by policy', null]];
for (const [code, message, database_state] of ERRORS) write(`errors/${code}.json`, envelope(code.startsWith('ACQUISITION') || code.startsWith('SNAPSHOT_F') || code.startsWith('SNAPSHOT_T') || code === 'STORE_BUSY' || code === 'READ_ONLY_UNAVAILABLE' ? 'snapshot.acquire' : 'history.list', null, { code, message, database_state, snapshot_id: null, reason: null }));
write('index.json', { generated_at: 'deterministic', runs: ordered.map((r) => r.run_id), specials: ['run-diff-2000', 'run-big-events', 'run-unknown-fields', 'run-incomplete-context', 'run-serializer-events', ...AVAIL.map((a) => `run-avail-${a}`), 'run-no-events', 'run-no-prompt'], pages: pages.length, errors: ERRORS.map(([c]) => c), snapshots: SNAPSHOTS.map((s) => s.snapshot_id) });
import { statSync } from 'node:fs';
const bigBytes = statSync(join(out, 'history.detail.run-big-events.json')).size;
console.log(`fixtures: ${RUNS.length} runs, ${pages.length} pages, big-events detail = ${(bigBytes / 1048576).toFixed(2)} MiB -> ${out}`);
