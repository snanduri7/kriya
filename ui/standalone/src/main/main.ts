/**
 * Electron main process (gate A-1). The ONLY process that spawns anything, and only the P-25 grammar through
 * kriya_process.ts. Reads no Kriya state, opens no listener, loads only the local renderer bundle.
 * D-9: unless KRIYA_UI_ALLOW_REAL_KRIYA=1 is set AND a kriya executable is configured, every KUP query goes to the
 * fixture stand-in (fake-kriya/fake_kriya.mjs). Remove that gate only after the owner lifts matrix protection.
 */
import { app, BrowserWindow, clipboard, ipcMain, session, type IpcMainInvokeEvent } from 'electron';
import { spawn } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { CONTENT_SECURITY_POLICY, PERMISSION_DECISION, WEB_PREFERENCES } from './hardening';
import { buildKriyaChildEnv, resolveConfigDirectory } from './child_env';
import { IPC_CHANNELS, LIMITS, validateKupRequest, validateOpenInIde, validateSettingKey, validateSettingValue } from './ipc_contract';
import { buildKriyaArgv } from './kriya_argv';
import { runKriya } from './kriya_process';
import { runMeasurement } from './measure';
import { DEFAULT_SOAK, runSoak } from './soak';
import { EDITOR_FORMS, planOpenInIde } from './open_in_ide';
import { SettingsStore } from './settings';

const MEASURE = process.env.KRIYA_UI_MEASURE === '1';
const SOAK = process.env.KRIYA_UI_SOAK === '1';
const ALLOW_REAL = process.env.KRIYA_UI_ALLOW_REAL_KRIYA === '1';
const FAKE_KRIYA = join(__dirname, '..', '..', 'fake-kriya', 'fake_kriya.mjs');
const RENDERER_INDEX = join(__dirname, '..', 'renderer', 'index.html');
const HOST_VERSION = '0.0.1';

let settings: SettingsStore;
let ownedWebContentsId: number | null = null;

function errorEnvelope(operation: string, code: string, message: string) {
  return { schema_version: 1, operation, request_id: `host-${Date.now()}`, observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code, message } };
}

/** Only the two fixture knobs reach the stand-in; never the ambient environment (P-31). */
function fakeEnv(): Record<string, string> {
  const env: Record<string, string> = {};
  if (process.env.KRIYA_FAKE_BEHAVIOR) env.KRIYA_FAKE_BEHAVIOR = process.env.KRIYA_FAKE_BEHAVIOR;
  if (process.env.KRIYA_FAKE_FIXTURES) env.KRIYA_FAKE_FIXTURES = process.env.KRIYA_FAKE_FIXTURES;
  env.KRIYA_FAKE_STATE = process.env.KRIYA_FAKE_STATE ?? join(app.getPath('userData'), 'fake-kriya-snapshots.json');
  return env;
}

function fromOwnedRenderer(event: IpcMainInvokeEvent): boolean { return ownedWebContentsId !== null && event.sender.id === ownedWebContentsId; }

const isDirectory = (p: string) => { try { return statSync(p).isDirectory(); } catch { return false; } };
/** The explicit configuration directory (F-5): every kriya child runs with this cwd; Electron's own cwd is never used. */
function configDirectory() { return resolveConfigDirectory(settings.get('configDirectory'), process.env, isDirectory); }

function registerIpc() {
  ipcMain.handle(IPC_CHANNELS.hostInfo, (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const real = ALLOW_REAL && !!settings.get('kriyaExecutable');
    const dir = configDirectory();
    return { kind: 'electron', hostVersion: HOST_VERSION, fixtureMode: !real, configDirectory: dir.ok ? dir.directory : null, configDirectorySource: dir.ok ? dir.source : 'invalid', configDirectoryProblem: dir.ok ? null : dir.message };
  });
  ipcMain.handle(IPC_CHANNELS.query, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const v = validateKupRequest(raw);
    if (!v.ok) return errorEnvelope(String((raw as { operation?: unknown } | null)?.operation ?? 'unknown'), 'INVALID_REQUEST', v.message);
    const argv = buildKriyaArgv(v.value);
    const configured = settings.get('kriyaExecutable');
    const useReal = ALLOW_REAL && configured && existsSync(configured);
    // F-5: the child's working directory is the validated configuration directory, in BOTH modes; a missing or invalid
    // one is a typed refusal, never a fallback to Electron's own launch directory.
    const dir = configDirectory();
    if (!dir.ok) return errorEnvelope(v.value.operation, 'HOST_ERROR', `kriya not launched: ${dir.message}`);
    let outcome;
    if (useReal) {
      // The real child gets exactly the owner's environment policy (child_env.ts): fixed PATH, fixed
      // PYTHONDONTWRITEBYTECODE=1, the operator's HOME, an absolute operator KRIYA_STATE_DIR if set - nothing else.
      const childEnv = buildKriyaChildEnv(process.env);
      if (!childEnv.ok) return errorEnvelope(v.value.operation, 'HOST_ERROR', `kriya not launched: ${childEnv.message}`);
      outcome = await runKriya({ executable: configured, argv, env: childEnv.env, cwd: dir.directory });
    } else {
      outcome = await runKriya({ executable: process.execPath, argv: [FAKE_KRIYA, ...argv], nodeScript: true, env: fakeEnv(), cwd: dir.directory });
    }
    if (outcome.kind === 'json') return outcome.json;
    return errorEnvelope(v.value.operation, outcome.code ?? 'HOST_ERROR', `${outcome.message ?? 'unknown'}${outcome.stderrTail ? ` | stderr: ${outcome.stderrTail.slice(-300)}` : ''}`);
  });
  ipcMain.handle(IPC_CHANNELS.openInIde, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const v = validateOpenInIde(raw);
    const editorId = settings.get('editor');
    const form = EDITOR_FORMS[editorId];
    if (!v.ok) return { ok: false, editor: editorId, verified: form.verified, message: v.message };
    const plan = planOpenInIde(editorId, settings.get('workspacePath'), v.value.path, v.value.line);
    if (!plan.ok) return { ok: false, editor: editorId, verified: form.verified, message: plan.reason };
    return new Promise((resolve) => {
      const child = spawn(plan.binary, plan.argv, { stdio: 'ignore', shell: false, detached: false });
      child.on('error', (e) => resolve({ ok: false, editor: editorId, verified: form.verified, message: `${form.label}: ${e.message} (${form.verificationNote})` }));
      child.on('spawn', () => resolve({ ok: true, editor: editorId, verified: form.verified, message: `${form.label}: ${plan.argv.join(' ')}${form.verified ? '' : ` (${form.verificationNote})`}` }));
    });
  });
  ipcMain.handle(IPC_CHANNELS.clipboardWrite, (event, text: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    if (typeof text !== 'string' || text.length > LIMITS.clipboardMax) throw new Error('clipboard text must be a string of at most 8 MiB');
    clipboard.writeText(text);
  });
  ipcMain.handle(IPC_CHANNELS.settingGet, (event, key: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const k = validateSettingKey(key); if (!k.ok) throw new Error(k.message);
    return settings.get(k.value);
  });
  ipcMain.handle(IPC_CHANNELS.settingSet, (event, key: unknown, value: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const k = validateSettingKey(key); if (!k.ok) throw new Error(k.message);
    const v = validateSettingValue(k.value, value); if (!v.ok) throw new Error(v.message);
    settings.set(k.value, v.value as never);
  });
}

