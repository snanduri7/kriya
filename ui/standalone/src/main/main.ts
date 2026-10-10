/**
 * Electron main process (gate A-1). The ONLY process that spawns anything, and only the P-25 grammar through
 * kriya_process.ts. Reads no Kriya state, opens no listener, loads only the local renderer bundle.
 * D-9: unless KRIYA_UI_ALLOW_REAL_KRIYA=1 is set AND a kriya executable is configured, every KUP query goes to the
 * fixture stand-in (fake-kriya/fake_kriya.mjs). Remove that gate only after the owner lifts matrix protection.
 * GUI-F-A1: that decision is made ONCE (host_mode.ts) and consulted by both the hostInfo and the query handler, so
 * the trust strip never shows a host state the queries do not use; a configured executable that does not exist is
 * a typed HOST_ERROR on every call (like an invalid configuration directory, F-5), never a silent fixture answer.
 */
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, session, type IpcMainInvokeEvent } from 'electron';
import { spawn } from 'node:child_process';
import { existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { CONTENT_SECURITY_POLICY, PERMISSION_DECISION, WEB_PREFERENCES } from './hardening';
import { buildKriyaChildEnv, resolveConfigDirectory } from './child_env';
import { isFixtureMode, resolveHostMode } from './host_mode';
import { PRODUCT_NAME, WINDOW_TITLE, aboutPanelOptions, applicationMenuTemplate, resolveUserData } from './identity';
import { IPC_CHANNELS, LIMITS, MENU_CHANNEL, type MenuCommand, validateKupRequest, validateOpenInIde, validateSettingKey, validateSettingValue } from './ipc_contract';
import { buildKriyaArgv } from './kriya_argv';
import { runKriya } from './kriya_process';
import { KriyaServiceHolder } from './kriya_service';
import { WorkspaceRegistry } from './workspace_host';
import { runCapture } from './capture';
import { runMeasurement } from './measure';
import { DEFAULT_SOAK, runSoak } from './soak';
import { EDITOR_FORMS, planOpenInIde } from './open_in_ide';
import { SettingsStore } from './settings';

// Product identity (identity.ts) is set before anything else reads the application name. The name decides Electron's default userData
// directory, so the directory earlier builds used is pinned explicitly (an operator's --user-data-dir still wins).
const pinnedUserData = resolveUserData(app.getPath('appData'), app.commandLine.hasSwitch('user-data-dir'));
app.setName(PRODUCT_NAME);
if (pinnedUserData !== null) app.setPath('userData', pinnedUserData);

const MEASURE = process.env.KRIYA_UI_MEASURE === '1';
const SOAK = process.env.KRIYA_UI_SOAK === '1';
const CAPTURE_DIR = process.env.KRIYA_UI_CAPTURE; // development screenshots only (capture.ts)
const CAPTURE_STEPS = process.env.KRIYA_UI_CAPTURE_STEPS;
// Development evidence only (reachable only while the screenshot driver is active): the folders the scripted chooser returns in place of the native dialog,
// a JSON array where null means the person cancelled. Everything after the dialog - registry, approval, Kriya's validation - is the production path.
const CAPTURE_CHOICES: Array<string | null> | null = CAPTURE_DIR && process.env.KRIYA_UI_CAPTURE_CHOOSE ? (JSON.parse(process.env.KRIYA_UI_CAPTURE_CHOOSE) as Array<string | null>) : null;
const TRACE = process.env.KRIYA_UI_TRACE === '1'; // per-request timings and service lifecycle on stderr (measurement)
const ALLOW_REAL = process.env.KRIYA_UI_ALLOW_REAL_KRIYA === '1';
const FAKE_KRIYA = join(__dirname, '..', '..', 'fake-kriya', 'fake_kriya.mjs');
const RENDERER_INDEX = join(__dirname, '..', 'renderer', 'index.html');
const HOST_VERSION = '0.0.1';

// M5: real-mode reads go through ONE persistent `kriya kup-stdio` child (kriya_service.ts). KRIYA_UI_TRANSPORT=spawn keeps the one-shot
// command path (compatibility, tooling and the 'before' measurement); both reach the same Kriya operation implementation.
const TRANSPORT: 'service' | 'spawn' = process.env.KRIYA_UI_TRANSPORT === 'spawn' ? 'spawn' : 'service';
const services = new KriyaServiceHolder();

let settings: SettingsStore;
let workspaces: WorkspaceRegistry;
let ownedWebContentsId: number | null = null;
let ownedWindow: BrowserWindow | null = null;

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
    const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync);
    const dir = configDirectory();
    return { kind: 'electron', hostVersion: HOST_VERSION, fixtureMode: isFixtureMode(host), configDirectory: dir.ok ? dir.directory : null, configDirectorySource: dir.ok ? dir.source : 'invalid', configDirectoryProblem: dir.ok ? null : dir.message };
  });
  ipcMain.handle(IPC_CHANNELS.query, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const v = validateKupRequest(raw);
    if (!v.ok) return errorEnvelope(String((raw as { operation?: unknown } | null)?.operation ?? 'unknown'), 'INVALID_REQUEST', v.message);
    // M5: a request may name a folder only if the person chose it (native chooser this session, or the recent list).
    const refused = workspaces.refuse(v.value);
    if (refused) return errorEnvelope(v.value.operation, 'INVALID_REQUEST', refused);
    const argv = buildKriyaArgv(v.value);
    // GUI-F-A1: the same decision hostInfo reports; a configured executable that does not exist is refused here,
    // never answered by the stand-in.
    const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync);
    if (host.mode === 'error') return errorEnvelope(v.value.operation, 'HOST_ERROR', `kriya not launched: ${host.message}`);
    // F-5: the child's working directory is the validated configuration directory, in BOTH modes; a missing or invalid
    // one is a typed refusal, never a fallback to Electron's own launch directory.
    const dir = configDirectory();
    if (!dir.ok) return errorEnvelope(v.value.operation, 'HOST_ERROR', `kriya not launched: ${dir.message}`);
    let outcome;
    if (host.mode === 'real') {
      // The real child gets exactly the owner's environment policy (child_env.ts): fixed PATH, fixed
      // PYTHONDONTWRITEBYTECODE=1, the operator's HOME, an absolute operator KRIYA_STATE_DIR if set - nothing else.
      const childEnv = buildKriyaChildEnv(process.env);
      if (!childEnv.ok) return errorEnvelope(v.value.operation, 'HOST_ERROR', `kriya not launched: ${childEnv.message}`);
      if (TRANSPORT === 'service') {
        const service = services.get({ executable: host.executable, cwd: dir.directory, env: childEnv.env });
        const served = await service.request(v.value);
        if (TRACE) process.stderr.write(`[kriya-ui] service ${v.value.operation} ${served.durationMs} ms${service.stats.starts > 0 && service.stats.requests === 1 ? ` (cold: child ready in ${service.stats.lastReadyMs} ms)` : ''}\n`);
        if (served.kind === 'json') return served.json;
        return errorEnvelope(v.value.operation, served.code, `${served.message}${served.stderrTail ? ` | stderr: ${served.stderrTail.slice(-300)}` : ''}`);
      }
      outcome = await runKriya({ executable: host.executable, argv, env: childEnv.env, cwd: dir.directory });
    } else {
      outcome = await runKriya({ executable: process.execPath, argv: [FAKE_KRIYA, ...argv], nodeScript: true, env: fakeEnv(), cwd: dir.directory });
    }
    if (TRACE) process.stderr.write(`[kriya-ui] spawn ${v.value.operation} ${outcome.durationMs} ms\n`);
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
    if (k.value === 'workspacePath') {
      const problem = workspaces.refuseSetting(v.value);
      if (problem) throw new Error(problem);
    }
    settings.set(k.value, v.value as never);
  });
  // M5 workspace lifecycle. The native chooser runs HERE, in the main process; the renderer receives a path and nothing else.
  ipcMain.handle(IPC_CHANNELS.chooseWorkspace, async (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return workspaces.choose(async () => {
      if (CAPTURE_CHOICES) return CAPTURE_CHOICES.shift() ?? null;
      const parent = BrowserWindow.fromWebContents(event.sender) ?? undefined;
      const options = { title: 'Open Folder', buttonLabel: 'Open', properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'> };
      const result = parent ? await dialog.showOpenDialog(parent, options) : await dialog.showOpenDialog(options);
      return result.canceled || result.filePaths.length !== 1 ? null : result.filePaths[0]!;
    });
  });
  ipcMain.handle(IPC_CHANNELS.recentWorkspaces, (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return workspaces.recent();
  });
  ipcMain.handle(IPC_CHANNELS.rememberWorkspace, (event, path: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const r = workspaces.remember(path); if (!r.ok) throw new Error(r.message);
    return r.value;
  });
  ipcMain.handle(IPC_CHANNELS.forgetWorkspace, (event, path: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const r = workspaces.forget(path); if (!r.ok) throw new Error(r.message);
    return r.value;
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
    width: 1400, height: 900, title: WINDOW_TITLE,
    webPreferences: { ...WEB_PREFERENCES, preload: join(__dirname, 'preload.js'), additionalArguments: MEASURE || SOAK ? ['--kriya-ui-measure'] : [] },
  });
  ownedWebContentsId = win.webContents.id;
  ownedWindow = win;
  if (MEASURE) {
    // Diagnostics for the measurement run only (stderr): load lifecycle, preload errors, renderer console.
    const log = (m: string) => process.stderr.write(`[measure] ${m}\n`);
    win.webContents.on('did-finish-load', () => log('did-finish-load'));
    win.webContents.on('did-fail-load', (_e, code, desc, url) => log(`did-fail-load ${code} ${desc} ${url}`));
    win.webContents.on('preload-error', (_e, path, error) => log(`preload-error ${path}: ${error.message}`));
    win.webContents.on('console-message', (event) => log(`console[${event.level}] ${event.message} (${event.sourceId}:${event.lineNumber})`));
    win.webContents.on('render-process-gone', (_e, details) => log(`render-process-gone ${details.reason}`));
  }
  win.on('page-title-updated', (e) => e.preventDefault()); // a page can never rename the product window
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.webContents.on('will-redirect', (e) => e.preventDefault());
  win.webContents.on('will-attach-webview', (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  void win.loadFile(RENDERER_INDEX);
  return win;
}

app.whenReady().then(async () => {
  app.setAboutPanelOptions(aboutPanelOptions(app.getVersion()));
  settings = new SettingsStore(join(app.getPath('userData'), 'kriya-ui-settings.json'));
  workspaces = new WorkspaceRegistry({
    getRecent: () => settings.get('recentWorkspaces'), setRecent: (list) => settings.set('recentWorkspaces', list),
    getCurrent: () => settings.get('workspacePath'), setCurrent: (path) => settings.set('workspacePath', path),
  });
  workspaces.close(); // a launch starts at the welcome screen: no folder is open until the person opens one
  const command = (name: MenuCommand) => { if (ownedWindow && !ownedWindow.isDestroyed()) ownedWindow.webContents.send(MENU_CHANNEL, name); };
  Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenuTemplate(process.platform === 'darwin', PRODUCT_NAME, { openFolder: () => command('openFolder') })));
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
  } else if (CAPTURE_DIR && CAPTURE_STEPS) {
    try {
      const files = await runCapture(win, CAPTURE_DIR, CAPTURE_STEPS);
      process.stdout.write(`captured: ${files.join(', ')}\n`);
    } catch (e) {
      process.stderr.write(`capture failed: ${e instanceof Error ? e.stack ?? e.message : String(e)}\n`);
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
// Ends the persistent child cleanly (EOF on its stdin); if the application dies instead, the closed pipe ends the child anyway.
app.on('before-quit', () => { void services.stop(); });
app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (e) => e.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
});
