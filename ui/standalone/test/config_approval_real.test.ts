/**
 * Configuration approval from the desktop against the REAL Kriya: the real persistent `kriya kup-stdio` process answering `configuration.approval_status` and
 * `configuration.approve`, the host's ConfigApprovalHost in front of it, the real `kriya authority inspect` as the independent witness, and the real
 * `kriya generate` launch path (only its workflow body, which would call a model, is the controlled stand-in) accepting - or refusing - the folder.
 *
 * What this proves that the unit tests cannot: the approval the application records is the approval the command line and a launched run honour; it is bound to the
 * configuration that was reviewed (a change in between is refused, nothing written); it lives in Kriya's store outside the folder, in the operator's HOME, and
 * survives a restart of the service; no configuration value crosses to the application. HOME and every store here are temporary directories: nothing touches ~/.kriya.
 * Needs the repository's .venv (skipped, loudly, when absent). No model is called.
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { chmodSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ConfigApprovalHost } from '../src/main/config_approval_host';
import { ExecutionHost } from '../src/main/execution_host';
import { launchExecution } from '../src/main/execution_process';
import { validateKupRequest, type KupRequest } from '../src/main/ipc_contract';
import { KriyaService } from '../src/main/kriya_service';

const ROOT = join(__dirname, '..', '..', '..');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const CONTROLLED = join(__dirname, 'fixtures', 'controlled_kriya.py');
const available = existsSync(PYTHON) && existsSync(KRIYA);
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any

const FIXED_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';
const gitCandidates = (path: string) => path.split(':').map((d) => `${d}/git`).filter((c) => existsSync(c)).map((c) => ({ path: c, works: spawnSync(c, ['--version'], { encoding: 'utf8' }).status === 0 }));
const SECRET = 'tok-9f8e7d6c5b4a-do-not-leak';
const PASSWORD = 'hunter2-pass';
const YAML = (url = 'http://operator:' + PASSWORD + '@127.0.0.1:11434/v1', extra = '') => `llm:\n  base_url: "${url}"\nmcp:\n  hostile:\n    command: /usr/local/bin/hostile-mcp\n    env:\n      API_TOKEN: "${SECRET}"\n${extra}`;
let counter = 0;
const mint = () => { counter += 1; return `20261014T1010${String(counter).padStart(2, '0')}-${(0xabcd1000 + counter).toString(16)}`; };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe.skipIf(!available)('configuration approval against real Kriya', () => {
  let base = ''; let state = ''; let config = ''; let home = '';
  let env: Record<string, string> = {};
  let service: KriyaService;
  let openWorkspace: string | null = null;
  let hostCalls = 0;
  let approvals: ConfigApprovalHost;

  const send = async (request: KupRequest): Promise<Json> => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(`service failed: ${out.code} ${out.message}`);
    return out.json as Json;
  };
  const status = async (ws: string): Promise<Json> => (await send({ operation: 'configuration.approval_status', workspace: ws })).data as Json;
  const workspace = (name: string, yaml: string | null = YAML()): string => {
    const dir = join(base, 'ws', name);
    mkdirSync(dir, { recursive: true });
    if (yaml !== null) writeFileSync(join(dir, 'kriya.yaml'), yaml);
    return realpathSync(dir);
  };
  const authorityDir = () => join(home, '.kriya', 'authority');
  const stored = () => (existsSync(authorityDir()) ? readdirSync(authorityDir()).filter((f) => f.endsWith('.json')) : []);
  /** The command line, run FROM the folder with the host's own environment: the independent witness. */
  const cli = (ws: string, ...argv: string[]) => spawnSync(KRIYA, ['authority', ...argv], { cwd: ws, env, encoding: 'utf8' });
  const restart = async () => {
    await service.stop();
    service = new KriyaService({ executable: KRIYA, cwd: config, env });
  };
  function executable(name: string, script: Json): string {
    const path = join(base, `kriya-${name}`);
    writeFileSync(path, `#!/bin/sh\nexport KRIYA_CONTROLLED_SCRIPT='${JSON.stringify(script)}'\nexec '${PYTHON}' '${CONTROLLED}' "$@"\n`);
    chmodSync(path, 0o755);
    return path;
  }
  const preflightBlockers = async (ws: string): Promise<string[]> => (((await send({ operation: 'execution.preflight', workspace: ws })).data as Json).blockers as Json[]).map((b) => b.code);

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), 'kriya-gui-cfgapprove-'));
    state = join(base, 'state'); config = join(base, 'config'); home = join(base, 'home');
    for (const d of [state, config, home, join(base, 'ws')]) mkdirSync(d);
    let childPath = FIXED_PATH;
    if (!(gitCandidates(FIXED_PATH)[0]?.works ?? false)) {
      const working = gitCandidates(process.env.PATH ?? '').concat(gitCandidates(FIXED_PATH)).find((c) => c.works);
      if (working) { mkdirSync(join(base, 'bin')); symlinkSync(working.path, join(base, 'bin', 'git')); childPath = `${join(base, 'bin')}:${FIXED_PATH}`; }
    }
    // exactly the host's child environment policy (child_env.ts): PATH, PYTHONDONTWRITEBYTECODE, HOME, KRIYA_STATE_DIR - no KRIYA_AUTHORITY_HOME, no KRIYA_TRUST_FILE
    env = { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1', PATH: childPath };
    service = new KriyaService({ executable: KRIYA, cwd: config, env });
    approvals = new ConfigApprovalHost({
      approve: (ws, fingerprint) => { hostCalls += 1; return send({ operation: 'configuration.approve', workspace: ws, fingerprint }); },
      currentWorkspace: () => openWorkspace,
    });
    execFileSync(PYTHON, ['-c', 'import kriya.cli'], { env: { PATH: process.env.PATH ?? '', ...env }, cwd: config });
  }, 120_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  it('an unapproved folder: the preflight carries the blocker and the status lists NAMES only - no value, no secret, no URL credential', async () => {
    const ws = workspace('names-only');
    expect(await preflightBlockers(ws)).toContain('CONFIG_AUTHORITY_REFUSED');
    const raw = await send({ operation: 'configuration.approval_status', workspace: ws });
    const data = raw.data as Json;
    expect(data).toMatchObject({ status: 'required', required: true, can_approve: true, reason: 'no_approval', values_shown: false });
    expect(data.protected_fields.map((f: Json) => f.field)).toEqual(['llm.base_url', 'mcp.hostile']);
    expect(data.fingerprint).toMatch(/^sha256:[0-9a-f]{64}$/);
    const text = JSON.stringify(raw);
    for (const leak of [SECRET, PASSWORD, 'operator:', 'hostile-mcp', '127.0.0.1']) expect(text, leak).not.toContain(leak);
    expect(stored()).toEqual([]); // reading approves nothing
  });

  it('GUI approval == CLI approval: recorded through the host, honoured by `kriya authority inspect`, by readiness, and by the launched run; stored outside the folder in the operator HOME', async () => {
    const ws = workspace('equivalence'); openWorkspace = ws;
    const before = await status(ws);
    const result = await approvals.approve({ workspace: ws, fingerprint: before.fingerprint });
    expect(result).toEqual({ approved: true, outcome: 'approved', code: null, message: null, reason: null });
    // 1. the independent witness: the command line says the approval is CURRENT and covers every pending field
    const inspected = cli(ws, 'inspect');
    expect(inspected.stdout + inspected.stderr).toContain('is CURRENT and covers all pending fields');
    // 2. readiness read from Kriya again: no blocker
    expect(await preflightBlockers(ws)).not.toContain('CONFIG_AUTHORITY_REFUSED');
    expect(((await status(ws)) as Json)).toMatchObject({ status: 'approved', reason: null, can_approve: false });
    // 3. the artifact lives in the operator's HOME, never in the folder, and holds no secret
    expect(stored()).toHaveLength(1);
    const artifactText = readFileSync(join(authorityDir(), stored()[0]!), 'utf8');
    expect(JSON.parse(artifactText).security_subset.records.map((r: Json) => r.field_path)).toEqual(['llm.base_url', 'mcp.hostile']);
    for (const leak of [SECRET, PASSWORD, 'hostile-mcp']) expect(artifactText, leak).not.toContain(leak);
    expect(readdirSync(ws)).toEqual(['kriya.yaml']); // nothing was written into the project
    // 4. the launched run (its own `load_config`, in its own process) honours it: a run starts and ends in SUCCESS with the controlled body
    const h = new ExecutionHost({ launch: launchExecution, prepare: () => ({ ok: true, executable: executable('approved', { outcome: 'success', step_ms: 50 }), env: { ...env } }),
      currentWorkspace: () => ws, mintLaunchId: mint, now: () => new Date(), preflight: (w) => send({ operation: 'execution.preflight', workspace: w }), graceMs: 4000 });
    const started = await h.start({ workspace: ws, goal: 'Add a clamp function with tests.' });
    expect(started.started, JSON.stringify(started.refusal)).toBe(true);
    const end = Date.now() + 90_000;
    while (h.state().phase !== 'exited') { if (Date.now() > end) throw new Error('run did not end'); await sleep(50); }
    const launched = (await send({ operation: 'launch.status', workspace: ws, launch_id: started.launch_id! })).data as Json;
    expect(launched.run_record).toMatchObject({ found: true, terminal_status: 'SUCCESS' });
  }, 180_000);

  it('the reverse direction: an approval made with `kriya authority approve` is seen as approved by the application', async () => {
    const ws = workspace('cli-first');
    expect((await status(ws)).status).toBe('required');
    const approved = cli(ws, 'approve', '--confirm');
    expect(approved.status, approved.stderr).toBe(0);
    expect((await status(ws)).status).toBe('approved');
    expect(await preflightBlockers(ws)).not.toContain('CONFIG_AUTHORITY_REFUSED');
  });

  it('TOCTOU: a configuration that changed between Review and Approve is refused as stale, NOTHING is written, and the updated one can then be approved', async () => {
    const ws = workspace('race'); openWorkspace = ws;
    const reviewed = (await status(ws)).fingerprint as string;
    const storedBefore = stored().length;
    writeFileSync(join(ws, 'kriya.yaml'), YAML('http://127.0.0.1:9/v1')); // B appears after A was reviewed
    const refused = await approvals.approve({ workspace: ws, fingerprint: reviewed });
    expect(refused).toMatchObject({ approved: false, outcome: null, code: 'APPROVAL_STALE', reason: 'configuration_changed' });
    expect(stored().length).toBe(storedBefore);
    expect((await status(ws)).status).toBe('required');
    expect(cli(ws, 'inspect').stdout).toContain('No local approval on file');
    const fresh = (await status(ws)).fingerprint as string;
    expect(fresh).not.toBe(reviewed);
    expect((await approvals.approve({ workspace: ws, fingerprint: fresh })).approved).toBe(true);
  });

  it('an approval does not outlive the configuration it covers: change one protected setting and the project needs approval again (host, CLI and readiness agree)', async () => {
    const ws = workspace('drift'); openWorkspace = ws;
    expect((await approvals.approve({ workspace: ws, fingerprint: (await status(ws)).fingerprint })).approved).toBe(true);
    expect((await status(ws)).status).toBe('approved');
    writeFileSync(join(ws, 'kriya.yaml'), YAML('http://127.0.0.1:7/v1'));
    expect(await status(ws)).toMatchObject({ status: 'required', reason: 'stale_approval' });
    expect(await preflightBlockers(ws)).toContain('CONFIG_AUTHORITY_REFUSED');
    expect(cli(ws, 'inspect').stdout).toContain('does NOT cover the current configuration');
  });

  it('durability across a restart of the service: the approval is still recognised; a review taken BEFORE the restart is refused as stale (the fingerprint is process-local)', async () => {
    const ws = workspace('restart'); openWorkspace = ws;
    const reviewedBefore = (await status(ws)).fingerprint as string;
    expect((await approvals.approve({ workspace: ws, fingerprint: reviewedBefore })).approved).toBe(true);
    await restart();
    expect((await status(ws)).status).toBe('approved'); // Kriya's store, not the window or the process, holds the approval
    const other = workspace('restart-review');
    openWorkspace = other;
    const reviewed = (await status(other)).fingerprint as string;
    await restart();
    expect(await approvals.approve({ workspace: other, fingerprint: reviewed })).toMatchObject({ approved: false, code: 'APPROVAL_STALE' });
    expect((await status(other)).status).toBe('required');
  }, 120_000);

  it('approving twice is idempotent: the second answer is already_approved and the stored approval is untouched', async () => {
    const ws = workspace('twice'); openWorkspace = ws;
    const fingerprint = (await status(ws)).fingerprint as string;
    expect((await approvals.approve({ workspace: ws, fingerprint })).outcome).toBe('approved');
    const files = stored().map((f) => [f, readFileSync(join(authorityDir(), f), 'utf8')]);
    expect(await approvals.approve({ workspace: ws, fingerprint })).toMatchObject({ approved: true, outcome: 'already_approved' });
    expect(stored().map((f) => [f, readFileSync(join(authorityDir(), f), 'utf8')])).toEqual(files);
  });

  it('a folder that needs no approval reports not_required and offers nothing to approve', async () => {
    const ws = workspace('plain', null);
    const data = await status(ws);
    expect(data).toMatchObject({ status: 'not_required', required: false, can_approve: false, protected_fields: [], fingerprint: null });
  });

  it('a malformed configuration is a typed error through the service, never a stack trace', async () => {
    const ws = workspace('broken', 'llm: [unclosed\n  - : :\n');
    const env2 = await send({ operation: 'configuration.approval_status', workspace: ws });
    expect(env2.data).toBeNull();
    expect(env2.error.code).toBe('CONFIG_LOAD_FAILED');
    expect(JSON.stringify(env2)).not.toContain('Traceback');
  });

  it('the host refuses what it must before Kriya is asked: another folder, a malformed fingerprint, extra keys; and the query channel cannot approve', async () => {
    const ws = workspace('guarded'); openWorkspace = ws;
    const other = workspace('not-open');
    const good = (await status(ws)).fingerprint as string;
    const calls = hostCalls;
    expect(await approvals.approve({ workspace: other, fingerprint: good })).toMatchObject({ approved: false, code: 'WORKSPACE_NOT_OPEN' });
    for (const bad of ['', 'sha256:xyz', good.toUpperCase(), good.slice(0, -1), 'all', '*']) expect(await approvals.approve({ workspace: ws, fingerprint: bad }), bad).toMatchObject({ approved: false, code: 'INVALID_REQUEST' });
    for (const extra of [{ fields: ['x'] }, { values: {} }, { path: '/' }, { digest: good }, { trust_file: '/x' }, { confirm: true }]) expect(await approvals.approve({ workspace: ws, fingerprint: good, ...extra })).toMatchObject({ approved: false, code: 'INVALID_REQUEST' });
    expect(await approvals.approve({ workspace: ws })).toMatchObject({ approved: false, code: 'INVALID_REQUEST' });
    expect(hostCalls).toBe(calls); // none of those reached Kriya
    expect((await status(ws)).status).toBe('required');
    expect(validateKupRequest({ operation: 'configuration.approve', workspace: ws, fingerprint: good }).ok).toBe(false);
  });

  it('the service itself refuses a request that carries anything but {workspace, fingerprint}', async () => {
    const ws = workspace('wire'); openWorkspace = ws;
    const out = await service.request({ operation: 'configuration.approve', workspace: ws, fingerprint: (await status(ws)).fingerprint, fields: ['x'] } as unknown as KupRequest);
    expect(out.kind).toBe('error');
    expect((await status(ws)).status).toBe('required');
  });
});
