/** New Project, host side: the renderer names a TOKEN and a NAME, never a path; Kriya creates; the registry approves only what Kriya reports created. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { IPC_CHANNELS, validateKupRequest } from '../src/main/ipc_contract';
import { PROJECT_TOKEN_RE, ProjectHost, TOKEN_MAX_LIVE, TOKEN_TTL_MS } from '../src/main/project_host';
import { WorkspaceRegistry } from '../src/main/workspace_host';

function make(over: Partial<{ create: (parent: string, name: string) => Promise<unknown>; runActive: () => boolean }> = {}) {
  let clock = 1_000;
  let n = 0;
  const approved: string[] = [];
  const calls: Array<{ parent: string; name: string }> = [];
  const host = new ProjectHost({
    create: over.create ?? (async (parent, name) => { calls.push({ parent, name }); return { error: null, data: { workspace: `${parent}/${name}` } }; }),
    approveCreated: (p) => { approved.push(p); },
    runActive: over.runActive ?? (() => false), now: () => clock, mintToken: () => `pt-${String(++n).padStart(32, '0')}`,
  });
  return { host, approved, calls, tick: (ms: number) => { clock += ms; } };
}
const choose = (host: ProjectHost, path: string | null = '/Users/p/work') => host.chooseParent(async () => path);

describe('choosing the parent', () => {
  it('returns an opaque token plus the path for display; cancel and invalid paths change nothing', async () => {
    const { host } = make();
    const ok = await choose(host);
    expect(ok).toMatchObject({ chosen: true, parent: '/Users/p/work', refused: null });
    expect(ok.token).toMatch(PROJECT_TOKEN_RE);
    expect(await choose(host, null)).toEqual({ chosen: false, token: null, parent: null, refused: null });
    for (const bad of ['relative', '', '/a\nb', 'x'.repeat(5000)]) expect((await choose(host, bad)).chosen, bad).toBe(false);
  });
  it('is refused without showing the chooser while a run owns the workspace', async () => {
    let shown = false;
    const { host } = make({ runActive: () => true });
    expect(await host.chooseParent(async () => { shown = true; return '/x'; })).toMatchObject({ chosen: false, refused: 'RUN_ACTIVE' });
    expect(shown).toBe(false);
  });
});

describe('creating', () => {
  it('asks Kriya with the PARENT the token stands for and the name, approves the path Kriya reports, and consumes the token', async () => {
    const { host, approved, calls } = make();
    const { token } = await choose(host);
    const r = await host.create({ token, name: 'my-app' });
    expect(r).toEqual({ created: true, path: '/Users/p/work/my-app', code: null, message: null, reason: null });
    expect(calls).toEqual([{ parent: '/Users/p/work', name: 'my-app' }]);
    expect(approved).toEqual(['/Users/p/work/my-app']);
    expect(await host.create({ token, name: 'again' })).toMatchObject({ created: false, code: 'PARENT_EXPIRED' }); // single use
  });
  it('NEVER approves a path when Kriya refuses, and keeps the token so the person can try another name', async () => {
    const { host, approved } = make({ create: async () => ({ data: null, error: { code: 'WORKSPACE_UNAVAILABLE', message: 'x already exists', reason: 'already_exists' } }) });
    const { token } = await choose(host);
    expect(await host.create({ token, name: 'x' })).toEqual({ created: false, path: null, code: 'WORKSPACE_UNAVAILABLE', message: 'x already exists', reason: 'already_exists' });
    expect(approved).toEqual([]);
    expect(await host.create({ token, name: 'y' })).toMatchObject({ code: 'WORKSPACE_UNAVAILABLE' }); // token still live: Kriya was asked again
  });
  it.each([
    ['not an envelope', 'banana'], ['no error key', { data: { workspace: '/a/b' } }], ['success without a workspace', { error: null, data: {} }],
    ['relative workspace', { error: null, data: { workspace: 'rel' } }], ['non-string workspace', { error: null, data: { workspace: 7 } }], ['null data', { error: null, data: null }],
  ])('a malformed Kriya answer (%s) is INVALID_RESPONSE and approves nothing', async (_n, answer) => {
    const { host, approved } = make({ create: async () => answer });
    const { token } = await choose(host);
    expect(await host.create({ token, name: 'x' })).toMatchObject({ created: false, code: 'INVALID_RESPONSE' });
    expect(approved).toEqual([]);
  });
  it('a thrown transport failure is a typed HOST_ERROR', async () => {
    const { host } = make({ create: async () => { throw new Error('service died'); } });
    const { token } = await choose(host);
    expect(await host.create({ token, name: 'x' })).toMatchObject({ created: false, code: 'HOST_ERROR', message: expect.stringContaining('service died') });
  });
  it('accepts exactly {token, name}: extra keys, a path, a non-string name and a forged token are refused before Kriya is asked', async () => {
    const { host, calls } = make();
    const { token } = await choose(host);
    for (const raw of [null, 'x', [], {}, { token }, { name: 'x' }, { token, name: 'x', parent: '/etc' }, { token, name: 7 }, { token, name: '' }, { token, name: 'a'.repeat(300) }, { token, name: 'a\0b' },
      { token: '/etc', name: 'x' }, { token: 'pt-' + 'g'.repeat(32), name: 'x' }, { token: 'pt-' + '0'.repeat(31), name: 'x' }]) {
      expect((await host.create(raw)).created, JSON.stringify(raw)).toBe(false);
    }
    expect(calls).toEqual([]);
  });
  it('a path-looking name is passed to Kriya verbatim (Kriya refuses it); the host never builds or joins a path', async () => {
    const asked: Array<[string, string]> = [];
    const { host } = make({ create: async (p, n) => { asked.push([p, n]); return { data: null, error: { code: 'INVALID_REQUEST', message: 'the project name is not valid', reason: 'invalid_name' } }; } });
    const { token } = await choose(host);
    for (const name of ['../escape', '/abs', 'a/b', '.hidden']) expect(await host.create({ token, name })).toMatchObject({ created: false, reason: 'invalid_name' });
    expect(asked).toEqual([['/Users/p/work', '../escape'], ['/Users/p/work', '/abs'], ['/Users/p/work', 'a/b'], ['/Users/p/work', '.hidden']]); // parent and name stay separate arguments
    const source = readFileSync(join(__dirname, '..', 'src', 'main', 'project_host.ts'), 'utf8').replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
    expect(source).not.toMatch(/path\.join|from 'node:|require\(|child_process|mkdir|\bfs\b/);
  });
  it('a token expires after the TTL and at most TOKEN_MAX_LIVE parents are remembered', async () => {
    const { host, tick } = make();
    const first = await choose(host);
    tick(TOKEN_TTL_MS + 1);
    expect(await host.create({ token: first.token, name: 'x' })).toMatchObject({ code: 'PARENT_EXPIRED' });
    const tokens = [];
    for (let i = 0; i < TOKEN_MAX_LIVE + 2; i += 1) tokens.push((await choose(host, `/p/${i}`)).token);
    expect(await host.create({ token: tokens[0], name: 'x' })).toMatchObject({ code: 'PARENT_EXPIRED' });
    expect(await host.create({ token: tokens[tokens.length - 1], name: 'x' })).toMatchObject({ created: true });
  });
  it('two concurrent creates with one token reach Kriya once; the second is BUSY', async () => {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    let asked = 0;
    const { host } = make({ create: async (p, n) => { asked += 1; await gate; return { error: null, data: { workspace: `${p}/${n}` } }; } });
    const { token } = await choose(host);
    const a = host.create({ token, name: 'x' });
    const b = host.create({ token, name: 'x' });
    release();
    const [ra, rb] = await Promise.all([a, b]);
    expect(asked).toBe(1);
    expect([ra.created, rb.created].sort()).toEqual([false, true]);
    expect([ra, rb].find((r) => !r.created)).toMatchObject({ code: 'BUSY' });
  });
  it('a run that starts after the folder was chosen blocks creation', async () => {
    let active = false;
    const { host, calls } = make({ runActive: () => active });
    const { token } = await choose(host);
    active = true;
    expect(await host.create({ token, name: 'x' })).toMatchObject({ created: false, code: 'RUN_ACTIVE' });
    expect(calls).toEqual([]);
  });
});

describe('authority wiring', () => {
  it('only a folder Kriya reported created becomes an approved workspace (registry.approveCreated)', () => {
    const store = { getRecent: () => [] as string[], setRecent: () => undefined, getCurrent: () => null, setCurrent: () => undefined };
    const registry = new WorkspaceRegistry(store);
    expect(registry.isApproved('/Users/p/work/new')).toBe(false);
    expect(registry.refuse({ operation: 'workspace.describe', workspace: '/Users/p/work/new' })).toMatch(/not chosen/);
    expect(registry.approveCreated('relative')).toBe(false);
    expect(registry.approveCreated('/Users/p/work/new')).toBe(true);
    expect(registry.refuse({ operation: 'workspace.describe', workspace: '/Users/p/work/new' })).toBeNull();
    expect(registry.isApproved('/Users/p/work/other')).toBe(false);
  });
  it('workspace.create is never accepted from the renderer; the context and probe reads take exactly a workspace', () => {
    expect(validateKupRequest({ operation: 'workspace.create', parent: '/tmp', name: 'x' }).ok).toBe(false);
    expect(validateKupRequest({ operation: 'workspace.create' }).ok).toBe(false);
    for (const operation of ['execution.context', 'execution.model_probe']) {
      expect(validateKupRequest({ operation, workspace: '/Users/p/work' }).ok, operation).toBe(true);
      expect(validateKupRequest({ operation, workspace: '/Users/p/work', extra: 1 }).ok, operation).toBe(false);
      expect(validateKupRequest({ operation, workspace: 'relative' }).ok, operation).toBe(false);
      expect(validateKupRequest({ operation }).ok, operation).toBe(false);
    }
  });
  it('main.ts checks the sender before each project channel and hands the project host nothing but the raw request', () => {
    const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
    for (const [channel, call] of [['chooseProjectParent', 'projects.chooseParent('], ['createProject', 'projects.create(raw)']] as const) {
      const at = main.indexOf(`IPC_CHANNELS.${channel}`);
      expect(at).toBeGreaterThan(0);
      const body = main.slice(at, at + 900);
      expect(body.indexOf('fromOwnedRenderer(event)')).toBeGreaterThan(-1);
      expect(body.indexOf('fromOwnedRenderer(event)')).toBeLessThan(body.indexOf(call));
    }
    expect(IPC_CHANNELS.createProject).toBe('kriya:host:project:create');
  });
});

describe('Reveal in Finder (host)', () => {
  const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
  it('reveals only the folder the HOST has open: the handler takes no argument, checks the sender, the folder, and never reveals in an evidence build', () => {
    const at = main.indexOf('IPC_CHANNELS.revealWorkspace');
    const body = main.slice(at, at + 700);
    expect(body).toContain('(event) =>'); // no renderer-supplied argument
    expect(body.indexOf('fromOwnedRenderer(event)')).toBeLessThan(body.indexOf('workspaces.current()'));
    expect(body.indexOf('isDirectory(current)')).toBeLessThan(body.indexOf('shell.showItemInFolder(current)'));
    expect(body.indexOf('if (dev)')).toBeLessThan(body.indexOf('shell.showItemInFolder(current)'));
    expect((main.match(/shell\./g) ?? []).length).toBe(1); // the one use of Electron's shell module
    expect(IPC_CHANNELS.revealWorkspace).toBe('kriya:host:workspace:reveal');
  });
});
