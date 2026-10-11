/**
 * Start Run end to end against the REAL Kriya: the real `kriya generate --json --launch-id` command, preflight, launch record, run coordinator
 * (workspace lock + RunRecord), attempt-evidence recorder, trace logger and the real persistent read process. The ONE thing replaced is the workflow body
 * that would call a model (fixtures/controlled_kriya.py) - no model is ever called. Needs the repository's .venv (skipped, loudly, when absent).
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ApprovalHost } from '../src/main/approval_host';
import { ExecutionHost } from '../src/main/execution_host';
import { launchExecution } from '../src/main/execution_process';
import type { KupRequest } from '../src/main/ipc_contract';
import { KriyaService } from '../src/main/kriya_service';
import { runKriya } from '../src/main/kriya_process';

const ROOT = join(__dirname, '..', '..', '..');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const CONTROLLED = join(__dirname, 'fixtures', 'controlled_kriya.py');
const available = existsSync(PYTHON);

interface Env { [k: string]: string }
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXED_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';
/** The `git` candidates on a PATH, in order, and whether each RUNS (the macOS Xcode shim exits 69 until its license is accepted). */
function gitCandidates(path: string): Array<{ path: string; works: boolean }> {
  return path.split(':').map((d) => `${d}/git`).filter((c) => existsSync(c)).map((c) => ({ path: c, works: spawnSync(c, ['--version'], { encoding: 'utf8' }).status === 0 }));
}
const firstGitWorks = gitCandidates(FIXED_PATH)[0]?.works ?? false;

