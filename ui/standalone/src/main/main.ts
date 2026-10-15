/**
 * Electron main process (gate A-1). The ONLY process that spawns anything, and only the P-25 grammar through
 * kriya_process.ts. Reads no Kriya state, opens no listener, loads only the local renderer bundle.
 * Backend selection (host_mode.ts): a PACKAGED app has no stand-in and no gate: it is real, or a typed error naming the Settings executable
 * (GUI-APP-REAL-DEFAULT-001: a double-click launch carries no environment variable). The development stand-in (D-9) and every other development driver live in
 * dev_drivers.ts, which the shipped product package does not contain.
 * GUI-F-A1: that decision is made ONCE (host_mode.ts) and consulted by both the hostInfo and the query handler, so
 * the trust strip never shows a host state the queries do not use; a configured executable that does not exist is
 * a typed HOST_ERROR on every call (like an invalid configuration directory, F-5), never a silent fixture answer.
 */
import { app, BrowserWindow, clipboard, dialog, ipcMain, Menu, Notification, session, shell, type IpcMainInvokeEvent } from 'electron';
import { spawn } from 'node:child_process';
import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { CONTENT_SECURITY_POLICY, PERMISSION_DECISION, WEB_PREFERENCES } from './hardening';
import { buildKriyaChildEnv, resolveConfigDirectory } from './child_env';
import { isFixtureMode, resolveHostMode } from './host_mode';
import { PRODUCT_NAME, WINDOW_TITLE, aboutPanelOptions, applicationMenuTemplate, resolveUserData } from './identity';
import { IPC_CHANNELS, LIMITS, MENU_CHANNEL, type MenuCommand, type KupRequest, validateKupRequest, validateOpenInIde, rendererMayWrite, validateSettingKey, validateSettingValue } from './ipc_contract';
import { buildKriyaArgv } from './kriya_argv';
import { runKriya } from './kriya_process';
import { KriyaServiceHolder } from './kriya_service';
import { WorkspaceRegistry } from './workspace_host';
import { ApprovalHost } from './approval_host';
import { RuntimeCommandHost, confirmationDialog as runtimeConfirmationDialog, isRunResponse } from './runtime_command_host';
import { ConfigApprovalHost, confirmationDialog, isApproveResponse } from './config_approval_host';
import { ProjectSetupHost } from './project_setup_host';
import { ProjectHost } from './project_host';
import { NotificationHost } from './notification_host';
import { ExecutionHost } from './execution_host';
import { launchExecution } from './execution_process';
import { resolveUiMode } from './ui_mode';
import { mustRefuseStart } from './product_guard';
import type { DevDrivers } from './dev_drivers';
import { EDITOR_FORMS, planOpenInIde } from './open_in_ide';
import { SettingsStore } from './settings';

// Product identity (identity.ts) is set before anything else reads the application name. The name decides Electron's default userData
// directory, so the directory earlier builds used is pinned explicitly (an operator's --user-data-dir still wins).
const pinnedUserData = resolveUserData(app.getPath('appData'), app.commandLine.hasSwitch('user-data-dir'));
app.setName(PRODUCT_NAME);
if (pinnedUserData !== null) app.setPath('userData', pinnedUserData);

/** Development and evidence drivers (capture, scripted chooser, measurement, soak, tracing, the fixture stand-in): loaded ONLY when this build carries dev_drivers.js. The shipped
 * product package omits it (mac-app.mjs) and the package check fails if any trace of it is present, so a product has no environment variable that scripts the window,
 * answers the folder chooser, or selects a stand-in backend. */
function loadDevDrivers(): DevDrivers | null {
  try { return (require('./dev_drivers') as typeof import('./dev_drivers')).createDevDrivers(process.env); } catch { return null; }
}
const dev = loadDevDrivers();
const ALLOW_REAL = dev?.allowReal ?? false;
{
  const refusal = mustRefuseStart(app.isPackaged, dev !== null, process.argv);
  if (refusal !== null) { process.stderr.write(`${refusal}\n`); app.exit(2); }
}
const RENDERER_DIR = join(__dirname, '..', 'renderer');
const RENDERER_INDEX = join(RENDERER_DIR, 'index.html');
// Which bundle this is (the Vite build records it): only an explicit M1-inspector build gets the narrow M1 workspace-setting compatibility.
const UI_MODE = resolveUiMode(() => { try { return readFileSync(join(RENDERER_DIR, 'ui-mode.json'), 'utf8'); } catch { return null; } });
const HOST_VERSION = '0.0.1';

