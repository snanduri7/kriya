/** The persistent read transport (kriya_service.ts) against a scriptable stand-in child: protocol, correlation, allowlist, and every way the child
 * can misbehave. Each failure is typed HOST_ERROR for the request(s) in flight, the next request restarts the child, and nothing is retried or
 * answered from memory. The real child is exercised in real_kriya_provider.test.ts. */
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';
import { IPC_CHANNELS, validateKupRequest, type KupRequest } from '../src/main/ipc_contract';
import { KriyaService, KriyaServiceHolder, SERVICE_ARGUMENTS, SERVICE_ARGV, toServiceRequest, type ServiceOptions } from '../src/main/kriya_service';

const SCRIPT = join(__dirname, 'fixtures', 'fake_service.mjs');
const SID = '20261004T093000000000Z-f1c70001';
const live: KriyaService[] = [];
const make = (behaviour = '', extra: Partial<ServiceOptions> = {}) => {
  const service = new KriyaService({ executable: process.execPath, argv: [SCRIPT], env: { FAKE_SERVICE: behaviour }, ...extra });
  live.push(service);
  return service;
};
afterEach(async () => { await Promise.all(live.splice(0).map((s) => s.stop())); });
const caps: KupRequest = { operation: 'capabilities' };
const describe_: KupRequest = { operation: 'workspace.describe', workspace: '/w' };
const alive = (pid: number | null) => { if (pid === null) return false; try { process.kill(pid, 0); return true; } catch { return false; } };

describe('request/response', () => {
  it('answers with the child\'s envelope, one child for many requests, the arguments exactly as typed', async () => {
    const s = make();
    const a = await s.request(caps);
    const b = await s.request({ operation: 'history.list', snapshot_id: SID, limit: 5, workspace: '/w' });
    expect(a.kind === 'json' && (a.json as { operation: string }).operation).toBe('capabilities');
    expect(b.kind === 'json' && (b.json as { echoed: unknown }).echoed).toMatchObject({ schema_version: 1, capability_set: '1.1', operation: 'history.list', arguments: { snapshot_id: SID, limit: 5, workspace: '/w' } });
    expect(s.stats).toMatchObject({ starts: 1, deaths: 0, requests: 2 });
    expect(a.kind === 'json' && (a.json as { pid: number }).pid).toBe(b.kind === 'json' && (b.json as { pid: number }).pid);
  });
  it('pipelined requests each resolve to their own answer', async () => {
    const s = make();
    const outcomes = await Promise.all(Array.from({ length: 12 }, (_, i) => s.request({ operation: 'history.detail', snapshot_id: SID, run_id: `r${i}` })));
    expect(outcomes.map((o) => o.kind === 'json' && (o.json as { echoed: { arguments: { run_id: string } } }).echoed.arguments.run_id)).toEqual(Array.from({ length: 12 }, (_, i) => `r${i}`));
    expect(s.stats.starts).toBe(1);
  });
  it('the child gets the fixed PATH plus only the environment the caller passed, in the caller\'s working directory', async () => {
    const s = make('', { cwd: '/tmp', env: { FAKE_SERVICE: '', HOME: '/h', KRIYA_STATE_DIR: '/s' } });
    const o = await s.request(caps);
    const seen = o.kind === 'json' ? (o.json as { env: Record<string, string>; cwd: string }) : null;
    expect(seen?.cwd).toMatch(/tmp$/);
    expect(seen?.env.HOME).toBe('/h');
    expect(seen?.env.PATH).toBe('/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin');
    expect(seen?.env.KRIYA_STATE_DIR).toBe('/s');
    expect(seen?.env.USER ?? seen?.env.LOGNAME ?? null).toBeNull(); // nothing leaks from the host's own environment
  });
  it('a typed refusal from the child is a typed error for that request alone', async () => {
    const s = make('refuse-on=workspace.describe');
    const refused = await s.request(describe_);
    expect(refused).toMatchObject({ kind: 'error', code: 'INVALID_REQUEST', message: 'refused by the stand-in' });
    expect((await s.request(caps)).kind).toBe('json');
    expect(s.stats.starts).toBe(1);
  });
});