let counter = 0;
const mint = () => { counter += 1; return `20261010T1010${String(counter).padStart(2, '0')}-${(0xabcd0000 + counter).toString(16)}`; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe.skipIf(!available)('Start Run against real Kriya with a controlled workflow body', () => {
  let base = ''; let state = ''; let config = ''; let home = '';
  let env: Env = {};
  let childPath = FIXED_PATH;
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
      launch: launchExecution, prepare: over.prepare ?? (() => ({ ok: true, executable: exe, env: { ...env } })),
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
    // The host's fixed PATH, with a working git first ONLY when this machine's first git is broken (a license-gated Xcode shim): the tests exercise execution,
    // not this machine's toolchain; the blocked case itself is the test below.
    if (!firstGitWorks) {
      const working = gitCandidates(process.env.PATH ?? '').concat(gitCandidates(FIXED_PATH)).find((c) => c.works);
      if (working) { mkdirSync(join(base, 'bin')); symlinkSync(working.path, join(base, 'bin', 'git')); childPath = `${join(base, 'bin')}:${FIXED_PATH}`; }
    }
    env = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1', PATH: childPath };
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

  it('GREENFIELD end to end: an EMPTY folder opens, goes through the same preflight/start path, is attributed from the first write, records an ADDED file and refreshes into its own run list', async () => {
    const ws = workspace('greenfield-empty'); // no files at all
    expect(readdirSync(ws)).toEqual([]);
    const described = ((await read({ operation: 'workspace.describe', workspace: ws })).data as Json);
    expect(described.checkout).toMatchObject({ state: 'not_a_repository', head: null, dirty: null }); // facts only; nothing classifies it as greenfield
    const pre = (await read({ operation: 'execution.preflight', workspace: ws })).data as Json;
    expect(pre.ready).toBe(true); // the same preflight an existing project gets
    const h = host(ws, executable('greenfield', { outcome: 'success', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal: 'Create a utility library with a clamp function.' });
    expect(r.started).toBe(true);
    await untilExited(h);
    const status1 = (await status(ws, r.launch_id!)).data as Json;
    expect(status1.launch.workspace.identity).toBe(described.identity); // linked to the folder that was opened
    expect(status1.run_record.terminal_status).toBe('SUCCESS');
    const recs = ((await read({ operation: 'evidence.records', run_id: r.launch_id!, kind: ['candidate.change'] })).data as Json).records as Array<{ payload: Json }>;
    expect(recs).toHaveLength(1);
    expect(recs[0]!.payload).toMatchObject({ created: true, deleted: false, unchanged: false }); // the cockpit classifies this ADDED: new file, no baseline
    const snap = (await read({ operation: 'snapshot.acquire', workspace: ws })).data as Json;
    const list = (await read({ operation: 'history.list', snapshot_id: snap.snapshot_id, workspace: ws })).data as Json;
    expect(list.runs.map((x: Json) => x.run_id)).toEqual([status1.launch.ended.trace_run_id]); // terminal refresh: exactly this folder's one run
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

  it('a decision the desktop cannot make stops the run, is recorded with its reason by Kriya, and is read back as approval_required - nothing is approved', async () => {
    const ws = workspace('approval');
    const h = host(ws, executable('approval', { outcome: 'approval', step_ms: 50 }));
    const r = await h.start({ workspace: ws, goal: 'g' });
    await untilExited(h);
    const d = (await status(ws, r.launch_id!)).data as Json;
    expect(d.launch.approval_required).toMatchObject({ kind: 'change_approval', reason: 'the proposed change touches a sensitive path', details: { files: ['.github/workflows/ci.yml'] } });
    expect(d.launch.phase).toBe('ENDED');
    expect(d.run_record.terminal_status).toBe('FAILURE'); // the run did not proceed past the decision
    expect(d.launch.ended.quality_gates_passed).toBeNull();
    expect(existsSync(join(ws, '.github'))).toBe(false); // nothing was applied to the folder
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

  it.skipIf(firstGitWorks)('on a machine whose first git on the fixed PATH does not run, the preflight blocks the start with the reason (the live-smoke finding) and nothing is launched', async () => {
    const ws = workspace('gitshim');
    const blockedEnv = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1', PATH: FIXED_PATH };
    const blockedService = new KriyaService({ executable: join(ROOT, '.venv', 'bin', 'kriya'), cwd: config, env: blockedEnv });
    try {
      const h = new ExecutionHost({ launch: launchExecution, prepare: () => ({ ok: true, executable: executable('gitshim', { outcome: 'success' }), env: blockedEnv }), currentWorkspace: () => ws, mintLaunchId: mint, now: () => new Date(),
        preflight: async (w) => { const out = await blockedService.request({ operation: 'execution.preflight', workspace: w }); return out.kind === 'json' ? out.json : { error: { code: 'HOST_ERROR', message: out.message }, data: null }; } });
      const r = await h.start({ workspace: ws, goal: 'g' });
      expect(r).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_BLOCKED' } });
      expect(r.refusal!.blockers!.map((b) => b.code)).toContain('TOOL_UNAVAILABLE');
      expect(r.refusal!.message).toMatch(/git is required for every run/);
      expect(existsSync(join(ws, '.kriya'))).toBe(false);
    } finally { await blockedService.stop(); }
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

  // ---------------------------------------------------------------- the human review of a change (GUI final): the REAL launch, the REAL approval action
  const realKriya = () => join(ROOT, '.venv', 'bin', 'kriya');
  function approvals(ws: string) {
    return new ApprovalHost({
      run: (o) => runKriya({ executable: o.executable, argv: o.argv, cwd: o.cwd, env: o.env, limits: o.limits }),
      prepare: () => ({ ok: true, executable: realKriya(), env: { ...env } }), currentWorkspace: () => ws,
    });
  }
  async function untilPending(ws: string, id: string, ms = 60_000): Promise<Json> {
    const end = Date.now() + ms;
    for (;;) {
      const s = await status(ws, id);
      const pending = s.error ? null : (s.data as Json).launch.pending_approval;
      if (pending && pending.status === 'PENDING') return pending;
      if (Date.now() > end) throw new Error('no pending review appeared');
      await sleep(50);
    }
  }
  const decision = (ws: string, id: string, review: Json, choice: 'approve' | 'reject', over: Json = {}) =>
    ({ workspace: ws, launch_id: id, review_id: review.review_id, candidate_digest: review.candidate_digest, decision: choice, ...over });

  it('APPROVE end to end: the run waits at the real approval path, the typed action is accepted, Kriya records it BEFORE the run continues, and the sealed outcome is SUCCESS linked to the real trace row', async () => {
    const ws = workspace('review-approve', { 'a.txt': 'a' });
    const h = host(ws, executable('review-approve', { outcome: 'review', step_ms: 100 }));
    const id = (await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' })).launch_id!;
    const pending = await untilPending(ws, id);
    expect(pending).toMatchObject({ kind: 'change_approval', status: 'PENDING', decision: null });
    expect(pending.files[0]).toMatchObject({ filepath: 'src/clamp.py', lines_added: 2, lines_removed: 0 });
    expect(pending.reason).toContain('Automated Code Review');
    // while it waits: live, the workspace is held, no outcome exists
    const waiting = (await status(ws, id)).data as Json;
    expect(waiting).toMatchObject({ liveness: 'live', workspace_lock: { held: true, held_by_this_launch: true } });
    expect(waiting.launch.ended).toBeNull();
    const a = approvals(ws);
    const accepted = await a.decide(decision(ws, id, pending, 'approve'));
    expect(accepted).toMatchObject({ accepted: true, code: 'ACCEPTED', decision: 'approve', review_id: pending.review_id });
    await untilExited(h);
    const d = (await status(ws, id)).data as Json;
    expect(d.run_record).toMatchObject({ found: true, terminal_status: 'SUCCESS' });
    expect(d.launch.pending_approval).toMatchObject({ status: 'APPROVED', decision: { decision: 'approve', source: 'launch-decision' } });
    expect(d.launch.ended).toMatchObject({ exit_code: 0, workflow_status: 'success' });
    // the link is the REAL trace row (the controlled body's result run_id is not it)
    const snap = (await read({ operation: 'snapshot.acquire', workspace: ws })).data as Json;
    const list = (await read({ operation: 'history.list', snapshot_id: snap.snapshot_id, workspace: ws })).data as Json;
    expect(list.runs.map((r: Json) => r.run_id)).toContain(d.launch.ended.trace_run_id);
    expect(h.state()).toMatchObject({ phase: 'exited', exit: { code: 0 } });
  }, 120_000);

  it('REJECT end to end: the durable outcome is the recorded human rejection (RunRecord FAILURE, trace row human_rejected), one attempt, no retry', async () => {
    const ws = workspace('review-reject', { 'a.txt': 'a' });
    const h = host(ws, executable('review-reject', { outcome: 'review', step_ms: 100 }));
    const id = (await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' })).launch_id!;
    const pending = await untilPending(ws, id);
    expect(await approvals(ws).decide(decision(ws, id, pending, 'reject'))).toMatchObject({ accepted: true, decision: 'reject' });
    await untilExited(h);
    const d = (await status(ws, id)).data as Json;
    expect(d.run_record).toMatchObject({ found: true, terminal_status: 'FAILURE' });
    expect(d.launch.pending_approval).toMatchObject({ status: 'REJECTED', decision: { decision: 'reject' } });
    expect(d.launch.ended).toMatchObject({ exit_code: 1 });
    expect(d.launch.ended.trace_run_id).not.toBeNull();
    expect(d.launch.ended.trace_run_id).not.toBe('20200101T000000-0badc0de'); // never the result's own run_id
    expect(d.launch.approval_history).toEqual([]);
    const records = (await read({ operation: 'evidence.records', run_id: id, limit: 200 })).data as Json;
    expect(records.records.filter((r: Json) => r.kind === 'attempt.opened')).toHaveLength(1); // no retry was invented
  }, 120_000);

  it('a decision for another candidate, another run, another folder, or in the wrong shape fails closed - and the run keeps waiting until the right one arrives', async () => {
    const ws = workspace('review-stale', { 'a.txt': 'a' });
    const other = workspace('review-elsewhere');
    const h = host(ws, executable('review-stale', { outcome: 'review', step_ms: 100 }));
    const id = (await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' })).launch_id!;
    const pending = await untilPending(ws, id);
    const a = approvals(ws);
    expect(await a.decide(decision(ws, id, pending, 'approve', { candidate_digest: 'sha256:' + '0'.repeat(64) }))).toMatchObject({ accepted: false, code: 'STALE_REVIEW' });
    expect(await a.decide(decision(ws, id, pending, 'approve', { review_id: 'ra-' + '0'.repeat(24) }))).toMatchObject({ accepted: false, code: 'STALE_REVIEW' });
    expect(await a.decide(decision(ws, '20200101T000000-00000000', pending, 'approve'))).toMatchObject({ accepted: false, code: 'LAUNCH_NOT_FOUND' });
    expect(await a.decide(decision(other, id, pending, 'approve'))).toMatchObject({ accepted: false, code: 'WORKSPACE_NOT_OPEN' }); // not the open folder
    expect(await a.decide({ ...decision(ws, id, pending, 'approve'), extra: 'x' })).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    expect(await a.decide(decision(ws, id, pending, 'approve-always' as never))).toMatchObject({ accepted: false, code: 'INVALID_REQUEST' });
    expect(((await status(ws, id)).data as Json).launch.pending_approval.status).toBe('PENDING'); // nothing was recorded by any refusal
    expect(await a.decide(decision(ws, id, pending, 'reject'))).toMatchObject({ accepted: true });
    await untilExited(h);
  }, 120_000);

  it('a duplicate or late decision is refused: the second approve and a decision after the run ended change nothing', async () => {
    const ws = workspace('review-duplicate', { 'a.txt': 'a' });
    const h = host(ws, executable('review-duplicate', { outcome: 'review', step_ms: 100 }));
    const id = (await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' })).launch_id!;
    const pending = await untilPending(ws, id);
    const a = approvals(ws);
    const [first, second] = await Promise.all([a.decide(decision(ws, id, pending, 'approve')), a.decide(decision(ws, id, pending, 'reject'))]);
    expect([first.accepted, second.accepted].filter(Boolean)).toHaveLength(1); // exactly one decision was taken (the host serializes; Kriya would refuse the other anyway)
    await untilExited(h);
    const late = await a.decide(decision(ws, id, pending, 'approve'));
    expect(late).toMatchObject({ accepted: false });
    expect(['LAUNCH_ENDED', 'ALREADY_DECIDED']).toContain(late.code);
    const d = (await status(ws, id)).data as Json;
    expect(d.launch.pending_approval.status).toBe(first.accepted ? 'APPROVED' : 'REJECTED');
  }, 120_000);

  it('a launch process that dies while the review is pending cannot be approved any more, and is never read as a success', async () => {
    const ws = workspace('review-died', { 'a.txt': 'a' });
    const h = host(ws, executable('review-died', { outcome: 'review', step_ms: 100 }));
    const id = (await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' })).launch_id!;
    const pending = await untilPending(ws, id);
    const pid = ((await status(ws, id)).data as Json).launch.process.pid as number;
    process.kill(pid, 'SIGKILL');
    await untilExited(h);
    const refused = await approvals(ws).decide(decision(ws, id, pending, 'approve'));
    expect(refused).toMatchObject({ accepted: false, code: 'LAUNCH_NOT_ACTIVE' });
    const d = (await status(ws, id)).data as Json;
    expect(d.liveness).toBe('interrupted');
    expect(d.launch.ended).toBeNull();
    expect(d.run_record.terminal_status).not.toBe('SUCCESS');
  }, 120_000);
});
