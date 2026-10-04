#!/usr/bin/env node
/**
 * Fixture stand-in for the `kriya` executable while matrix protection (D-9) holds. Speaks EXACTLY the gated KUP
 * grammar (handover/GUI-D4-READ-STRATEGY/03_GATE.md): explicit snapshot acquisition, pinned history reads, snapshot
 * list/prune. Answers from ui/fixtures/generated; snapshot ids acquired at runtime live in KRIYA_FAKE_STATE (a JSON
 * file the host owns), so "Acquire new snapshot" produces a new id and retention keeps the newest three.
 * Never touches Kriya state. Behaviours for the shell's limit tests via KRIYA_FAKE_BEHAVIOR: slow, huge, garbage,
 * schema2, error:<CODE>, exit3, verify_corrupt (snapshot.verify answers SNAPSHOT_CORRUPT), echoenv (prints the
 * process environment as JSON - for the child-environment policy test only).
 */
import { readFileSync, existsSync, writeFileSync, mkdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const GEN = process.env.KRIYA_FAKE_FIXTURES ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'generated');
const STATE = process.env.KRIYA_FAKE_STATE ?? null;
const args = process.argv.slice(2);
const behavior = process.env.KRIYA_FAKE_BEHAVIOR ?? '';
const RETAIN = 3;

function fail(msg) { process.stderr.write(`fake-kriya: ${msg}\n`); process.exit(2); }
function emit(obj, code = 0) { process.stdout.write(JSON.stringify(obj), () => process.exit(code)); }
function emitRaw(text, code) { process.stdout.write(text, () => process.exit(code)); }
function load(name) { const p = join(GEN, name); if (!existsSync(p)) return null; return JSON.parse(readFileSync(p, 'utf8')); }
const now = () => new Date().toISOString().replace(/\.(\d{3})Z$/, '.$1000Z');
const errorEnvelope = (operation, code, message, extra = {}) => ({ schema_version: 1, operation, request_id: 'fixture', observed_at: now(), source: null, consistency: null, data: null, error: { code, message, database_state: null, snapshot_id: null, reason: null, ...extra } });

// ---- snapshot state (fixture snapshots + runtime-acquired ones) ----------------------------------------------
const index = load('index.json');
const fixtureList = load('snapshot.list.json');
function readState() {
  if (STATE && existsSync(STATE)) { try { return JSON.parse(readFileSync(STATE, 'utf8')); } catch { /* fall through */ } }
  return { snapshots: fixtureList?.data?.snapshots ?? [] };
}
function writeState(state) { if (!STATE) return; mkdirSync(dirname(STATE), { recursive: true }); writeFileSync(STATE, JSON.stringify(state)); }
function newSnapshot() {
  const d = new Date();
  const p = (n, w = 2) => String(n).padStart(w, '0');
  const id = `${d.getUTCFullYear()}${p(d.getUTCMonth() + 1)}${p(d.getUTCDate())}T${p(d.getUTCHours())}${p(d.getUTCMinutes())}${p(d.getUTCSeconds())}${p(d.getUTCMilliseconds(), 3)}000Z-${Math.floor(Math.random() * 0xffffffff).toString(16).padStart(8, '0')}`;
  const base = fixtureList.data.snapshots[0];
  return { ...base, snapshot_id: id, acquisition_started_at: now(), acquisition_completed_at: now(), source_metadata_now: base.source_metadata_at_acquisition, source_metadata_changed: false };
}
const consistencyFor = (snap) => ({ kind: 'snapshot_copy', live_stream: false, snapshot_id: snap.snapshot_id, acquisition_started_at: snap.acquisition_started_at, acquisition_completed_at: snap.acquisition_completed_at, source_metadata_at_acquisition: snap.source_metadata_at_acquisition, source_metadata_now: snap.source_metadata_now, source_metadata_changed: snap.source_metadata_changed });
const sourceFor = (snap) => ({ state_directory: '/fixture/.kriya/state', trace_database: '/fixture/.kriya/state/traces.db', snapshot_directory: `/fixture/.kriya/state/kup-snapshots/${snap.snapshot_id}`, snapshot_id: snap.snapshot_id });

function parse(argv) {
  if (argv[0] === 'runs' && argv[1] === 'status' && argv[2] === '--workspace' && argv[4] === '--json' && argv[5] === '--kup-version' && argv[6] === '1' && argv.length === 7) return { op: 'workspace.status', workspace: argv[3] };
  if (argv[0] !== 'traces' || argv[1] !== '--json') return null;
  const rest = argv.slice(2);
  if (rest.join(' ') === '--capabilities') return { op: 'capabilities' };
  if (rest[0] === '--snapshot') { if (rest.length === 1) return { op: 'snapshot.acquire' }; if (rest[1] === '--workspace' && rest.length === 3) return { op: 'snapshot.acquire', workspace: rest[2] }; return null; }
  if (rest[0] === '--snapshots') { if (rest.length === 1) return { op: 'snapshot.list' }; if (rest[1] === '--verify' && rest.length === 2) return { op: 'snapshot.list', verify: true }; return null; }
  if (rest[0] === '--snapshot-prune') { if (rest.length === 1) return { op: 'snapshot.prune' }; if (rest[1] === '--keep' && rest.length === 3) return { op: 'snapshot.prune', keep: Number(rest[2]) }; return null; }
  if (rest[0] === '--snapshot-verify') return typeof rest[1] === 'string' && rest.length === 2 ? { op: 'snapshot.verify', sid: rest[1] } : null;
  if (rest[0] === '--snapshot-id' && typeof rest[1] === 'string') {
    const sid = rest[1]; const tail = rest.slice(2);
    if (tail[0] === '-n') { const n = Number(tail[1]); if (!Number.isInteger(n) || n < 1 || n > 200) return null; if (tail.length === 2) return { op: 'history.list', sid, n }; if (tail[2] === '--cursor' && tail.length === 4) return { op: 'history.list', sid, n, cursor: tail[3] }; return null; }
    if (tail[0] === '--run-id' && typeof tail[1] === 'string') { if (tail.length === 2) return { op: 'history.detail', sid, runId: tail[1] }; if (tail[2] === '--include-prompt' && tail.length === 3) return { op: 'history.prompt', sid, runId: tail[1] }; }
  }
  return null;
}