function hardenSession() {
  const s = session.defaultSession;
  s.setPermissionRequestHandler((_wc, _permission, callback) => callback(PERMISSION_DECISION));
  s.setPermissionCheckHandler(() => PERMISSION_DECISION);
  s.webRequest.onHeadersReceived((details, callback) => {
    callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [CONTENT_SECURITY_POLICY] } });
  });
  // Nothing but the local bundle (file://) may ever be requested: no remote content, ever.
  s.webRequest.onBeforeRequest((details, callback) => callback({ cancel: !details.url.startsWith('file://') }));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1400, height: 900, title: 'Kriya run inspector',
    webPreferences: { ...WEB_PREFERENCES, preload: join(__dirname, 'preload.js'), additionalArguments: MEASURE || SOAK ? ['--kriya-ui-measure'] : [] },
  });
  ownedWebContentsId = win.webContents.id;
  if (MEASURE) {
    // Diagnostics for the measurement run only (stderr): load lifecycle, preload errors, renderer console.
    const log = (m: string) => process.stderr.write(`[measure] ${m}\n`);
    win.webContents.on('did-finish-load', () => log('did-finish-load'));
    win.webContents.on('did-fail-load', (_e, code, desc, url) => log(`did-fail-load ${code} ${desc} ${url}`));
    win.webContents.on('preload-error', (_e, path, error) => log(`preload-error ${path}: ${error.message}`));
    win.webContents.on('console-message', (event) => log(`console[${event.level}] ${event.message} (${event.sourceId}:${event.lineNumber})`));
    win.webContents.on('render-process-gone', (_e, details) => log(`render-process-gone ${details.reason}`));
  }
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.webContents.on('will-redirect', (e) => e.preventDefault());
  win.webContents.on('will-attach-webview', (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  void win.loadFile(RENDERER_INDEX);
  return win;
}

app.whenReady().then(async () => {
  settings = new SettingsStore(join(app.getPath('userData'), 'kriya-ui-settings.json'));
  hardenSession();
  registerIpc();
  const win = createWindow();
  if (SOAK) {
    try {
      const n = (k: string, d: number) => Number(process.env[k] ?? d);
      const file = await runSoak(win, join(__dirname, '..', '..', 'measurements'), { seconds: n('KRIYA_UI_SOAK_SECONDS', DEFAULT_SOAK.seconds), minSelections: n('KRIYA_UI_SOAK_MIN_SELECTIONS', DEFAULT_SOAK.minSelections), sampleSeconds: n('KRIYA_UI_SOAK_SAMPLE_SECONDS', DEFAULT_SOAK.sampleSeconds), warmupSeconds: n('KRIYA_UI_SOAK_WARMUP_SECONDS', DEFAULT_SOAK.warmupSeconds), ceilingMB: n('KRIYA_UI_SOAK_CEILING_MB', DEFAULT_SOAK.ceilingMB) });
      process.stdout.write(`soak written: ${file}\n`);
      app.exit(0);
    } catch (e) {
      process.stderr.write(`soak failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
      app.exit(1);
    }
  } else if (MEASURE) {
    try {
      const file = await runMeasurement(win, join(__dirname, '..', '..', 'measurements'), Number(process.env.KRIYA_UI_MEASURE_CYCLES ?? 100));
      process.stdout.write(`measurement written: ${file}\n`);
      app.exit(0);
    } catch (e) {
      process.stderr.write(`measurement failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
      app.exit(1);
    }
  }
});
app.on('window-all-closed', () => app.quit());
app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (e) => e.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
});