// M5: real-mode reads go through ONE persistent `kriya kup-stdio` child (kriya_service.ts). KRIYA_UI_TRANSPORT=spawn keeps the one-shot
// command path (compatibility, tooling and the 'before' measurement); both reach the same Kriya operation implementation.
const TRANSPORT: 'service' | 'spawn' = process.env.KRIYA_UI_TRANSPORT === 'spawn' ? 'spawn' : 'service';
const services = new KriyaServiceHolder();

let settings: SettingsStore;
let workspaces: WorkspaceRegistry;
let execution: ExecutionHost;
let approvals: ApprovalHost;
let runtimeCommands: RuntimeCommandHost;
let configApprovals: ConfigApprovalHost;
let projectSetup: ProjectSetupHost;
let projects: ProjectHost;
let notifications: NotificationHost;
const liveNotifications = new Set<Notification>(); // a shown notification is kept until it is clicked or closed (otherwise it can be collected before the person sees it)
let ownedWebContentsId: number | null = null;
let ownedWindow: BrowserWindow | null = null;
let quitting = false; // set once the person has confirmed interrupting a run and quitting

/** `detail` is raw technical text (a child's stderr tail): it travels in its own field and the cockpit shows it only behind a "Technical detail" disclosure, never inside the message. */
function errorEnvelope(operation: string, code: string, message: string, detail?: string) {
  return { schema_version: 1, operation, request_id: `host-${Date.now()}`, observed_at: new Date().toISOString(), source: null, consistency: null, data: null, error: { code, message, ...(detail ? { detail: detail.slice(-600) } : {}) } };
}


function fromOwnedRenderer(event: IpcMainInvokeEvent): boolean { return ownedWebContentsId !== null && event.sender.id === ownedWebContentsId; }

const isDirectory = (p: string) => { try { return statSync(p).isDirectory(); } catch { return false; } };
/** The explicit configuration directory (F-5): every kriya child runs with this cwd; Electron's own cwd is never used. */
function configDirectory() { return resolveConfigDirectory(settings.get('configDirectory'), process.env, isDirectory); }

/** One KUP request through the SAME path the renderer's queries take (validation, workspace gate, persistent service), for host-initiated asks (the Start Run preflight). */
async function kupQuery(request: KupRequest): Promise<unknown> {
  const refused = workspaces.refuse(request);
  if (refused) return errorEnvelope(request.operation, 'INVALID_REQUEST', refused);
  const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync, app.isPackaged);
  if (host.mode !== 'real') return errorEnvelope(request.operation, 'HOST_ERROR', 'kriya not launched: no real Kriya executable is configured');
  const dir = configDirectory();
  if (!dir.ok) return errorEnvelope(request.operation, 'HOST_ERROR', `kriya not launched: ${dir.message}`);
  const childEnv = buildKriyaChildEnv(process.env);
  if (!childEnv.ok) return errorEnvelope(request.operation, 'HOST_ERROR', `kriya not launched: ${childEnv.message}`);
  const served = await services.get({ executable: host.executable, cwd: dir.directory, env: childEnv.env }).request(request);
  return served.kind === 'json' ? served.json : errorEnvelope(request.operation, served.code, served.message);
}

/** The executable and fixed child environment for an EXECUTION process (null = fixture stand-in: no execution backend). */
function prepareExecution() {
  const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync, app.isPackaged);
  if (host.mode === 'fixture') return null;
  if (host.mode === 'error') return { ok: false as const, message: host.message };
  const childEnv = buildKriyaChildEnv(process.env);
  return childEnv.ok ? { ok: true as const, executable: host.executable, env: childEnv.env } : { ok: false as const, message: childEnv.message };
}

