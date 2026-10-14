/**
 * ProjectSetupHost (host side of Project Setup): a narrow, typed proxy. Controlled executors only - the real-Kriya behaviour is in project_setup_real.test.ts.
 * The host must (1) accept exactly {workspace}, (2) set up only the folder the host has open, (3) refuse while a run is active, (4) take one setup at a time,
 * (5) report Kriya's own answer - refusals included - and never claim a creation Kriya did not report, and (6) never be reachable from the generic query channel.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { IPC_CHANNELS, validateInitializeProject, validateKupRequest } from '../src/main/ipc_contract';
import { toServiceRequest } from '../src/main/kriya_service';
import { buildKriyaArgv } from '../src/main/kriya_argv';
import { ProjectSetupHost } from '../src/main/project_setup_host';

const WS = '/work/proj';
const envelope = (data: unknown, error: unknown = null) => ({ schema_version: 1, operation: 'project.initialize', request_id: 'q1', observed_at: 'now', source: null, consistency: null, data, error });
const created = async () => envelope({ workspace: WS, outcome: 'created', file: 'kriya.yaml', profile: 'local-models-governed', summary: { model: 'qwen3-coder:30b', fallback_models: ['qwen3.6:35b-a3b-q4_K_M'], workflow: 'enforce' }, next: 'x' });
function make(answer: (ws: string) => Promise<unknown> = created, open: string | null = WS, active = false) {
  const asked: string[] = [];
  const host = new ProjectSetupHost({ initialize: (ws) => { asked.push(ws); return answer(ws); }, currentWorkspace: () => open, runActive: () => active });
  return { host, asked };
}

describe('request grammar and channels', () => {
  it('is exactly {workspace}: no setting, model, path or content can ride along', () => {
    expect(validateInitializeProject({ workspace: WS })).toEqual({ ok: true, value: { workspace: WS } });
    for (const bad of [null, [], 'x', {}, { workspace: 'rel' }, { workspace: WS, model: 'evil' }, { workspace: WS, yaml: 'a: 1' }, { workspace: WS, path: '/etc' }, { workspace: 7 }, { workspace: '/a\u0000b' }]) {
      expect(validateInitializeProject(bad).ok, JSON.stringify(bad)).toBe(false);
    }
  });
  it('has its own channel; project.initialize is not on the query channel and has no one-shot command line', () => {
    expect(IPC_CHANNELS.initializeProject).toBe('kriya:host:project:initialize');
    expect(validateKupRequest({ operation: 'project.initialize', workspace: WS }).ok).toBe(false);
    expect(() => buildKriyaArgv({ operation: 'project.initialize', workspace: WS })).toThrow(/persistent Kriya service/);
    expect(toServiceRequest('q1', { operation: 'project.initialize', workspace: WS }).arguments).toEqual({ workspace: WS });
    expect(() => toServiceRequest('q1', { operation: 'project.initialize', workspace: WS, yaml: 'x' } as never)).toThrow(/does not take: yaml/);
  });
});

describe('host rules', () => {
  it('asks Kriya once with exactly the open folder and reports the creation with Kriya\'s own summary', async () => {
    const { host, asked } = make();
    const out = await host.initialize({ workspace: WS });
    expect(out).toMatchObject({ initialized: true, outcome: 'created', code: null });
    expect(out.summary).toMatchObject({ model: 'qwen3-coder:30b', workflow: 'enforce' });
    expect(asked).toEqual([WS]);
  });
  it('an invalid request, another folder, no open folder, or an active run never reaches Kriya', async () => {
    const { host, asked } = make();
    expect(await host.initialize({ workspace: WS, model: 'x' })).toMatchObject({ initialized: false, code: 'INVALID_REQUEST' });
    expect(await host.initialize({ workspace: '/work/other' })).toMatchObject({ initialized: false, code: 'WORKSPACE_NOT_OPEN' });
    expect(asked).toEqual([]);
    const closed = make(created, null);
    expect(await closed.host.initialize({ workspace: WS })).toMatchObject({ code: 'WORKSPACE_NOT_OPEN' });
    const busy = make(created, WS, true);
    expect(await busy.host.initialize({ workspace: WS })).toMatchObject({ initialized: false, code: 'RUN_ACTIVE' });
    expect([...closed.asked, ...busy.asked]).toEqual([]);
  });
  it('one setup at a time: a double click is refused before Kriya is asked again, and the guard is released afterwards', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    const { host, asked } = make(async () => { await gate; return created(); });
    const first = host.initialize({ workspace: WS });
    expect(await host.initialize({ workspace: WS })).toMatchObject({ initialized: false, code: 'SETUP_IN_FLIGHT' });
    release();
    expect((await first).initialized).toBe(true);
    expect(asked).toHaveLength(1);
    expect((await host.initialize({ workspace: WS })).initialized).toBe(true);
  });
  it('Kriya\'s typed refusals (already configured, unwritable) are passed through verbatim and are never a creation', async () => {
    const { host } = make(async () => envelope(null, { code: 'INVALID_REQUEST', message: 'this folder already has kriya.yaml; Project Setup never overwrites a configuration', reason: 'already_configured', database_state: null }));
    expect(await host.initialize({ workspace: WS })).toEqual({ initialized: false, outcome: null, code: 'INVALID_REQUEST', message: 'this folder already has kriya.yaml; Project Setup never overwrites a configuration', reason: 'already_configured', summary: null });
  });
  it('any other outcome, a non-envelope, or a transport failure is not a creation and leaks no stack or path', async () => {
    for (const data of [{ outcome: 'approved' }, {}, null, 'created']) expect(await make(async () => envelope(data)).host.initialize({ workspace: WS }), JSON.stringify(data)).toMatchObject({ initialized: false, code: 'INVALID_RESPONSE' });
    expect(await make(async () => 'nope').host.initialize({ workspace: WS })).toMatchObject({ initialized: false, code: 'INVALID_RESPONSE' });
    const answer = await make(async () => { throw new Error('spawn ENOENT /secret/path/kriya'); }).host.initialize({ workspace: WS });
    expect(answer).toMatchObject({ initialized: false, code: 'HOST_ERROR' });
    expect(JSON.stringify(answer)).not.toContain('/secret/path');
  });
});

describe('structure (main process)', () => {
  const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
  it('project.initialize is named once in main.ts, inside the ProjectSetupHost wiring, and its only caller is its own IPC handler', () => {
    expect(main.match(/project\.initialize/g)).toHaveLength(1);
    expect(main.match(/projectSetup\.initialize\(/g)).toHaveLength(1);
    expect(main).toContain('ipcMain.handle(IPC_CHANNELS.initializeProject');
    const block = main.slice(main.indexOf('projectSetup = new ProjectSetupHost({'), main.indexOf('approvals = new ApprovalHost({'));
    expect(block).toContain("operation: 'project.initialize'");
    expect(block).toContain('runActive: () => execution.active()');
  });
  it('the setup does not approve anything: the host has no path to configuration.approve', () => {
    const host = readFileSync(join(__dirname, '..', 'src', 'main', 'project_setup_host.ts'), 'utf8');
    expect(host).not.toMatch(/configuration\.approve|approve\(|grant/);
  });
});
