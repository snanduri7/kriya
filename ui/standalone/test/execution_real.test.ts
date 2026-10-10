/**
 * Start Run end to end against the REAL Kriya: the real `kriya generate --json --launch-id` command, preflight, launch record, run coordinator
 * (workspace lock + RunRecord), attempt-evidence recorder, trace logger and the real persistent read process. The ONE thing replaced is the workflow body
 * that would call a model (fixtures/controlled_kriya.py) - no model is ever called. Needs the repository's .venv (skipped, loudly, when absent).
 */
import { execFileSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ExecutionHost } from '../src/main/execution_host';
import { launchExecution } from '../src/main/execution_process';
import type { KupRequest } from '../src/main/ipc_contract';
import { KriyaService } from '../src/main/kriya_service';

const ROOT = join(__dirname, '..', '..', '..');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const CONTROLLED = join(__dirname, 'fixtures', 'controlled_kriya.py');
const available = existsSync(PYTHON);

interface Env { [k: string]: string }
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

let counter = 0;
const mint = () => { counter += 1; return `20261010T1010${String(counter).padStart(2, '0')}-${(0xabcd0000 + counter).toString(16)}`; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe.skipIf(!available)('Start Run against real Kriya with a controlled workflow body', () => {
  let base = ''; let state = ''; let config = ''; let home = '';
  let env: Env = {};
  let service: KriyaService;

  const read = async (request: KupRequest): Promise<Json> => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(`read failed: ${out.code} ${out.message}`);
    return out.json as Json;
  };

  /** A kriya executable whose workflow body follows `script`; everything else is the real CLI. */
  function executable(name: string, script: Json): string {
    const path = join(base, `kriya-${name}`);
    writeFileSync(path, `#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='${JSON.stringify(script)}'\nexec '${PYTHON}' '${CONTROLLED}' "$@"\n`);
    chmodSync(path, 0o755);
    return path;
  }
  function workspace(name: string, files: Record<string, string> = {}): string {
    const dir = join(base, 'ws', name);
    mkdirSync(dir, { recursive: true });
    for (const [file, text] of Object.entries(files)) writeFileSync(join(dir, file), text);
    return realpathSync(dir);
  }
  function host(ws: string, exe: string, over: { prepare?: ConstructorParameters<typeof ExecutionHost>[0]['prepare'] } = {}) {
    return new ExecutionHost({
      launch: launchExecution, prepare: over.prepare ?? (() => ({ ok: true, executable: exe, env: { PATH: '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin', ...env } })),
      currentWorkspace: () => ws, mintLaunchId: mint, now: () => new Date(),
      preflight: (workspaceArg) => read({ operation: 'execution.preflight', workspace: workspaceArg }), graceMs: 4000,
    });
  }
  async function untilExited(h: ExecutionHost, ms = 60_000) { const end = Date.now() + ms; while (h.state().phase !== 'exited') { if (Date.now() > end) throw new Error('execution did not exit'); await sleep(40); } }
  const status = (ws: string, id: string) => read({ operation: 'launch.status', workspace: ws, launch_id: id });
  const dirOf = (ws: string) => join(ws, '.kriya', 'control', 'launches');

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), 'kriya-gui-exec-'));
    state = join(base, 'state'); config = join(base, 'config'); home = join(base, 'home');
    for (const d of [state, config, home, join(base, 'ws')]) mkdirSync(d);
    env = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1' };
    const real = join(base, 'kriya-real');
    writeFileSync(real, `#!/bin/sh\nexec '${PYTHON}' -m kriya.cli "$@"\n`);
    chmodSync(real, 0o755);
    service = new KriyaService({ executable: join(ROOT, '.venv', 'bin', 'kriya'), cwd: config, env });
    execFileSync(PYTHON, ['-c', 'import kriya.cli'], { env: { PATH: process.env.PATH ?? '', ...env }, cwd: config });
  }, 120_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  it('success: the run is followed through durable records, reads stay responsive during it, and the outcome is the RunRecord - linked to the workspace from the first write', async () => {
    const ws = workspace('success', { 'a.txt': 'a' });
    const exe = executable('success', { outcome: 'success', step_ms: 400 });
    const h = host(ws, exe);
    const t0 = Date.now();
    const started = await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' });
    const ackMs = Date.now() - t0;
    expect(started.started).toBe(true);
    const id = started.launch_id!;
    const seen: string[] = [];
    const readLatencies: number[] = [];
    let lastSeq = 0; let firstRecordMs = -1; let ended: Json | null = null;
    const identity = ((await read({ operation: 'workspace.describe', workspace: ws })).data as Json).identity as string;
    while (h.state().phase !== 'exited' || ended === null) {
      const r0 = Date.now();
      const s = await status(ws, id); // a read while the execution process runs
      readLatencies.push(Date.now() - r0);
      if (s.error) { expect(s.error.code).toBe('LAUNCH_NOT_FOUND'); } else {
        if (firstRecordMs < 0) firstRecordMs = Date.now() - t0;
        const d = s.data as Json;
        seen.push(`${d.launch.phase}/${d.liveness}`);
        expect(d.launch.workspace).toEqual({ path: ws, identity }); // linked to the workspace from the first record
        if (d.evidence.state !== 'absent' && d.evidence.line_count > lastSeq) {
          const page = await read({ operation: 'evidence.records', run_id: id, from_seq: lastSeq + 1, limit: 200 });
          expect(page.error).toBeNull();
          const recs = (page.data as Json).records as Array<{ seq: number }>;
          expect(recs[0]!.seq).toBe(lastSeq + 1);
          lastSeq = recs[recs.length - 1]!.seq;
        }
        if (d.launch.phase === 'ENDED') ended = d;
      }
      await sleep(60);
      if (Date.now() - t0 > 90_000) throw new Error('run did not end');
    }
    expect(seen).toContain('RUNNING/live'); // observed in progress, from Kriya's records
    expect(Math.max(...readLatencies)).toBeLessThan(3000); // the read process was never blocked by the execution process
    const d = ended!;
    expect(d.run_record).toMatchObject({ found: true, lifecycle_state: 'SUCCESS', terminal_status: 'SUCCESS' });
    expect(d.launch.ended).toMatchObject({ exit_code: 0, workflow_status: 'success', quality_gates_passed: true });
    expect(d.evidence).toMatchObject({ state: 'sealed', sealed: true });
    expect(d.liveness).toBe('ended');
    expect(h.state()).toMatchObject({ phase: 'exited', exit: { code: 0, signal: null } });
    // the evidence store is the launch's own, sealed and verified, with the workspace identity in its manifest
    expect(((await read({ operation: 'evidence.verify', run_id: id })).data as Json).status).toBe('VERIFIED');
    const manifest = (await read({ operation: 'evidence.manifest', run_id: id })).data as Json;
    expect(manifest.manifest.workspace_identity).toBe(identity);
    expect(manifest.manifest.run_id ?? id).toBe(id);
    // after acquiring a snapshot the trace row is listed for THIS workspace only, linked through its recorded evidence event
    const snap = (await read({ operation: 'snapshot.acquire', workspace: ws })).data as Json;
    const list = (await read({ operation: 'history.list', snapshot_id: snap.snapshot_id, workspace: ws })).data as Json;
    expect(list.runs.map((r: Json) => r.run_id)).toContain(d.launch.ended.trace_run_id);
    const link = (await read({ operation: 'evidence.manifest', run_id: d.launch.ended.trace_run_id, snapshot_id: snap.snapshot_id, from_trace: true })).data as Json;
    expect(link.link.evidence_run_id).toBe(id);
    process.stderr.write(`[exec-timing] start->ack ${ackMs} ms, ack->launch record visible ${firstRecordMs - ackMs} ms, read latency during run max ${Math.max(...readLatencies)} ms (median ${[...readLatencies].sort((a, b) => a - b)[Math.floor(readLatencies.length / 2)]} ms, n=${readLatencies.length})\n`);
  }, 180_000);

  it('the launch record never holds the goal text, and a hostile goal reaches no shell', async () => {
    const ws = workspace('hostile');
    const marker = join(base, 'pwned');
    const goal = `x; touch ${marker}; $(touch ${marker}) \`touch ${marker}\``;
    const h = host(ws, executable('hostile', { outcome: 'success', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal });
    expect(r.started).toBe(true);
    await untilExited(h);
    expect(existsSync(marker)).toBe(false);
    const files = readdirSync(dirOf(ws));
    expect(files).toEqual([`${r.launch_id}.json`]);
    const raw = readFileSync(join(dirOf(ws), files[0]!), 'utf8');
    expect(raw).not.toContain('touch');
    expect(JSON.parse(raw).goal.chars).toBe(goal.length);
  }, 120_000);

  it('a durable failure outcome is FAILURE (the process still exits normally)', async () => {
    const ws = workspace('failure');
    const h = host(ws, executable('failure', { outcome: 'failure', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    await untilExited(h);
    const d = (await status(ws, r.launch_id!)).data as Json;
    expect(d.run_record).toMatchObject({ terminal_status: 'FAILURE' });
    expect(d.launch.ended).toMatchObject({ exit_code: 1, workflow_status: 'failure', failure_category: 'quality_gate_failed' });
  }, 120_000);

  it('a crash inside the run is sealed: RunRecord FAILURE, launch ENDED with the error, no trace row invented', async () => {
    const ws = workspace('crash');
    const h = host(ws, executable('crash', { outcome: 'crash', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    await untilExited(h);
    const d = (await status(ws, r.launch_id!)).data as Json;
    expect(d.run_record).toMatchObject({ found: true, terminal_status: 'FAILURE' });
    expect(d.launch).toMatchObject({ phase: 'ENDED' });
    expect(d.launch.ended.error).toContain('scripted crash');
    expect(d.launch.ended.trace_run_id).toBeNull();
  }, 120_000);

  it('a process killed mid-run leaves a launch that is RUNNING, not live, and interrupted - never a success', async () => {
    const ws = workspace('die');
    const h = host(ws, executable('die', { outcome: 'die', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    await untilExited(h);
    expect(h.state().exit).toEqual({ code: null, signal: 'SIGKILL' });
    const d = (await status(ws, r.launch_id!)).data as Json;
    expect(d.launch.phase).toBe('RUNNING');
    expect(d.workspace_lock.held).toBe(false); // the OS released the kernel lock
    expect(d.liveness).toBe('interrupted');
    expect(d.run_record.terminal_status).toBeNull();
    expect(d.evidence.sealed).toBe(false);
    // the workspace is then flagged for recovery by Kriya's own assessment, and the next preflight says so
    const next = (await read({ operation: 'execution.preflight', workspace: ws })).data as Json;
    expect(next.ready).toBe(false);
    expect(next.blockers[0].code).toBe('RECOVERY_REQUIRED');
  }, 120_000);

  it('a process that exits before Kriya records anything is an exit code and LAUNCH_NOT_FOUND', async () => {
    const ws = workspace('early');
    const exe = join(base, 'kriya-early');
    writeFileSync(exe, '#!/bin/sh\nexit 1\n');
    chmodSync(exe, 0o755);
    const h = host(ws, exe);
    const r = await h.start({ workspace: ws, goal: 'g' });
    expect(r.started).toBe(true);
    await untilExited(h);
    expect(h.state().exit).toEqual({ code: 1, signal: null });
    expect((await status(ws, r.launch_id!)).error.code).toBe('LAUNCH_NOT_FOUND');
  }, 120_000);

  it('operator-only configuration in the folder blocks the start before any process exists', async () => {
    const ws = workspace('authority', { 'kriya.yaml': 'execution_policy:\n  enabled: true\n' });
    const h = host(ws, executable('authority', { outcome: 'success' }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    expect(r).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_BLOCKED' } });
    expect(r.refusal!.blockers![0]!.code).toBe('CONFIG_AUTHORITY_REFUSED');
    expect(h.state().phase).toBe('idle');
    expect(existsSync(join(ws, '.kriya'))).toBe(false); // nothing was written into the folder
  }, 120_000);

  it('the preflight the host saw and the one the launched process recorded are the same resolution (host-side fields aside)', async () => {
    const ws = workspace('parity', { 'kriya.yaml': 'llm:\n  context_window: 12345\n' });
    const h = host(ws, executable('parity', { outcome: 'success', step_ms: 50 }));
    const before = (await read({ operation: 'execution.preflight', workspace: ws })).data as Json;
    const r = await h.start({ workspace: ws, goal: 'g' });
    await untilExited(h);
    const record = JSON.parse(readFileSync(join(dirOf(ws), `${r.launch_id}.json`), 'utf8')) as Json;
    const strip = (p: Json) => { const { state, ...rest } = p; return { ...rest, state: { directory: state.directory, source: state.source } }; };
    expect(strip(record.preflight)).toEqual(strip(before));
    expect(before.configuration.source).toBe('auto_discovered_cwd');
    expect(before.model.context_window).toBe(12345);
  }, 120_000);

  it('one run at a time: a held run blocks a second start in the host AND the folder; reads continue; the held run then completes', async () => {
    const ws = workspace('hold');
    const release = join(base, 'release-hold');
    const h = host(ws, executable('hold', { outcome: 'hold', step_ms: 50, release }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    expect(r.started).toBe(true);
    let live: Json | null = null;
    for (let i = 0; i < 200 && live === null; i += 1) { const s = await status(ws, r.launch_id!); if (!s.error && s.data.liveness === 'live' && s.data.evidence.line_count >= 5) live = s.data; else await sleep(100); }
    expect(live).not.toBeNull();
    expect(live!.workspace_lock).toEqual({ held: true, held_by_this_launch: true });
    expect(h.active()).toBe(true);
    expect(await h.start({ workspace: ws, goal: 'second' })).toMatchObject({ started: false, refusal: { code: 'EXECUTION_BUSY' } });
    const other = host(ws, executable('hold2', { outcome: 'success' })); // a second host (another window/process) is stopped by Kriya's own preflight
    expect(await other.start({ workspace: ws, goal: 'second' })).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_BLOCKED', blockers: [{ code: 'RUN_ACTIVE' }] } });
    expect(((await read({ operation: 'capabilities' })).data as Json).kup_versions).toEqual([1]); // navigation still answers
    writeFileSync(release, '');
    await untilExited(h);
    expect(((await status(ws, r.launch_id!)).data as Json).run_record.terminal_status).toBe('SUCCESS');
  }, 180_000);

  it('quitting during a run interrupts it like Ctrl-C: Kriya seals the run as a failure and the launch as exit 130 - nothing is reported as finished work', async () => {
    const ws = workspace('quit');
    const release = join(base, 'release-never');
    const h = host(ws, executable('quit', { outcome: 'hold', step_ms: 50, release }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    for (let i = 0; i < 200; i += 1) { const s = await status(ws, r.launch_id!); if (!s.error && s.data.evidence.line_count >= 4) break; await sleep(100); }
    await h.shutdown();
    expect(h.state()).toMatchObject({ phase: 'exited', diagnostic: 'terminated_by_the_host' });
    const d = (await status(ws, r.launch_id!)).data as Json;
    expect(d.launch.phase).toBe('ENDED');
    expect(d.launch.ended.exit_code).toBe(130);
    expect(d.run_record.terminal_status).toBe('FAILURE');
    expect(d.workspace_lock.held).toBe(false);
  }, 180_000);
});