describe('allowlist (nothing but typed KUP requests reaches the child)', () => {
  it('toServiceRequest builds the wire request only for the table and refuses any other operation or argument', () => {
    expect(toServiceRequest('q1', { operation: 'snapshot.verify', snapshot_id: SID })).toEqual({ id: 'q1', schema_version: 1, capability_set: '1.1', operation: 'snapshot.verify', arguments: { snapshot_id: SID } });
    expect(() => toServiceRequest('q1', { operation: 'recover' } as unknown as KupRequest)).toThrow(/outside the service allowlist/);
    expect(() => toServiceRequest('q1', { operation: 'capabilities', cwd: '/' } as unknown as KupRequest)).toThrow(/does not take: cwd/);
    expect(() => toServiceRequest('q1', { operation: 'history.list', snapshot_id: SID, shell: 'x' } as unknown as KupRequest)).toThrow(/does not take: shell/);
  });
  it('the table covers exactly the operations the host validator accepts', () => {
    const accepted = ['capabilities', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify', 'history.list', 'history.detail', 'history.prompt', 'workspace.status', 'workspace.describe', 'execution.preflight', 'execution.context', 'execution.model_probe', 'launch.status', 'evidence.manifest', 'evidence.verify', 'evidence.records', 'evidence.change_content', 'evidence.call_content'];
    // workspace.create is in the SERVICE table (the host's own token-gated New Project flow sends it) but is NEVER accepted from the renderer's query channel.
    expect(Object.keys(SERVICE_ARGUMENTS).sort()).toEqual([...accepted, 'workspace.create'].sort());
    expect(validateKupRequest({ operation: 'workspace.create', parent: '/tmp', name: 'x' }).ok).toBe(false);
    for (const op of ['recover', 'generate', 'fix', 'evidence.blob', 'history.delete', 'shell', '']) expect(validateKupRequest({ operation: op }).ok, op).toBe(false);
    expect(SERVICE_ARGV).toEqual(['kup-stdio']);
    expect(IPC_CHANNELS.query).toBe('kriya:kup:query');
  });
  it('an operation outside the table never reaches the child, and does not start one', async () => {
    const s = make();
    const o = await s.request({ operation: 'recover' } as unknown as KupRequest);
    expect(o).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(s.stats.starts).toBe(0);
  });
  it('an over-long request is refused locally', async () => {
    const o = await make().request({ operation: 'evidence.records', run_id: 'r', unit: 'x'.repeat(70_000) });
    expect(o).toMatchObject({ kind: 'error', code: 'INVALID_REQUEST' });
  });
});

describe('failure is typed, in-flight work fails, and the next request restarts the child', () => {
  it('unexpected death while a request is in flight: HOST_ERROR naming the exit, then a controlled restart answers the next request', async () => {
    const s = make('die-on=workspace.describe');
    const first = (await s.request(caps)) as { json: { pid: number } };
    const dying = await s.request(describe_);
    expect(dying).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(dying.kind === 'error' && dying.message).toMatch(/exited \(code 3\) unexpectedly/);
    expect(s.alive).toBe(false);
    const second = (await s.request(caps)) as { json: { pid: number } };
    expect(second.json.pid).not.toBe(first.json.pid);
    expect(s.stats).toMatchObject({ starts: 2, deaths: 1 });
  });
  it('a death with several requests in flight fails ALL of them and none of them is retried or answered twice', async () => {
    const s = make('die-on=history.prompt');
    await s.request(caps);
    const [a, b, c] = await Promise.all([s.request({ operation: 'history.detail', snapshot_id: SID, run_id: 'a' }), s.request({ operation: 'history.prompt', snapshot_id: SID, run_id: 'b' }), s.request({ operation: 'history.detail', snapshot_id: SID, run_id: 'c' })]);
    expect(a.kind).toBe('json'); // answered before the child died, in order
    expect(b).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(c).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(s.stats.requests).toBe(4);
  });
  it('a child that dies after answering is replaced without losing the next request', async () => {
    const s = make('die-after=1');
    expect((await s.request(caps)).kind).toBe('json');
    await new Promise((r) => setTimeout(r, 150));
    expect(s.alive).toBe(false);
    expect((await s.request(caps)).kind).toBe('json');
    expect(s.stats.starts).toBe(2);
  });
  it('a child that cannot start is a typed startup failure for the request that needed it, and the next request tries again', async () => {
    const s = new KriyaService({ executable: '/definitely/not/a/kriya', argv: SERVICE_ARGV });
    live.push(s);
    const first = await s.request(caps);
    expect(first).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(first.kind === 'error' && first.message).toMatch(/spawn failed/);
    expect((await s.request(caps)).kind).toBe('error');
    expect(s.stats.starts).toBe(2);
  });
  it('a child that exits before it is ready reports it with its stderr', async () => {
    const s = new KriyaService({ executable: process.execPath, argv: ['-e', 'console.error("boom: bad config"); process.exit(9)'] });
    live.push(s);
    const o = await s.request(caps);
    expect(o.kind === 'error' && o.message).toMatch(/exited \(code 9\) unexpectedly before it was ready/);
    expect(o.kind === 'error' && o.stderrTail).toMatch(/boom: bad config/);
  });
  it('a child that never announces itself is ended after the ready timeout', async () => {
    const s = make('ready=skip', { limits: { readyTimeoutMs: 300 } });
    const o = await s.request(caps);
    expect(o.kind === 'error' && o.message).toMatch(/did not become ready within 300 ms/);
    await new Promise((r) => setTimeout(r, 200));
    expect(s.alive).toBe(false);
  });
  it('a first line that is not the ready event is a protocol violation, not a service', async () => {
    for (const behaviour of ['ready=garbage']) {
      const o = await make(behaviour).request(caps);
      expect(o).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    }
  });
  it('a request that is not answered in time fails, the hung child is killed, and the next request gets a fresh one', async () => {
    const s = make('hang-on=workspace.describe', { limits: { requestTimeoutMs: 300 } });
    const warm = (await s.request(caps)) as { json: { pid: number } };
    const hung = await s.request(describe_);
    expect(hung.kind === 'error' && hung.message).toMatch(/did not answer workspace\.describe within 300 ms; the service was restarted/);
    await new Promise((r) => setTimeout(r, 200));
    expect(alive(warm.json.pid)).toBe(false);
    const fresh = (await s.request(caps)) as { json: { pid: number } };
    expect(fresh.json.pid).not.toBe(warm.json.pid);
  });
  it('an answer for a request that is not in flight breaks the protocol: everything in flight fails and the child is replaced', async () => {
    const s = make('wrong-id');
    const o = await s.request(caps);
    expect(o.kind === 'error' && o.message).toMatch(/not in flight/);
    await new Promise((r) => setTimeout(r, 150));
    expect(s.alive).toBe(false);
  });
  it('a non-JSON protocol line, an oversized line and an answer with neither envelope nor error are each violations', async () => {
    for (const [behaviour, pattern] of [['garbage-on=capabilities', /not a JSON object/], ['big-on=capabilities', /over 1000 bytes/], ['noenvelope-on=capabilities', /neither an envelope nor a typed error/]] as const) {
      const s = make(behaviour, { limits: { lineMaxBytes: 1000 } });
      const o = await s.request(caps);
      expect(o.kind === 'error' && o.message, behaviour).toMatch(pattern);
      await new Promise((r) => setTimeout(r, 150));
      expect(s.alive, behaviour).toBe(false);
    }
  });
});

describe('lifecycle', () => {
  it('stop() ends the child cleanly (EOF), no process is left behind, and nothing is accepted afterwards', async () => {
    const s = make();
    const o = (await s.request(caps)) as { json: { pid: number } };
    expect(alive(o.json.pid)).toBe(true);
    await s.stop();
    expect(alive(o.json.pid)).toBe(false);
    expect(s.alive).toBe(false);
    expect(await s.request(caps)).toMatchObject({ kind: 'error', code: 'HOST_ERROR' });
    expect(s.stats.starts).toBe(1);
  });
  it('stop() before any request is a no-op', async () => { await make().stop(); });
  it('the holder keeps one service per identity and replaces the child when the working directory, executable or environment changes', async () => {
    const holder = new KriyaServiceHolder();
    const base: ServiceOptions = { executable: process.execPath, argv: [SCRIPT], cwd: '/tmp', env: { FAKE_SERVICE: '' } };
    const a = holder.get(base);
    expect(holder.get({ ...base })).toBe(a);
    const pidA = ((await a.request(caps)) as { json: { pid: number } }).json.pid;
    const b = holder.get({ ...base, cwd: '/' });
    expect(b).not.toBe(a);
    const pidB = ((await b.request(caps)) as { json: { pid: number } }).json.pid;
    expect(pidB).not.toBe(pidA);
    await new Promise((r) => setTimeout(r, 200));
    expect(alive(pidA)).toBe(false); // the replaced child was stopped, not leaked
    expect(holder.get({ ...base, env: { FAKE_SERVICE: '', KRIYA_STATE_DIR: '/s' } })).not.toBe(b);
    await holder.stop();
    expect(alive(pidB)).toBe(false);
  });
  it('the workspace is a per-request argument: switching A -> B -> A reaches the same child and carries no state from one to the other', async () => {
    const s = make();
    const answers = [] as Array<{ pid: number; echoed: { arguments: { workspace: string } } }>;
    for (const w of ['/a', '/b', '/a']) answers.push(((await s.request({ operation: 'workspace.describe', workspace: w })) as { json: (typeof answers)[number] }).json);
    expect(new Set(answers.map((x) => x.pid)).size).toBe(1);
    expect(answers.map((x) => x.echoed.arguments.workspace)).toEqual(['/a', '/b', '/a']);
    expect(s.stats.starts).toBe(1);
  });
});
