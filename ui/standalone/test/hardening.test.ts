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
    expect(preloadTs).not.toMatch(/ipcRenderer\.(on|send|sendSync|postMessage)\(/);
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
      if (f !== 'kriya_process.ts' && f !== 'main.ts') expect(src, f).not.toContain('spawn(');
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
});