const req = parse(args);
if (!req) fail(`usage error: argv outside the KUP grammar: ${JSON.stringify(args)}`);
if (behavior === 'slow') await new Promise((r) => setTimeout(r, Number(process.env.KRIYA_FAKE_SLEEP_MS ?? 70_000)));
if (behavior === 'echoenv') emitRaw(JSON.stringify(process.env), 0);
else if (behavior === 'huge') emitRaw('{"schema_version":1,"pad":"' + 'x'.repeat(Number(process.env.KRIYA_FAKE_HUGE_BYTES ?? 9 * 1024 * 1024)) + '"}', 0);
else if (behavior === 'garbage') emitRaw('Traceback (most recent call last): not json', 1);
else if (behavior === 'exit3') { process.stderr.write('fake-kriya: simulated failure\n'); process.exit(3); }
else if (behavior.startsWith('error:')) emit(errorEnvelope(req.op, behavior.slice(6), `fixture error ${behavior.slice(6)}`));
else {
  let out;
  const state = readState();
  const find = (sid) => state.snapshots.find((s) => s.snapshot_id === sid);
  const pinnedOrError = (op, sid) => {
    if (!state.snapshots.length) return errorEnvelope(op, 'SNAPSHOT_MISSING', 'no published snapshot exists; acquire one first');
    if (!find(sid)) return errorEnvelope(op, 'SNAPSHOT_UNAVAILABLE', `snapshot ${sid} is not published (pruned or never existed)`, { snapshot_id: sid });
    return null;
  };
  const withSnapshot = (env, snap) => ({ ...env, observed_at: now(), source: sourceFor(snap), consistency: consistencyFor(snap) });
  switch (req.op) {
    case 'capabilities': out = load('capabilities.json'); break;
    case 'snapshot.acquire': {
      const snap = newSnapshot();
      state.snapshots = [snap, ...state.snapshots];
      const pruned = state.snapshots.slice(RETAIN).map((s) => s.snapshot_id);
      state.snapshots = state.snapshots.slice(0, RETAIN);
      writeState(state);
      out = withSnapshot(load('snapshot.acquire.json'), snap);
      out.data = { ...snap, orphans_removed: [], pruned, backup_steps: 1, duration_ms: 14 };
      break;
    }
    case 'snapshot.list': out = load('snapshot.list.json'); out.observed_at = now(); out.data = { ...out.data, snapshots: state.snapshots.map((s) => (req.verify ? { ...s, digest_verified: true } : s)) }; break;
    case 'snapshot.prune': {
      const keep = req.keep ?? RETAIN; const removed = state.snapshots.slice(keep).map((s) => s.snapshot_id); state.snapshots = state.snapshots.slice(0, keep); writeState(state);
      out = load('snapshot.prune.json'); out.data = { removed, orphans_removed: [], kept: state.snapshots.map((s) => s.snapshot_id) }; break;
    }
    case 'snapshot.verify': {
      // digest verified at pin: exactly the named snapshot; a corrupt one is the typed error with the id, never data
      const snap = find(req.sid);
      if (!snap) out = pinnedOrError('snapshot.verify', req.sid);
      else if (behavior === 'verify_corrupt') out = errorEnvelope('snapshot.verify', 'SNAPSHOT_CORRUPT', `snapshot digest ${snap.snapshot_id.slice(-8)} differs from manifest`, { snapshot_id: snap.snapshot_id });
      else { out = { ...load('snapshot.verify.json'), observed_at: now(), source: sourceFor(snap) }; out.data = { ...out.data, snapshot_id: snap.snapshot_id, sha256: snap.snapshot_id.slice(-8).repeat(8), size: snap.size, verified_at: now() }; }
      break;
    }
    case 'history.list': {
      out = pinnedOrError('history.list', req.sid) ?? withSnapshot(load(req.cursor ? `history.list.cursor.${req.cursor}.json` : 'history.list.page1.json') ?? errorEnvelope('history.list', 'INVALID_REQUEST', 'cursor is not an opaque token issued by this protocol'), find(req.sid));
      if (out.data) out.data.runs = out.data.runs.slice(0, req.n);
      break;
    }
    case 'history.detail': out = pinnedOrError('history.detail', req.sid) ?? withSnapshot(load(`history.detail.${req.runId}.json`) ?? errorEnvelope('history.detail', 'INVALID_REQUEST', `no run ${req.runId} in snapshot ${req.sid}`), find(req.sid)); break;
    case 'history.prompt': out = pinnedOrError('history.prompt', req.sid) ?? withSnapshot(load(`history.prompt.${req.runId}.json`) ?? errorEnvelope('history.prompt', 'INVALID_REQUEST', `no run ${req.runId} in snapshot ${req.sid}`), find(req.sid)); break;
    case 'workspace.status': out = load('workspace.status.json'); if (out?.data) out.data.workspace = req.workspace; break;
  }
  if (behavior === 'schema2') out = { ...out, schema_version: 2 };
  emit(out);
}
