#!/usr/bin/env node
/**
 * Fixture stand-in for the `kriya` executable while matrix protection (D-9) holds. Speaks EXACTLY the P-25 grammar
 * and answers from ui/fixtures/generated. Never touches Kriya state. Behaviours for the shell's limit tests via
 * KRIYA_FAKE_BEHAVIOR: slow (sleeps KRIYA_FAKE_SLEEP_MS), huge (KRIYA_FAKE_HUGE_BYTES of stdout), garbage (non-JSON),
 * schema2, error:<CODE>, exit3.
 */
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const GEN = process.env.KRIYA_FAKE_FIXTURES ?? join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'fixtures', 'generated');
const args = process.argv.slice(2);
const behavior = process.env.KRIYA_FAKE_BEHAVIOR ?? '';

function fail(msg) { process.stderr.write(`fake-kriya: ${msg}\n`); process.exit(2); }
// Flush before exiting: a pipe write is asynchronous in Node, and process.exit() right after it truncates stdout.
function emit(obj, code = 0) { process.stdout.write(JSON.stringify(obj), () => process.exit(code)); }
function emitRaw(text, code) { process.stdout.write(text, () => process.exit(code)); }
function load(name) { const p = join(GEN, name); if (!existsSync(p)) return null; return JSON.parse(readFileSync(p, 'utf8')); }
const errorEnvelope = (operation, code, message) => ({ schema_version: 1, operation, request_id: 'fixture', observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code, message } });

function parse(argv) {
  if (argv[0] === 'runs' && argv[1] === 'status' && argv[2] === '--workspace' && argv[4] === '--json' && argv[5] === '--kup-version' && argv[6] === '1' && argv.length === 7) return { op: 'workspace.status', workspace: argv[3] };
  if (argv[0] !== 'traces') return null;
  const rest = argv.slice(1);
  if (rest.join(' ') === '--capabilities --json') return { op: 'capabilities' };
  if (rest[0] !== '--json') return null;
  if (rest[1] === '-n') {
    const n = Number(rest[2]);
    if (!Number.isInteger(n) || n < 1 || n > 200) return null;
    if (rest.length === 3) return { op: 'history.list', n };
    if (rest[3] === '--cursor' && rest.length === 5) return { op: 'history.list', n, cursor: rest[4] };
    return null;
  }
  if (rest[1] === '--run-id' && typeof rest[2] === 'string') {
    if (rest.length === 3) return { op: 'history.detail', runId: rest[2] };
    if (rest[3] === '--include-prompt' && rest.length === 4) return { op: 'history.prompt', runId: rest[2] };
  }
  return null;
}

const req = parse(args);
if (!req) fail(`usage error: argv outside the KUP grammar: ${JSON.stringify(args)}`);
if (behavior === 'slow') await new Promise((r) => setTimeout(r, Number(process.env.KRIYA_FAKE_SLEEP_MS ?? 70_000)));
if (behavior === 'huge') emitRaw('{"schema_version":1,"pad":"' + 'x'.repeat(Number(process.env.KRIYA_FAKE_HUGE_BYTES ?? 9 * 1024 * 1024)) + '"}', 0);
else if (behavior === 'garbage') emitRaw('Traceback (most recent call last): not json', 1);
else {
if (behavior === 'exit3') { process.stderr.write('fake-kriya: simulated failure\n'); process.exit(3); }
if (behavior.startsWith('error:')) { emit(errorEnvelope(req.op, behavior.slice(6), `fixture error ${behavior.slice(6)}`)); } else {

let out;
switch (req.op) {
  case 'capabilities': out = load('capabilities.json'); break;
  case 'history.list':
    out = load(req.cursor ? `history.list.cursor.${req.cursor}.json` : 'history.list.page1.json') ?? errorEnvelope('history.list', 'INVALID_CURSOR', 'unknown cursor');
    if (out.data) out.data.runs = out.data.runs.slice(0, req.n);
    break;
  case 'history.detail': out = load(`history.detail.${req.runId}.json`) ?? errorEnvelope('history.detail', 'NOT_FOUND', `no run ${req.runId}`); break;
  case 'history.prompt': out = load(`history.prompt.${req.runId}.json`) ?? errorEnvelope('history.prompt', 'NOT_FOUND', `no run ${req.runId}`); break;
  case 'workspace.status': out = load('workspace.status.json'); if (out?.data) out.data.workspace = req.workspace; break;
}
if (behavior === 'schema2') out = { ...out, schema_version: 2 };
emit(out);
}
}
