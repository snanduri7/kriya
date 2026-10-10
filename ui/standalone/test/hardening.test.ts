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
      if (f !== 'kriya_process.ts' && f !== 'kriya_service.ts' && f !== 'main.ts') expect(src, f).not.toContain('spawn(');
    }
    for (const f of readdirSync(RENDERER)) {
      const src = read(join(RENDERER, f));
      expect(src, f).not.toMatch(/from 'electron'|require\(|node:|child_process/);
    }
  });
  it('the fixture stand-in is the only executable while matrix protection holds (D-9 gate in main.ts)', () => {
    expect(mainTs).toContain("process.env.KRIYA_UI_ALLOW_REAL_KRIYA === '1'");
    expect(mainTs).toContain('fake_kriya.mjs');
  });
  it('the real kriya is launched only with the child environment policy (child_env.ts), never with the host environment', () => {
    expect(mainTs).toContain('buildKriyaChildEnv(process.env)');
    expect(mainTs).not.toMatch(/env:\s*process\.env|\.\.\.process\.env/);
    const runner = read(join(MAIN, 'kriya_process.ts'));
    expect(runner).not.toMatch(/\.\.\.process\.env/);
    expect(runner).toContain('KRIYA_CHILD_PATH');
  });
  it('every kriya child runs in the validated configuration directory, never in Electron\'s own working directory (F-5)', () => {
    expect((mainTs.match(/cwd: dir\.directory/g) ?? []).length).toBe(3); // the persistent service, the one-shot real child and the stand-in alike
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
    expect(mainTs).toContain("app.on('before-quit', () => { void services.stop(); })");
  });
  it('the scripted chooser exists only while the screenshot driver is active and replaces nothing but the native dialog call', () => {
    expect(mainTs).toContain('CAPTURE_DIR && process.env.KRIYA_UI_CAPTURE_CHOOSE');
    expect(mainTs.indexOf('if (CAPTURE_CHOICES) return CAPTURE_CHOICES.shift()')).toBeGreaterThan(mainTs.indexOf('workspaces.choose(async () => {'));
    expect(mainTs.indexOf('if (CAPTURE_CHOICES) return CAPTURE_CHOICES.shift()')).toBeLessThan(mainTs.indexOf('dialog.showOpenDialog'));
    expect(mainTs.match(/KRIYA_UI_CAPTURE_CHOOSE/g)?.length).toBe(2);
  });
  it('a request that names a folder is checked against the folders the person chose, BEFORE any transport; the native chooser runs only in main', () => {
    expect(mainTs.indexOf('workspaces.refuse(v.value)')).toBeGreaterThan(-1);
    expect(mainTs.indexOf('workspaces.refuse(v.value)')).toBeLessThan(mainTs.indexOf('buildKriyaArgv(v.value)'));
    expect(mainTs).toContain('dialog.showOpenDialog');
    expect(mainTs).toContain('workspaces.refuseSetting(v.value)');
    for (const f of readdirSync(RENDERER)) expect(read(join(RENDERER, f)), f).not.toMatch(/showOpenDialog|webkitdirectory|showDirectoryPicker/);
  });
});