/** Launch ids have the shape of a Kriya run id (YYYYMMDDTHHMMSS-xxxxxxxx); Kriya validates the shape and refuses a reused id. */
function mintLaunchId(): string {
  const d = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}T${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}-${randomBytes(4).toString('hex')}`;
}

function registerIpc() {
  ipcMain.handle(IPC_CHANNELS.hostInfo, (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync, app.isPackaged);
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
    // The one-shot command line. The operations only the persistent service serves have none: held as a typed refusal for the one-shot paths, never a thrown error.
    let argv: string[] = [];
    let oneShotRefusal: string | null = null;
    try { argv = buildKriyaArgv(v.value); } catch (error) { oneShotRefusal = error instanceof Error ? error.message : String(error); }
    // GUI-F-A1: the same decision hostInfo reports; a configured executable that does not exist is refused here,
    // never answered by the stand-in.
    const host = resolveHostMode(ALLOW_REAL, settings.get('kriyaExecutable'), existsSync, app.isPackaged);
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
        dev?.trace(`[kriya-ui] service ${v.value.operation} ${served.durationMs} ms${service.stats.starts > 0 && service.stats.requests === 1 ? ` (cold: child ready in ${service.stats.lastReadyMs} ms)` : ''}\n`);
        if (served.kind === 'json') return served.json;
        return errorEnvelope(v.value.operation, served.code, served.message, served.stderrTail || undefined);
      }
      if (oneShotRefusal) return errorEnvelope(v.value.operation, 'HOST_ERROR', oneShotRefusal);
      outcome = await runKriya({ executable: host.executable, argv, env: childEnv.env, cwd: dir.directory });
    } else {
      if (!dev) return errorEnvelope(v.value.operation, 'HOST_ERROR', 'kriya not launched: this build has no stand-in backend and no Kriya executable is configured');
      if (oneShotRefusal) return errorEnvelope(v.value.operation, 'HOST_ERROR', oneShotRefusal);
      outcome = await dev.runFake(argv, dir.directory);
    }
    dev?.trace(`[kriya-ui] spawn ${v.value.operation} ${outcome.durationMs} ms\n`);
    if (outcome.kind === 'json') return outcome.json;
    return errorEnvelope(v.value.operation, outcome.code ?? 'HOST_ERROR', outcome.message ?? 'unknown', outcome.stderrTail || undefined);
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
    if (!rendererMayWrite(k.value, UI_MODE, v.value)) throw new Error(`${k.value} is managed by the host and cannot be changed from the application window`);
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
      if (dev?.captureChoices) return dev.captureChoices.shift() ?? null;
      const parent = BrowserWindow.fromWebContents(event.sender) ?? undefined;
      const options = { title: 'Open Folder', buttonLabel: 'Open', properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'> };
      const result = parent ? await dialog.showOpenDialog(parent, options) : await dialog.showOpenDialog(options);
      return result.canceled || result.filePaths.length !== 1 ? null : result.filePaths[0]!;
    });
  });
  // End-user experience: New Project. The native chooser runs HERE; the renderer gets an opaque token for the parent folder and later asks for a name to be
  // created inside it. Kriya (`workspace.create`) makes the folder; this process never touches the filesystem for it.
  ipcMain.handle(IPC_CHANNELS.chooseProjectParent, async (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return projects.chooseParent(async () => {
      if (dev?.captureChoices) return dev.captureChoices.shift() ?? null;
      const parent = BrowserWindow.fromWebContents(event.sender) ?? undefined;
      const options = { title: 'New Project - choose where to create it', buttonLabel: 'Choose', properties: ['openDirectory', 'createDirectory'] as Array<'openDirectory' | 'createDirectory'> };
      const result = parent ? await dialog.showOpenDialog(parent, options) : await dialog.showOpenDialog(options);
      return result.canceled || result.filePaths.length !== 1 ? null : result.filePaths[0]!;
    });
  });
  ipcMain.handle(IPC_CHANNELS.createProject, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return projects.create(raw);
  });
  ipcMain.handle(IPC_CHANNELS.revealWorkspace, (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const current = workspaces.current(); // the folder the HOST has open; never a renderer-supplied path
    if (current === null) return { revealed: false, reason: 'no workspace is open' };
    if (!isDirectory(current)) return { revealed: false, reason: 'the folder is no longer there' };
    if (dev) return { revealed: false, reason: 'not available in an evidence build' };
    shell.showItemInFolder(current);
    return { revealed: true, reason: null };
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
  // M6 execution: the renderer can only ask the host to start THE run (typed {workspace, goal}) and read what the host knows about the process.
  ipcMain.handle(IPC_CHANNELS.startRun, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    const startedAt = Date.now();
    const result = await execution.start(raw);
    dev?.trace(`[kriya-ui] startRun ${result.started ? `launched ${result.launch_id}` : `refused ${result.refusal?.code}`} in ${Date.now() - startedAt} ms\n`);
    return result;
  });
  ipcMain.handle(IPC_CHANNELS.decideApproval, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return approvals.decide(raw);
  });
  ipcMain.handle(IPC_CHANNELS.decideRuntimeCommand, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return runtimeCommands.decide(raw);
  });
  ipcMain.handle(IPC_CHANNELS.approveConfiguration, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return configApprovals.approve(raw);
  });
  ipcMain.handle(IPC_CHANNELS.initializeProject, async (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return projectSetup.initialize(raw);
  });
  ipcMain.handle(IPC_CHANNELS.notifyRun, (event, raw: unknown) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return notifications.notify(raw);
  });
  ipcMain.handle(IPC_CHANNELS.executionState, (event) => {
    if (!fromOwnedRenderer(event)) throw new Error('unexpected sender');
    return execution.state();
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
    webPreferences: { ...WEB_PREFERENCES, ...(dev?.webPreferences ?? {}), preload: join(__dirname, 'preload.js'), additionalArguments: dev?.windowArgs ?? [] },
  });
  ownedWebContentsId = win.webContents.id;
  ownedWindow = win;
  dev?.attachWindow(win);
  // Closing the only window while a run this host started is in progress would leave the application running with no window and no way back to the run:
  // the same question as quitting, and the window stays until it is answered.
  win.on('close', (event) => {
    if (!quitting && execution && execution.active()) { event.preventDefault(); void interruptAndQuit(); }
  });
  win.on('page-title-updated', (e) => e.preventDefault()); // a page can never rename the product window
  win.webContents.on('will-navigate', (e) => e.preventDefault());
  win.webContents.on('will-redirect', (e) => e.preventDefault());
  win.webContents.on('will-attach-webview', (e) => e.preventDefault());
  win.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  void win.loadFile(RENDERER_INDEX);
  return win;
}

/**
 * The human commitment for a configuration approval: a native confirmation created HERE, in the main process, as a sheet on the application window. Its text is a
 * function of the open folder's name alone (config_approval_host.ts). Cancel is the default and the Escape action; only the `Approve Configuration` button returns true.
 * An evidence build driven by the capture driver answers from its scripted queue instead (dev_drivers.ts); an exhausted or absent queue means Cancel.
 */
async function confirmConfigurationApproval(workspace: string): Promise<boolean> {
  if (dev?.captureActive) return dev.nextConfirmation();
  const options = confirmationDialog(workspace);
  const parent = ownedWindow && !ownedWindow.isDestroyed() ? ownedWindow : null;
  const result = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options);
  return isApproveResponse(result.response);
}

/**
 * The human commitment for a runtime-command approval: a native confirmation created HERE, in the main process, as a sheet on the application window. Its text is a constant
 * (runtime_command_host.ts): no command, directory, fingerprint or renderer string enters it. Cancel is the default and the Escape action; only `Run Commands` returns true.
 * An evidence build driven by the capture driver answers from the same scripted queue as the configuration confirmation; an exhausted or absent queue means Cancel.
 */
async function confirmRuntimeCommands(): Promise<boolean> {
  if (dev?.captureActive) return dev.nextConfirmation();
  const options = runtimeConfirmationDialog();
  const parent = ownedWindow && !ownedWindow.isDestroyed() ? ownedWindow : null;
  const result = parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options);
  return isRunResponse(result.response);
}

app.whenReady().then(async () => {
  app.setAboutPanelOptions(aboutPanelOptions(app.getVersion()));
  settings = new SettingsStore(join(app.getPath('userData'), 'kriya-ui-settings.json'));
  execution = new ExecutionHost({
    launch: launchExecution, prepare: prepareExecution, currentWorkspace: () => workspaces.current(), mintLaunchId, now: () => new Date(),
    preflight: (workspace) => kupQuery({ operation: 'execution.preflight', workspace }),
    log: dev?.traceLine,
  });
  configApprovals = new ConfigApprovalHost({
    approve: (workspace, fingerprint) => kupQuery({ operation: 'configuration.approve', workspace, fingerprint }),
    confirm: confirmConfigurationApproval,
    currentWorkspace: () => workspaces.current(), log: dev?.traceLine,
  });
  projectSetup = new ProjectSetupHost({
    initialize: (workspace) => kupQuery({ operation: 'project.initialize', workspace }),
    currentWorkspace: () => workspaces.current(), runActive: () => execution.active(), log: dev?.traceLine,
  });
  approvals = new ApprovalHost({
    run: (options) => runKriya({ executable: options.executable, argv: options.argv, cwd: options.cwd, env: options.env, limits: options.limits }), prepare: prepareExecution,
    reviewable: (workspace, launchId, reviewId) => kupQuery({ operation: 'change_approval.review', workspace, launch_id: launchId, review_id: reviewId }),
    currentWorkspace: () => workspaces.current(), log: dev?.traceLine,
  });
  runtimeCommands = new RuntimeCommandHost({
    run: (options) => runKriya({ executable: options.executable, argv: options.argv, cwd: options.cwd, env: options.env, limits: options.limits }), prepare: prepareExecution,
    reviewable: (workspace, launchId, reviewId) => kupQuery({ operation: 'runtime_command.review', workspace, launch_id: launchId, review_id: reviewId }),
    confirm: confirmRuntimeCommands, currentWorkspace: () => workspaces.current(), log: dev?.traceLine,
  });
  workspaces = new WorkspaceRegistry({
    getRecent: () => settings.get('recentWorkspaces'), setRecent: (list) => settings.set('recentWorkspaces', list),
    getCurrent: () => settings.get('workspacePath'), setCurrent: (path) => settings.set('workspacePath', path),
  }, undefined, { legacyInspector: UI_MODE === 'inspector', runActive: () => execution.active() });
  projects = new ProjectHost({
    create: (parent, name) => kupQuery({ operation: 'workspace.create', parent, name }),
    approveCreated: (path) => { workspaces.approveCreated(path); },
    runActive: () => execution.active(), now: () => Date.now(), mintToken: () => `pt-${randomBytes(16).toString('hex')}`,
  });
  const activate = () => {
    if (ownedWindow && !ownedWindow.isDestroyed()) { if (ownedWindow.isMinimized()) ownedWindow.restore(); ownedWindow.show(); ownedWindow.focus(); ownedWindow.webContents.send(MENU_CHANNEL, 'showRun' satisfies MenuCommand); }
  };
  notifications = new NotificationHost({
    // An evidence/dev build never raises a real notification (it runs unattended on the owner's desktop).
    supported: () => dev === null && Notification.isSupported(),
    windowFocused: () => ownedWindow !== null && !ownedWindow.isDestroyed() && ownedWindow.isFocused(),
    currentLaunch: () => execution.state().launch_id, workspacePath: () => workspaces.current(),
    show: (spec, onClick) => {
      const n = new Notification({ title: spec.title, body: spec.body });
      liveNotifications.add(n);
      n.on('click', () => { liveNotifications.delete(n); onClick(); });
      n.on('close', () => { liveNotifications.delete(n); });
      n.show();
    },
    onActivate: activate, now: () => Date.now(),
  });
  workspaces.close(); // a launch starts at the welcome screen: no folder is open until the person opens one
  const command = (name: MenuCommand) => { if (ownedWindow && !ownedWindow.isDestroyed()) ownedWindow.webContents.send(MENU_CHANNEL, name); };
  Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenuTemplate(process.platform === 'darwin', PRODUCT_NAME, { openFolder: () => command('openFolder'), newProject: () => command('newProject') })));
  hardenSession();
  registerIpc();
  const win = createWindow();
  if (dev) await dev.run(win);
});
app.on('window-all-closed', () => app.quit());
// Ends the persistent child cleanly (EOF on its stdin); if the application dies instead, the closed pipe ends the child anyway.
// M6: quitting while a run this host started is in progress asks first; confirming interrupts it the way a terminal's Ctrl-C does (SIGINT: Kriya seals
// the run and the launch as interrupted), with a SIGKILL after the grace period. Nothing is left running unobserved.
/** Asks the person; true = interrupt the run (SIGINT, then SIGKILL after the grace period) and quit. */
async function confirmInterruptAndQuit(): Promise<boolean> {
  if (dev?.captureActive) return true;
  const options = {
    type: 'warning' as const, buttons: ['Interrupt run and quit', 'Keep running'], defaultId: 1, cancelId: 1, message: 'A Kriya run is in progress',
    detail: 'Quitting interrupts it, the same as pressing Ctrl-C in a terminal. Kriya records the interruption; nothing is reported as finished.',
  };
  const parent = ownedWindow && !ownedWindow.isDestroyed() ? ownedWindow : null;
  return (parent ? await dialog.showMessageBox(parent, options) : await dialog.showMessageBox(options)).response === 0;
}
async function interruptAndQuit(): Promise<void> {
  if (!(await confirmInterruptAndQuit())) return;
  quitting = true;
  await execution.shutdown();
  await services.stop();
  app.quit();
}
app.on('before-quit', (event) => {
  if (!quitting && execution && execution.active()) {
    event.preventDefault();
    void interruptAndQuit();
    return;
  }
  void services.stop();
});
app.on('web-contents-created', (_e, contents) => {
  contents.on('will-attach-webview', (e) => e.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
});
