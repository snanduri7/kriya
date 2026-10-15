/**
 * Runtime-command approval from the desktop against the REAL Kriya (Demo V2): the real `kriya generate --json --launch-id` command, launch record, run coordinator, the REAL
 * runtime-command approval path (`request_runtime_command_approval`, the call the executor awaits), the real persistent read process answering `runtime_command.review`, the
 * host's RuntimeCommandHost in front of it with a SCRIPTED native confirmation, and the real `launch-decision --kind runtime_command`. Only the workflow body that would call a
 * model is the controlled stand-in (fixtures/controlled_kriya.py: after an approval it RECORDS the approved commands as executed, exactly the gate outcome the executor writes).
 *
 * What this proves that the unit tests cannot: the commands the host re-proves and shows are the commands Kriya recorded; a native Cancel leaves the REAL record PENDING and the run
 * waiting; an approval is durable BEFORE the run continues; a stale fingerprint is refused and records nothing; nothing is "executed" before the decision; ApplicationOutcome reads the
 * executor's recorded steps. No model is called. Needs the repository's .venv (skipped, loudly, when absent).
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ExecutionHost } from '../src/main/execution_host';
import { launchExecution } from '../src/main/execution_process';
import type { KupRequest } from '../src/main/ipc_contract';
import { KriyaService } from '../src/main/kriya_service';
import { runKriya } from '../src/main/kriya_process';
import { RuntimeCommandHost } from '../src/main/runtime_command_host';

const ROOT = join(__dirname, '..', '..', '..');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const CONTROLLED = join(__dirname, 'fixtures', 'controlled_kriya.py');
const available = existsSync(PYTHON);
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXED_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';
const gitCandidates = (path: string) => path.split(':').map((d) => `${d}/git`).filter((c) => existsSync(c)).map((c) => ({ path: c, works: spawnSync(c, ['--version'], { encoding: 'utf8' }).status === 0 }));
let counter = 0;
const mint = () => { counter += 1; return `20261015T1010${String(counter).padStart(2, '0')}-${(0xabce0000 + counter).toString(16)}`; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const COMMANDS = [['mvn', '-q', 'package'], ['java', '-cp', 'target/classes', 'com.example.App']];

describe.skipIf(!available)('runtime-command approval against real Kriya', () => {
  let base = ''; let state = ''; let config = ''; let home = '';
  let env: Record<string, string> = {};
  let service: KriyaService;
  let dialogs = 0;
  let nativeAnswer = true;
  let openWorkspace: string | null = null;

  const read = async (request: KupRequest): Promise<Json> => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(`read failed: ${out.code} ${out.message}`);
    return out.json as Json;
  };
  const executable = (name: string, script: Json): string => {
    const path = join(base, `kriya-${name}`);
    writeFileSync(path, `#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='${JSON.stringify(script)}'\nexec '${PYTHON}' '${CONTROLLED}' "$@"\n`);
    chmodSync(path, 0o755);
    return path;
  };
  const workspace = (name: string): string => {
    const dir = join(base, 'ws', name);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, 'a.txt'), 'a');
    return realpathSync(dir);
  };
  const host = (ws: string, exe: string) => new ExecutionHost({
    launch: launchExecution, prepare: () => ({ ok: true, executable: exe, env: { ...env } }), currentWorkspace: () => ws, mintLaunchId: mint, now: () => new Date(),
    preflight: (w) => read({ operation: 'execution.preflight', workspace: w }), graceMs: 4000,
  });
  const decider = (ws: string) => new RuntimeCommandHost({
    run: (o) => runKriya({ executable: o.executable, argv: o.argv, cwd: o.cwd, env: o.env, limits: o.limits }),
    prepare: () => ({ ok: true, executable: join(ROOT, '.venv', 'bin', 'kriya'), env: { ...env } }),
    reviewable: (workspaceArg, launchId, reviewId) => read({ operation: 'runtime_command.review', workspace: workspaceArg, launch_id: launchId, review_id: reviewId }),
    confirm: async () => { dialogs += 1; return nativeAnswer; }, currentWorkspace: () => openWorkspace ?? ws,
  });
  const status = async (ws: string, id: string): Promise<Json> => (await read({ operation: 'launch.status', workspace: ws, launch_id: id })).data as Json;
  async function untilPending(ws: string, id: string, ms = 60_000): Promise<Json> {
    const end = Date.now() + ms;
    for (;;) {
      const s = await read({ operation: 'launch.status', workspace: ws, launch_id: id });
      const pending = s.error ? null : (s.data as Json).launch.pending_approval;
      if (pending && pending.status === 'PENDING') return pending;
      if (Date.now() > end) throw new Error('no pending review appeared');
      await sleep(50);
    }
  }
  async function untilExited(h: ExecutionHost, ms = 60_000) { const end = Date.now() + ms; while (h.state().phase !== 'exited') { if (Date.now() > end) throw new Error('execution did not exit'); await sleep(40); } }
  const request = (ws: string, id: string, review: Json, decision: 'approve' | 'reject', over: Json = {}) =>
    ({ workspace: ws, launch_id: id, review_id: review.review_id, command_fingerprint: review.command_fingerprint, decision, ...over });
  const start = async (name: string, script: Json = {}) => {
    const ws = workspace(name);
    const h = host(ws, executable(name, { outcome: 'runtime_review', step_ms: 100, ...script }));
    const id = (await h.start({ workspace: ws, goal: 'Create a small Java application that prints Hello from Kriya and run it.' })).launch_id!;
    return { ws, h, id, pending: await untilPending(ws, id) };
  };

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), 'kriya-gui-runtime-'));
    state = join(base, 'state'); config = join(base, 'config'); home = join(base, 'home');
    for (const d of [state, config, home, join(base, 'ws')]) mkdirSync(d);
    let childPath = FIXED_PATH;
    if (!(gitCandidates(FIXED_PATH)[0]?.works ?? false)) {
      const working = gitCandidates(process.env.PATH ?? '').concat(gitCandidates(FIXED_PATH)).find((c) => c.works);
      if (working) { mkdirSync(join(base, 'bin')); symlinkSync(working.path, join(base, 'bin', 'git')); childPath = `${join(base, 'bin')}:${FIXED_PATH}`; }
    }
    env = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1', PATH: childPath };
    service = new KriyaService({ executable: join(ROOT, '.venv', 'bin', 'kriya'), cwd: config, env });
    execFileSync(PYTHON, ['-c', 'import kriya.cli'], { env: { PATH: process.env.PATH ?? '', ...env }, cwd: config });
  }, 120_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  it('APPROVE end to end: the real pending review is a typed runtime subject, the host re-proves it, the native dialog is shown once, the decision is durable BEFORE the run continues, and the executor\'s recorded steps are what ApplicationOutcome reads', async () => {
    const { ws, h, id, pending } = await start('approve');
    dialogs = 0; nativeAnswer = true;
    expect(pending).toMatchObject({ kind: 'runtime_command', status: 'PENDING', unit_id: 's4', working_directory_kind: 'ISOLATED_WORKTREE', input_channel: 'none', stdin_present: false });
    expect(pending.commands).toEqual(COMMANDS);
    expect(pending.files).toBeUndefined();                                    // never represented as files
    // the host's proof: Kriya re-proves the recorded commands against the fingerprint before they are shown
    const proof = await read({ operation: 'runtime_command.review', workspace: ws, launch_id: id, review_id: pending.review_id });
    expect(proof.error).toBeNull();
    expect(proof.data).toMatchObject({ verified: true, status: 'PENDING', review_id: pending.review_id, command_fingerprint: pending.command_fingerprint, commands: COMMANDS });
    // while it waits: nothing is recorded as executed and the run holds the workspace
    const waiting = await status(ws, id);
    expect(waiting).toMatchObject({ liveness: 'live', workspace_lock: { held: true, held_by_this_launch: true } });
    expect(waiting.launch.ended).toBeNull();
    const answer = await decider(ws).decide(request(ws, id, pending, 'approve'));
    expect(answer).toMatchObject({ accepted: true, code: 'ACCEPTED', decision: 'approve', review_id: pending.review_id });
    expect(dialogs).toBe(1);
    await untilExited(h);
    const done = await status(ws, id);
    expect(done.run_record).toMatchObject({ found: true, terminal_status: 'SUCCESS' });
    expect(done.launch.pending_approval).toMatchObject({ kind: 'runtime_command', status: 'APPROVED', decision: { decision: 'approve', source: 'launch-decision' } });
    // ApplicationOutcome: EXECUTED, exit 0, the printed output - from the recorded steps of the approved commands
    const snap = (await read({ operation: 'snapshot.acquire', workspace: ws })).data as Json;
    const detail = (await read({ operation: 'history.detail', snapshot_id: snap.snapshot_id, run_id: done.launch.ended.trace_run_id })).data as Json;
    expect(detail.application_outcome.data).toMatchObject({ status: 'EXECUTED', succeeded: true, exit_code: 0, commands: COMMANDS.map((c) => c.join(' ')) });
    expect(detail.application_outcome.data.stdout.text).toContain('Hello from Kriya');
  }, 180_000);

  it('native CANCEL: nothing is sent, the REAL record stays PENDING, the run keeps waiting - and a later confirmed approval still works', async () => {
    const { ws, h, id, pending } = await start('cancel');
    dialogs = 0; nativeAnswer = false;
    const d = decider(ws);
    expect(await d.decide(request(ws, id, pending, 'approve'))).toMatchObject({ accepted: false, code: 'CONFIRMATION_DECLINED', decision: null });
    expect(dialogs).toBe(1);
    await sleep(400);
    const still = await status(ws, id);
    expect(still.launch.pending_approval).toMatchObject({ review_id: pending.review_id, status: 'PENDING', decision: null });
    expect(still.launch.ended).toBeNull();
    expect(h.state().phase).not.toBe('exited');
    nativeAnswer = true;                                                        // the person looks again and confirms
    expect(await d.decide(request(ws, id, pending, 'approve'))).toMatchObject({ accepted: true });
    await untilExited(h);
    expect((await status(ws, id)).run_record).toMatchObject({ terminal_status: 'SUCCESS' });
  }, 180_000);

  it('REJECT: no native dialog, the real decision is recorded, nothing is executed, the run FAILS with no ApplicationOutcome claiming execution', async () => {
    const { ws, h, id, pending } = await start('reject');
    dialogs = 0; nativeAnswer = true;
    expect(await decider(ws).decide(request(ws, id, pending, 'reject'))).toMatchObject({ accepted: true, decision: 'reject' });
    expect(dialogs).toBe(0);
    await untilExited(h);
    const done = await status(ws, id);
    expect(done.launch.pending_approval).toMatchObject({ kind: 'runtime_command', status: 'REJECTED', decision: { decision: 'reject' } });
    expect(done.run_record).toMatchObject({ terminal_status: 'FAILURE' });
    const snap = (await read({ operation: 'snapshot.acquire', workspace: ws })).data as Json;
    const detail = (await read({ operation: 'history.detail', snapshot_id: snap.snapshot_id, run_id: done.launch.ended.trace_run_id })).data as Json;
    expect(detail.application_outcome.data.status).not.toBe('EXECUTED');
  }, 180_000);

  it('a stale or invented fingerprint: the host cannot prove it, shows NO dialog, sends NOTHING; Kriya refuses the same fingerprint on its own; the record stays PENDING', async () => {
    const { ws, h, id, pending } = await start('stale');
    dialogs = 0; nativeAnswer = true;
    const bogus = 'sha256:' + '9'.repeat(64);
    expect(await decider(ws).decide(request(ws, id, pending, 'approve', { command_fingerprint: bogus }))).toMatchObject({ accepted: false, code: 'COMMAND_NOT_REVIEWABLE' });
    expect(dialogs).toBe(0);
    // another run (a launch id Kriya never recorded for this folder) and another review/unit (an identity this run never issued) are equally unprovable: no dialog, nothing sent
    expect(await decider(ws).decide(request(ws, id, pending, 'approve', { launch_id: '20261015T101099-deadbeef' }))).toMatchObject({ accepted: false, code: 'COMMAND_NOT_REVIEWABLE' });
    expect(await decider(ws).decide(request(ws, id, pending, 'approve', { review_id: 'ra-' + 'f'.repeat(24) }))).toMatchObject({ accepted: false, code: 'COMMAND_NOT_REVIEWABLE' });
    expect(dialogs).toBe(0);
    // Kriya's own decision action refuses it independently (the host's proof is not the only line of defence)
    const direct = await runKriya({ executable: join(ROOT, '.venv', 'bin', 'kriya'), cwd: ws, env, limits: { timeoutMs: 30_000 },
      argv: ['launch-decision', '--launch-id', id, '--review-id', pending.review_id, '--candidate-digest', bogus, '--decision', 'approve', '--kind', 'runtime_command'] });
    expect(direct.kind === 'json' ? (direct.json as Json).code : null).toBe('STALE_REVIEW');
    // a change-review decision cannot approve a runtime review
    const wrongKind = await runKriya({ executable: join(ROOT, '.venv', 'bin', 'kriya'), cwd: ws, env, limits: { timeoutMs: 30_000 },
      argv: ['launch-decision', '--launch-id', id, '--review-id', pending.review_id, '--candidate-digest', pending.command_fingerprint, '--decision', 'approve'] });
    expect(wrongKind.kind === 'json' ? (wrongKind.json as Json).code : null).toBe('WRONG_REVIEW_KIND');
    expect((await status(ws, id)).launch.pending_approval).toMatchObject({ status: 'PENDING', decision: null });
    expect(await decider(ws).decide(request(ws, id, pending, 'reject'))).toMatchObject({ accepted: true });   // clean up: let the run end
    await untilExited(h);
    expect(readdirSync(ws)).toContain('a.txt');
  }, 180_000);
});
