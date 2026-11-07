/**
 * Recover Project (host): a narrow proxy for Kriya's own `runs recover` on the folder THIS host has open. Controlled executors; nothing here judges cleanliness.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ALLOWED_ARGV_PREFIXES, argvIsAllowed, recoverArgv } from '../src/main/kriya_argv';
import { RECOVER_BUTTONS, RecoverHost, isRecoverResponse, recoverDialog, type RecoverDeps } from '../src/main/recover_host';

const WS = '/Users/dev/projects/calc';
const report = (status: string, errors: string[] = []) => ({ after: { status }, errors, recovered_runs: [] });
function make(over: Partial<RecoverDeps> & { confirmed?: boolean; answer?: unknown } = {}) {
  const calls: string[] = []; const asked: string[] = [];
  const deps: RecoverDeps = {
    recover: async (w) => { calls.push(w); return over.answer ?? report('CLEAN'); },
    confirm: async (w) => { asked.push(w); return over.confirmed ?? true; },
    currentWorkspace: () => WS, runActive: () => false, ...over,
  };
  return { host: new RecoverHost(deps), calls, asked };
}

describe('Recover Project host', () => {
  it('asks the person first (native), then Kriya for the OPEN folder, and says recovered only when KRIYA reports CLEAN afterwards', async () => {
    const m = make();
    expect(await m.host.recover()).toEqual({ recovered: true, status: 'CLEAN', code: null, message: null, errors: [] });
    expect(m.asked).toEqual([WS]); expect(m.calls).toEqual([WS]);
  });
  it('a status other than CLEAN after recovery is reported with Kriya\'s own word and is NOT recovered (no false clean)', async () => {
    for (const status of ['RECOVERY_AVAILABLE', 'COMPLETE_PARTIAL_REQUIRED', 'MANUAL_ACTION_REQUIRED', 'RUN_ACTIVE']) {
      expect(await make({ answer: report(status) }).host.recover()).toMatchObject({ recovered: false, status, code: null });
    }
  });
  it('CLEAN with errors reported is not recovered either', async () => {
    expect(await make({ answer: report('CLEAN', ['could not remove a staged file']) }).host.recover()).toMatchObject({ recovered: false, status: 'CLEAN', errors: ['could not remove a staged file'] });
  });
  it('a declined confirmation asks Kriya nothing and changes nothing', async () => {
    const m = make({ confirmed: false });
    expect(await m.host.recover()).toMatchObject({ recovered: false, code: 'CONFIRMATION_DECLINED' });
    expect(m.calls).toEqual([]);
  });
  it('refuses with no open folder or while a run is active - before asking anything', async () => {
    const none = make({ currentWorkspace: () => null }); expect(await none.host.recover()).toMatchObject({ code: 'WORKSPACE_NOT_OPEN' }); expect(none.asked).toEqual([]);
    const busy = make({ runActive: () => true }); expect(await busy.host.recover()).toMatchObject({ code: 'RUN_ACTIVE' }); expect(busy.asked).toEqual([]);
  });
  it('a Kriya failure or an unreadable report is a typed refusal, never a claim of success', async () => {
    expect(await make({ recover: async () => { throw new Error('spawn failed'); } }).host.recover()).toMatchObject({ recovered: false, code: 'HOST_ERROR', status: null });
    for (const answer of [null, 'text', {}, { after: {} }, { after: { status: 3 } }]) expect(await make({ recover: async () => answer }).host.recover()).toMatchObject({ recovered: false, code: 'INVALID_RESPONSE' });
  });
  it('a second recovery while one runs is refused', async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((r) => { release = r; });
    const m = make({ recover: async () => { await gate; return report('CLEAN'); } });
    const first = m.host.recover();
    await new Promise((r) => setTimeout(r, 5));
    expect(await m.host.recover()).toMatchObject({ code: 'RECOVER_IN_FLIGHT' });
    release();
    expect(await first).toMatchObject({ recovered: true });
  });
});

describe('the recover command line and its dialog', () => {
  it('is one fixed argv: runs recover --workspace <folder> --json, allowlisted, never --complete-partial', () => {
    expect(recoverArgv(WS)).toEqual(['runs', 'recover', '--workspace', WS, '--json']);
    expect(argvIsAllowed(recoverArgv(WS))).toBe(true);
    expect(recoverArgv(WS).join(' ')).not.toContain('complete-partial');
    expect(ALLOWED_ARGV_PREFIXES.some((p) => p.join(' ') === 'runs recover --workspace')).toBe(true);
    expect(argvIsAllowed(['runs', 'recover', '--complete-partial'])).toBe(false);      // only the exact prefix is allowed
    expect(argvIsAllowed(['runs', 'prune', '--workspace', WS])).toBe(false);
  });
  it('the native confirmation defaults to Cancel and only the explicit button commits; its text is a function of the folder name', () => {
    const d = recoverDialog(WS);
    expect(d.buttons).toEqual([...RECOVER_BUTTONS]); expect(d.defaultId).toBe(0); expect(d.cancelId).toBe(0);
    expect(isRecoverResponse(1)).toBe(true); expect(isRecoverResponse(0)).toBe(false);
    expect(d.message).toContain('calc'); expect(JSON.stringify(d)).not.toContain('/Users/dev');
    expect(d.detail).toContain('does not change your source files');
    expect(recoverDialog('/x/evil\u0007\nname').message).not.toMatch(/[\u0000-\u001f]/);
  });
  it('main.ts takes no renderer argument for cancel or recover, checks the sender, and the preload exposes them with no payload', () => {
    const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
    for (const name of ['cancelRun', 'recoverWorkspace']) {
      const body = main.slice(main.indexOf(`IPC_CHANNELS.${name}`), main.indexOf(`IPC_CHANNELS.${name}`) + 260);
      expect(body).toContain('async (event) =>');
      expect(body.indexOf('fromOwnedRenderer(event)')).toBeGreaterThan(-1);
    }
    const preload = readFileSync(join(__dirname, '..', 'src', 'main', 'preload.ts'), 'utf8');
    expect(preload).toContain('cancelRun: () => ipcRenderer.invoke(IPC_CHANNELS.cancelRun)');
    expect(preload).toContain('recoverWorkspace: () => ipcRenderer.invoke(IPC_CHANNELS.recoverWorkspace)');
  });
});
