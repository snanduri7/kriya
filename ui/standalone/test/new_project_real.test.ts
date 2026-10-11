/** New Project against the REAL `kriya kup-stdio`: the host's ProjectHost asks Kriya (`workspace.create`), Kriya makes the one empty folder, and the ordinary reads
 * (workspace.describe, execution.preflight/context/model_probe) work on it. Never calls a model. Needs the repository's .venv. */
import { execFileSync } from 'node:child_process';
import { existsSync, lstatSync, mkdirSync, mkdtempSync, readdirSync, realpathSync, rmSync, symlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { ProjectHost } from '../src/main/project_host';
import { KriyaService } from '../src/main/kriya_service';
import { WorkspaceRegistry } from '../src/main/workspace_host';

const ROOT = join(__dirname, '..', '..', '..');
const KRIYA = join(ROOT, '.venv', 'bin', 'kriya');
const available = existsSync(KRIYA);

describe.skipIf(!available)('New Project, real Kriya', () => {
  let base = ''; let parent = ''; let service: KriyaService; let host: ProjectHost; let registry: WorkspaceRegistry;
  let n = 0;
  const picked: string[] = [];

  beforeAll(() => {
    base = realpathSync(mkdtempSync(join(tmpdir(), 'kriya-newproj-')));
    parent = join(base, 'projects');
    mkdirSync(parent);
    const config = join(base, 'config'); const state = join(base, 'state'); const home = join(base, 'home');
    for (const d of [config, state, home]) mkdirSync(d);
    service = new KriyaService({ executable: KRIYA, cwd: config, env: { HOME: home, KRIYA_STATE_DIR: state, PYTHONDONTWRITEBYTECODE: '1' } });
    const store = { getRecent: () => [] as string[], setRecent: () => undefined, getCurrent: () => null, setCurrent: () => undefined };
    registry = new WorkspaceRegistry(store);
    host = new ProjectHost({
      create: async (p, name) => { const out = await service.request({ operation: 'workspace.create', parent: p, name }); if (out.kind !== 'json') throw new Error(out.message); return out.json; },
      approveCreated: (path) => { picked.push(path); registry.approveCreated(path); },
      runActive: () => false, now: () => Date.now(), mintToken: () => `pt-${String(++n).padStart(32, '0')}`,
    });
  }, 60_000);
  afterAll(async () => { await service?.stop(); if (base) rmSync(base, { recursive: true, force: true }); });

  const choose = async (path = parent) => (await host.chooseParent(async () => path)).token as string;
  const query = async (request: Parameters<KriyaService['request']>[0]) => { const out = await service.request(request); if (out.kind !== 'json') throw new Error(out.message); return out.json as { error: { code: string; reason?: string } | null; data: Record<string, any> | null }; };

  it('creates exactly one empty folder, approves it, and the ordinary reads work on it', async () => {
    const r = await host.create({ token: await choose(), name: 'my app' });
    expect(r).toMatchObject({ created: true, path: join(parent, 'my app') });
    expect(readdirSync(join(parent, 'my app'))).toEqual([]); // no .git, no kriya.yaml, no scaffold
    expect(readdirSync(parent)).toEqual(['my app']);
    expect(registry.refuse({ operation: 'workspace.describe', workspace: r.path! })).toBeNull(); // approved because Kriya created it
    const described = await query({ operation: 'workspace.describe', workspace: r.path! });
    expect(described.error).toBeNull();
    expect(described.data!.checkout.state).toBe('not_a_repository');
    expect(described.data!.name).toBe('my app');
    const pre = await query({ operation: 'execution.preflight', workspace: r.path! });
    expect(pre.error).toBeNull();
    expect(pre.data!.blockers.filter((b: { code: string }) => b.code !== 'TOOL_UNAVAILABLE' && b.code !== 'STATE_DIRECTORY_NOT_OBSERVABLE')).toEqual([]); // nothing about the folder itself blocks a run
    const ctx = await query({ operation: 'execution.context', workspace: r.path! });
    expect(ctx.error).toBeNull();
    expect(ctx.data!.available).toBe(true);
    expect(ctx.data!.routing.map((x: { role: string }) => x.role)).toEqual(['planner', 'architect', 'developer', 'reviewer', 'run_verifier', 'skill_gap', 'spec_compliance']);
    expect(ctx.data!.skills.selection).toBe('automatic');
    expect(ctx.data!.tools.health).toBe('not_checked');
    const probe = await query({ operation: 'execution.model_probe', workspace: r.path! });
    expect(probe.error).toBeNull();
    expect(['reachable', 'unreachable', 'error', 'unreadable', 'not_probed']).toContain(probe.data!.endpoints[0].state);
    expect(JSON.stringify(probe.data) + JSON.stringify(ctx.data)).not.toMatch(/local-key/); // the default API key is never returned
  }, 90_000);

  it('an existing folder or file of that name is refused and left untouched', async () => {
    mkdirSync(join(parent, 'taken')); writeFileSync(join(parent, 'taken', 'user.txt'), 'precious');
    writeFileSync(join(parent, 'afile'), 'x');
    for (const name of ['taken', 'afile']) {
      const r = await host.create({ token: await choose(), name });
      expect(r, name).toMatchObject({ created: false, code: 'WORKSPACE_UNAVAILABLE', reason: 'already_exists' });
    }
    expect(readdirSync(join(parent, 'taken'))).toEqual(['user.txt']);
    expect(registry.isApproved(join(parent, 'taken'))).toBe(false);
  });

  it.each(['../escape', 'a/b', '.hidden', 'a:b', ' lead', 'trail ', 'a'.repeat(101)])('the name %j is refused by Kriya and nothing is created', async (name) => {
    const before = readdirSync(parent).sort();
    const r = await host.create({ token: await choose(), name });
    expect(r).toMatchObject({ created: false, code: 'INVALID_REQUEST', reason: 'invalid_name' });
    expect(readdirSync(parent).sort()).toEqual(before);
    expect(existsSync(join(base, 'escape'))).toBe(false);
  });

  it('Unicode names work and a symlinked parent is followed to the real folder', async () => {
    const r1 = await host.create({ token: await choose(), name: 'プロジェクト' });
    expect(r1).toMatchObject({ created: true });
    expect(lstatSync(r1.path!).isDirectory()).toBe(true);
    const link = join(base, 'link'); symlinkSync(parent, link);
    const r2 = await host.create({ token: await choose(link), name: 'via-link' });
    expect(r2).toMatchObject({ created: true, path: join(parent, 'via-link') }); // the REAL path is what is reported and approved
  });

  it('a parent that does not exist or is not writable is a typed refusal', async () => {
    expect(await host.create({ token: await choose(join(base, 'nope')), name: 'x' })).toMatchObject({ created: false, reason: 'parent_not_found' });
    const ro = join(base, 'readonly'); mkdirSync(ro); execFileSync('chmod', ['555', ro]);
    try {
      const r = await host.create({ token: await choose(ro), name: 'x' });
      if (process.getuid && process.getuid() !== 0) expect(r).toMatchObject({ created: false, reason: 'permission_denied' });
      expect(readdirSync(ro)).toEqual([]);
    } finally { execFileSync('chmod', ['755', ro]); }
  });

  it('the renderer\'s generic query path cannot create (workspace.create is not a valid renderer request)', async () => {
    const { validateKupRequest } = await import('../src/main/ipc_contract');
    expect(validateKupRequest({ operation: 'workspace.create', parent, name: 'viaquery' }).ok).toBe(false);
    expect(existsSync(join(parent, 'viaquery'))).toBe(false);
  });

  it('everything the host approved was reported created by Kriya (nothing else became a workspace)', () => {
    expect(picked.every((p) => p.startsWith(parent + '/'))).toBe(true);
    expect(picked.length).toBeGreaterThanOrEqual(3);
  });
});
