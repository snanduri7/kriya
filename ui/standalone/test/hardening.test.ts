import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { CONTENT_SECURITY_POLICY, HARDENING_BEHAVIOURS, PERMISSION_DECISION, WEB_PREFERENCES } from '../src/main/hardening';
import { ALLOWED_CHANNELS, IPC_CHANNELS } from '../src/main/ipc_contract';

const MAIN = join(__dirname, '..', 'src', 'main');
const RENDERER = join(__dirname, '..', 'src', 'renderer');
const read = (f: string) => readFileSync(f, 'utf8');
const mainTs = read(join(MAIN, 'main.ts'));
const preloadTs = read(join(MAIN, 'preload.ts'));

describe('gate A-1 hardening settings (every one checked)', () => {
  it('BrowserWindow webPreferences are the hardened set', () => {
    expect(WEB_PREFERENCES).toMatchObject({ contextIsolation: true, sandbox: true, nodeIntegration: false, nodeIntegrationInWorker: false, nodeIntegrationInSubFrames: false, webviewTag: false, webSecurity: true, allowRunningInsecureContent: false, experimentalFeatures: false, devTools: false });
    expect(mainTs).toContain('...WEB_PREFERENCES');
  });
  it('CSP is strict: no remote content, no inline script, no eval, no frames, no forms', () => {
    for (const d of ["default-src 'none'", "script-src 'self'", "style-src 'self'", "connect-src 'none'", "object-src 'none'", "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'", "frame-src 'none'"]) expect(CONTENT_SECURITY_POLICY).toContain(d);
    expect(CONTENT_SECURITY_POLICY).not.toMatch(/unsafe-inline|unsafe-eval|https?:|\*/);
    const html = read(join(RENDERER, 'index.html'));
    expect(html).toContain('http-equiv="Content-Security-Policy"');
    expect(html).toContain("default-src 'none'");
  });
  it('every permission is denied and every behaviour is wired in main.ts', () => {
    expect(PERMISSION_DECISION).toBe(false);
    for (const b of HARDENING_BEHAVIOURS) expect(mainTs, b).toContain(b);
    expect(mainTs).toContain("setWindowOpenHandler(() => ({ action: 'deny' }))");
    expect(mainTs).toContain("on('will-navigate', (e) => e.preventDefault())");
    expect(mainTs).toContain("cancel: !details.url.startsWith('file://')");
  });
  it('the preload exposes only the typed allowlist: every invoke uses a fixed channel constant, one exposeInMainWorld', () => {
    const invokes = [...preloadTs.matchAll(/ipcRenderer\.invoke\(([^,)]+)/g)].map((m) => m[1]!.trim());
    expect(invokes.length).toBe(ALLOWED_CHANNELS.length);
    for (const i of invokes) expect(i).toMatch(/^IPC_CHANNELS\.[a-zA-Z]+$/);
    expect((preloadTs.match(/exposeInMainWorld/g) ?? []).length).toBe(1);
    // Nothing can be SENT to the main process except through a fixed invoke channel; the single inbound subscription is the menu command channel.
    expect(preloadTs).not.toMatch(/ipcRenderer\.(send|sendSync|sendTo|sendToHost|postMessage)\(/);
    expect([...preloadTs.matchAll(/ipcRenderer\.on\(([^,)]+)/g)].map((m) => m[1]!.trim())).toEqual(['MENU_CHANNEL']);
    expect(preloadTs).toContain('MENU_COMMANDS as readonly unknown[]).includes(command)');
    expect(preloadTs).not.toMatch(/listener\((_event|event)/); // the event object is never forwarded to the page
    expect(preloadTs).not.toMatch(/require\(|process\.env|child_process|readFile/);
    for (const ch of ALLOWED_CHANNELS) expect(mainTs).toContain(`ipcMain.handle(IPC_CHANNELS.${Object.entries(IPC_CHANNELS).find(([, v]) => v === ch)![0]}`);
  });
  it('process spawning lives only in the main process, never with a shell; no listener, no Kriya state read', () => {
    const files = readdirSync(MAIN).map((f) => [f, read(join(MAIN, f))] as const);
    for (const [f, src] of files) {
      expect(src, f).not.toMatch(/shell:\s*true/);
      expect(src, f).not.toMatch(/\bexec(Sync|File)?\(/);
      expect(src, f).not.toMatch(/from 'node:(http|https|net|dgram)'|\.listen\(/);
      expect(src, f).not.toMatch(/\.kriya\/|traces\.db|sqlite/i);
      if (f !== 'kriya_process.ts' && f !== 'kriya_service.ts' && f !== 'execution_process.ts' && f !== 'main.ts') expect(src, f).not.toContain('spawn(');
    }
    for (const f of readdirSync(RENDERER)) {
      const src = read(join(RENDERER, f));
      expect(src, f).not.toMatch(/from 'electron'|require\(|node:|child_process/);
    }
  });
  it('the fixture stand-in and its D-9 gate live ONLY in the dev drivers module; main.ts knows neither the environment variable nor the stand-in script', () => {
    const dev = read(join(MAIN, 'dev_drivers.ts'));
    expect(dev).toContain("env.KRIYA_UI_ALLOW_REAL_KRIYA === '1'");
    expect(dev).toContain('fake_kriya.mjs');
    expect(mainTs).toContain('const ALLOW_REAL = dev?.allowReal ?? false;');
    expect(mainTs).not.toMatch(/KRIYA_UI_ALLOW_REAL_KRIYA|fake_kriya|fake-kriya|KRIYA_FAKE_/);
  });
  it('the real kriya is launched only with the child environment policy (child_env.ts), never with the host environment', () => {
    expect(mainTs).toContain('buildKriyaChildEnv(process.env)');
    expect(mainTs).not.toMatch(/env:\s*process\.env|\.\.\.process\.env/);
    const runner = read(join(MAIN, 'kriya_process.ts'));
    expect(runner).not.toMatch(/\.\.\.process\.env/);
    expect(runner).toContain('KRIYA_CHILD_PATH');
  });
  it('every kriya child runs in the validated configuration directory, never in Electron\'s own working directory (F-5)', () => {
    expect((mainTs.match(/cwd: dir\.directory/g) ?? []).length).toBe(3); // the persistent service (query path and the host's own preflight ask) and the one-shot real child
    expect(mainTs).toContain('dev.runFake(argv, dir.directory)'); // the development stand-in runs in the same validated directory
    expect(mainTs).toContain("resolveConfigDirectory(settings.get('configDirectory'), process.env, isDirectory)");
    expect(mainTs).not.toMatch(/process\.cwd\(\)/);
    expect(mainTs).toContain('configDirectorySource');
  });
});

describe('M5 persistent transport and workspace authority (hardening)', () => {
  const serviceTs = read(join(MAIN, 'kriya_service.ts'));
  it('the persistent service spawns exactly the configured executable with the fixed kup-stdio argv, without a shell, with a fixed environment', () => {
    expect((serviceTs.match(/spawn\(/g) ?? []).length).toBe(1);
    expect(serviceTs).toContain("export const SERVICE_ARGV: readonly string[] = ['kup-stdio'];");
    expect(serviceTs).toContain('shell: false');
    expect(serviceTs).toContain('env: { PATH: KRIYA_CHILD_PATH, ...(this.options.env ?? {}) }');
    expect(serviceTs).not.toMatch(/process\.env|\.\.\.process/);
    expect(serviceTs).toContain('toServiceRequest(id, request)'); // nothing is written to the child that did not pass the allowlist table
  });
  it('real-mode queries use the service by default; the one-shot command path stays available and is chosen only by KRIYA_UI_TRANSPORT=spawn', () => {
    expect(mainTs).toContain("process.env.KRIYA_UI_TRANSPORT === 'spawn' ? 'spawn' : 'service'");
    expect(mainTs).toContain("if (TRANSPORT === 'service')");
    expect(mainTs).toContain('runKriya({ executable: host.executable, argv, env: childEnv.env, cwd: dir.directory })');
    expect(mainTs).toContain("app.on('before-quit', (event) => {");
    expect(mainTs).toContain('void services.stop();');
  });
  it('the scripted chooser exists only in the dev drivers module (absent from the product), active only while the screenshot driver is, and replaces nothing but the native dialog call', () => {
    const dev = read(join(MAIN, 'dev_drivers.ts'));
    expect(dev).toContain('CAPTURE_DIR && env.KRIYA_UI_CAPTURE_CHOOSE');
    expect(mainTs).not.toMatch(/KRIYA_UI_CAPTURE/);
    const use = mainTs.indexOf('if (dev?.captureChoices) return dev.captureChoices.shift()');
    expect(use).toBeGreaterThan(mainTs.indexOf('workspaces.choose(async () => {'));
    expect(use).toBeLessThan(mainTs.indexOf('dialog.showOpenDialog'));
  });
  it('a request that names a folder is checked against the folders the person chose, BEFORE any transport; the native chooser runs only in main', () => {
    expect(mainTs.indexOf('workspaces.refuse(v.value)')).toBeGreaterThan(-1);
    expect(mainTs.indexOf('workspaces.refuse(v.value)')).toBeLessThan(mainTs.indexOf('buildKriyaArgv(v.value)'));
    expect(mainTs).toContain('dialog.showOpenDialog');
    expect(mainTs).toContain('workspaces.refuseSetting(v.value)');
    for (const f of readdirSync(RENDERER)) expect(read(join(RENDERER, f)), f).not.toMatch(/showOpenDialog|webkitdirectory|showDirectoryPicker/);
  });
});

describe('M6 execution (hardening): Start Run is the only execution action and it is not a command endpoint', () => {
  const hostTs = read(join(MAIN, 'execution_host.ts'));
  const processTs = read(join(MAIN, 'execution_process.ts'));
  it('the preload exposes only typed execution messages (start, state, and the closed-kind notification request) - no command, no argv, no generic invoke', () => {
    expect(preloadTs).toContain('startRun: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.startRun, request)');
    expect(preloadTs).toContain('executionState: () => ipcRenderer.invoke(IPC_CHANNELS.executionState)');
    expect(preloadTs).not.toMatch(/ipcRenderer\.invoke\((?!IPC_CHANNELS\.)/);
    expect(preloadTs).not.toMatch(/spawn|child_process|shell|exec\(/i);
    expect(preloadTs).toContain('notifyRun: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.notifyRun, request)');
    expect(Object.keys(IPC_CHANNELS).filter((k) => /run|exec/i.test(k)).sort()).toEqual(['decideRuntimeCommand', 'executionState', 'notifyRun', 'startRun']); // (decideApproval, decideRuntimeCommand [own host test] and the project steps are classified in their own tests)
  });
  it('both execution handlers verify the sender, and startRun hands the raw request to the host (which validates it) - the handler builds nothing', () => {
    for (const name of ['startRun', 'executionState']) {
      const at = mainTs.indexOf(`ipcMain.handle(IPC_CHANNELS.${name}`);
      expect(at, name).toBeGreaterThan(-1);
      expect(mainTs.slice(at, at + 220), name).toContain("if (!fromOwnedRenderer(event)) throw new Error('unexpected sender')");
    }
    expect(mainTs).toContain('await execution.start(raw)');
  });
  it('the execution process is spawned in exactly one place: no shell, the validated argv, the workspace as cwd, the prepared environment only', () => {
    expect((processTs.match(/spawn\(/g) ?? []).length).toBe(1);
    expect(processTs).toContain('shell: false');
    expect(processTs).toContain('[...invocation.argv]');
    expect(processTs).toContain('cwd: invocation.cwd');
    expect(processTs).toContain('env: { ...invocation.env }');
    expect(processTs).not.toMatch(/process\.env|\.\.\.process/);
    expect(mainTs).toContain('launch: launchExecution');
    expect(mainTs).toContain('currentWorkspace: () => workspaces.current()'); // the run's folder is the registry's open workspace, never a renderer-supplied one
  });
  it('the host module is pure: no Electron, no child_process, no filesystem or network, no environment', () => {
    expect(hostTs).not.toMatch(/from 'electron'|from 'node:(child_process|fs|net|http|https|dgram)'|require\(|process\.env/);
    expect(hostTs).not.toMatch(/spawn\(/);
  });
  it('the execution command line exists in one place only, and nothing in main or the preload can add to it', () => {
    expect(hostTs).toContain("return ['generate', '--json', '--launch-id', launchId];");
    for (const f of readdirSync(MAIN)) {
      if (f === 'execution_host.ts') continue;
      const src = read(join(MAIN, f));
      // `--launch-id` is also an option of the review-decision command (kriya_argv.ts, below); `generate`, auto-approval and trust options appear nowhere else.
      expect(src, f).not.toMatch(/'generate'|"generate"|--yes|--knowledge-policy|--trust-file/);
      if (f !== 'kriya_argv.ts') expect(src, f).not.toMatch(/--launch-id/);
    }
  });
  it('the review decision is ONE typed action: one command line in one place, validated twice, no option that could approve more than the one review (GUI final)', () => {
    const argv = read(join(MAIN, 'kriya_argv.ts'));
    expect(argv).toContain("const argv = ['launch-decision', '--launch-id', request.launch_id, '--review-id', request.review_id, '--candidate-digest', request.candidate_digest, '--decision', request.decision];");
    for (const f of readdirSync(MAIN)) {
      const src = read(join(MAIN, f));
      expect(src, f).not.toMatch(/--yes|--always|--auto-approve|--autonomy|(['"])-y\1/);
      if (f !== 'kriya_argv.ts' && f !== 'approval_host.ts') expect(src, f).not.toContain('launch-decision');
    }
    const host = read(join(MAIN, 'approval_host.ts'));
    expect(host).toContain('validateDecideApproval(raw)');
    expect(host).toContain('buildLaunchDecisionArgv(request.value)');
    expect(host).toContain('this.deps.currentWorkspace() !== workspace');
    expect(host).not.toMatch(/from '(node:)?child_process'|from 'electron'|from '(node:)?fs'|writeFile|appendFile|mkdir/); // no process, no Electron, no file written by the proxy
    expect(mainTs).toContain('return approvals.decide(raw);');
    expect(read(join(MAIN, 'preload.ts'))).toContain('decideApproval: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.decideApproval, request)');
    expect(read(join(MAIN, 'ipc_contract.ts'))).toContain("Object.keys(v).sort().join() !== 'candidate_digest,decision,launch_id,review_id,workspace'");
  });
  it('the runtime-command decision is ONE typed action with a native confirmation in main: validated, workspace-checked, no process/Electron/file in the proxy (Demo V2)', () => {
    const host = read(join(MAIN, 'runtime_command_host.ts'));
    expect(host).toContain('validateDecideRuntimeCommand(raw)');
    expect(host).toContain('buildRuntimeCommandDecisionArgv(request.value)');
    expect(host).toContain('this.deps.currentWorkspace() !== workspace');
    expect(host).toContain('this.deps.reviewable(');
    expect(host).not.toMatch(/from '(node:)?child_process'|from 'electron'|from '(node:)?fs'|writeFile|appendFile|mkdir/);
    expect(mainTs).toContain('return runtimeCommands.decide(raw);');
    expect(mainTs).toContain("reviewable: (workspace, launchId, reviewId) => kupQuery({ operation: 'runtime_command.review', workspace, launch_id: launchId, review_id: reviewId })");
    expect(read(join(MAIN, 'preload.ts'))).toContain('decideRuntimeCommand: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.decideRuntimeCommand, request)');
    expect(read(join(MAIN, 'ipc_contract.ts'))).toContain("Object.keys(v).sort().join() !== 'command_fingerprint,decision,launch_id,review_id,workspace'");
    // the native text is fixed in main: the confirmation function takes no argument and builds the dialog from the constant
    expect(mainTs).toContain('async function confirmRuntimeCommands(): Promise<boolean>');
    expect(mainTs).toContain('const options = runtimeConfirmationDialog();');
  });
  it('the launch id is minted in main and never accepted from the renderer', () => {
    expect(mainTs).toContain('mintLaunchId,');
    expect(mainTs).toContain('function mintLaunchId()');
    expect(hostTs).toContain('this.deps.mintLaunchId()');
    expect(read(join(MAIN, 'ipc_contract.ts'))).toContain("Object.keys(v).sort().join() !== 'goal,workspace'");
  });
  it('quitting while a run exists asks first and interrupts through the host (SIGINT then SIGKILL); nothing is left unobserved', () => {
    expect(mainTs).toContain('execution.active()');
    expect(mainTs).toContain('await execution.shutdown()');
    expect(mainTs).toContain("buttons: ['Interrupt run and quit', 'Keep running']");
    expect(hostTs).toContain("child.kill('SIGINT')");
    expect(hostTs).toContain("child.kill('SIGKILL')");
  });
  it('closing the window during a run asks the same question as quitting and keeps the window until it is answered (reviewer P2-1)', () => {
    expect(mainTs).toContain("win.on('close', (event) => {");
    expect(mainTs).toContain('if (!quitting && execution && execution.active()) { event.preventDefault(); void interruptAndQuit(); }');
    expect(mainTs).toContain('async function interruptAndQuit()');
    expect(mainTs.match(/confirmInterruptAndQuit\(\)/g)?.length).toBeGreaterThanOrEqual(2);
  });
  it('packaging refuses a fixture-bearing or non-cockpit build (reviewer P3-7)', () => {
    const mac = read(join(__dirname, '..', 'mac-app.mjs'));
    expect(mac).toContain("scripts-check-bundle.mjs");
    expect(mac).toContain("if (mode !== 'cockpit') throw");
  });
  it('the registry gets the run-active lock, and the M1 compatibility is switched on only by the build marker', () => {
    expect(mainTs).toContain("{ legacyInspector: UI_MODE === 'inspector', runActive: () => execution.active() }");
    expect(mainTs).toContain('resolveUiMode(');
  });
});
