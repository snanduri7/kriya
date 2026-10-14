/**
 * Project Setup against the REAL Kriya: the real persistent `kriya kup-stdio`, the host's ProjectSetupHost and ConfigApprovalHost in front of it. HOME and every store are
 * temporary directories (nothing touches ~/.kriya). No model is called.
 *
 * What this proves that the unit tests cannot: the journey the owner asked for end to end - an EMPTY folder is runnable (no blocker), Project Setup creates a schema-valid
 * kriya.yaml, that file is NOT approved (readiness now says approval is required), the GUI's own approval path makes it ready, a second setup never overwrites, and the
 * created file is what Kriya's own authority path then reads (`kriya authority inspect`).
 */
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ConfigApprovalHost } from '../src/main/config_approval_host';
import { ProjectSetupHost } from '../src/main/project_setup_host';
import { KriyaService } from '../src/main/kriya_service';
import type { KupRequest } from '../src/main/ipc_contract';

const ROOT = join(__dirname, '..', '..', '..');
const PYTHON = join(ROOT, '.venv', 'bin', 'python');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const available = existsSync(PYTHON) && existsSync(KRIYA);
type Json = Record<string, any>; // eslint-disable-line @typescript-eslint/no-explicit-any
const FIXED_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';

describe.skipIf(!available)('Project Setup against real Kriya', () => {
  let base = ''; let home = ''; let config = '';
  let env: Record<string, string> = {};
  let service: KriyaService;
  let open: string | null = null;
  let setup: ProjectSetupHost;
  let approvals: ConfigApprovalHost;

  const send = async (request: KupRequest): Promise<Json> => {
    const out = await service.request(request);
    if (out.kind !== 'json') throw new Error(`service failed: ${out.code} ${out.message}`);
    return out.json as Json;
  };
  const folder = (name: string): string => { const dir = join(base, 'ws', name); mkdirSync(dir, { recursive: true }); return realpathSync(dir); };
  const authorityFiles = () => (existsSync(join(home, '.kriya', 'authority')) ? readdirSync(join(home, '.kriya', 'authority')).filter((f) => f.endsWith('.json')) : []);
  const blockers = async (ws: string): Promise<string[]> => (((await send({ operation: 'execution.preflight', workspace: ws })).data as Json).blockers as Json[]).map((b) => b.code);
  const cli = (ws: string, ...argv: string[]) => spawnSync(KRIYA, ['authority', ...argv], { cwd: ws, env, encoding: 'utf8' });

  beforeAll(() => {
    base = mkdtempSync(join(tmpdir(), 'kriya-gui-setup-'));
    home = join(base, 'home'); config = join(base, 'config');
    for (const d of [home, config, join(base, 'state'), join(base, 'ws')]) mkdirSync(d);
    env = { HOME: home, KRIYA_STATE_DIR: join(base, 'state'), PYTHONDONTWRITEBYTECODE: '1', PATH: FIXED_PATH };
    service = new KriyaService({ executable: KRIYA, cwd: config, env });
    setup = new ProjectSetupHost({ initialize: (workspace) => send({ operation: 'project.initialize', workspace }), currentWorkspace: () => open, runActive: () => false });
    approvals = new ConfigApprovalHost({ approve: (workspace, fingerprint) => send({ operation: 'configuration.approve', workspace, fingerprint }), confirm: async () => true, currentWorkspace: () => open });
    execFileSync(PYTHON, ['-c', 'import kriya.cli'], { env: { PATH: process.env.PATH ?? '', ...env }, cwd: config });
  }, 120_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  it('the whole journey: empty folder is runnable -> Setup creates a schema-valid kriya.yaml -> it is NOT approved -> the GUI approval makes it ready -> a second setup never overwrites', async () => {
    const ws = folder('journey'); open = ws;
    // 1. a brand-new empty folder: ready on the packaged defaults, nothing to approve
    expect(await blockers(ws)).toEqual([]);
    expect(((await send({ operation: 'configuration.approval_status', workspace: ws })).data as Json)).toMatchObject({ status: 'not_required', protected_fields: [] });
    // 2. Project Setup
    const created = await setup.initialize({ workspace: ws });
    expect(created).toMatchObject({ initialized: true, outcome: 'created', code: null });
    expect(created.summary).toMatchObject({ model: 'qwen3-coder:30b', workflow: 'enforce', changes: 'held for your approval', web_lookup: 'off' });
    expect(readdirSync(ws)).toEqual(['kriya.yaml']);
    const yaml = readFileSync(join(ws, 'kriya.yaml'), 'utf8');
    expect(yaml).toMatch(/^# Kriya project configuration - created by Project Setup\./);
    expect(yaml).toMatch(/workflow_controller:\n {2}enabled: true\n {2}mode: enforce/);
    // 3. NOT approved: Kriya now says approval is required, and readiness carries the blocker; no authority artifact exists
    const status = (await send({ operation: 'configuration.approval_status', workspace: ws })).data as Json;
    expect(status).toMatchObject({ status: 'required', can_approve: true, reason: 'no_approval' });
    expect(status.protected_fields.map((f: Json) => f.field)).toEqual(expect.arrayContaining(['llm.base_url', 'llm_chain', 'workflow_controller.enabled', 'workflow_controller.mode']));
    expect(await blockers(ws)).toContain('CONFIG_AUTHORITY_REFUSED');
    expect(authorityFiles()).toEqual([]);
    expect(cli(ws, 'inspect').stdout).toContain('No local approval on file');
    // 4. the GUI's approval path (the same backend authority path as the CLI) bound to what was reviewed
    expect((await approvals.approve({ workspace: ws, fingerprint: status.fingerprint })).approved).toBe(true);
    expect(await blockers(ws)).not.toContain('CONFIG_AUTHORITY_REFUSED');
    expect(cli(ws, 'inspect').stdout).toContain('is CURRENT and covers all pending fields');
    // 5. a second setup refuses and the file is untouched
    const again = await setup.initialize({ workspace: ws });
    expect(again).toMatchObject({ initialized: false, code: 'INVALID_REQUEST', reason: 'already_configured' });
    expect(readFileSync(join(ws, 'kriya.yaml'), 'utf8')).toBe(yaml);
  }, 180_000);

  it('never overwrites an existing configuration or follows a symlink, and refuses the folder that is not the open one', async () => {
    const mine = folder('mine'); open = mine;
    writeFileSync(join(mine, 'kriya.yaml'), 'llm:\n  model: mine\n');
    expect(await setup.initialize({ workspace: mine })).toMatchObject({ initialized: false, reason: 'already_configured' });
    expect(readFileSync(join(mine, 'kriya.yaml'), 'utf8')).toBe('llm:\n  model: mine\n');
    const linked = folder('linked'); open = linked;
    symlinkSync(join(base, 'elsewhere.yaml'), join(linked, 'kriya.yaml'));
    expect(await setup.initialize({ workspace: linked })).toMatchObject({ initialized: false, reason: 'already_configured' });
    expect(existsSync(join(base, 'elsewhere.yaml'))).toBe(false);
    open = mine;
    expect(await setup.initialize({ workspace: linked })).toMatchObject({ initialized: false, code: 'WORKSPACE_NOT_OPEN' });
  }, 120_000);

  it('the query channel and one-shot grammar cannot create a configuration (the request is a mutation with its own typed channel)', async () => {
    const { validateKupRequest } = await import('../src/main/ipc_contract');
    expect(validateKupRequest({ operation: 'project.initialize', workspace: folder('q') }).ok).toBe(false);
  });
});
