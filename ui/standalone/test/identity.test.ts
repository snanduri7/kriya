/** Product identity: the app is Kriya wherever a user can see a name. Electron's stock names (menu-bar title, About/Hide/Quit, window title,
 * bundle, helper apps) must never be what the user sees. The main-process half is pure data checked here; the bundle half is checked on the real
 * packaged bundle (macOS only) and against the stock Electron bundle as a negative control. */
import { existsSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PRODUCT_NAME, WINDOW_TITLE, aboutPanelOptions, applicationMenuTemplate, resolveUserData } from '../src/main/identity';

const ROOT = join(__dirname, '..');
const read = (...p: string[]) => readFileSync(join(ROOT, ...p), 'utf8');
const labels = (template: unknown): string[] => JSON.stringify(template).match(/"label":"[^"]*"/g)?.map((l) => l.slice(9, -1)) ?? [];

describe('main-process identity', () => {
  it('the product is Kriya everywhere it is named', () => {
    expect([PRODUCT_NAME, WINDOW_TITLE]).toEqual(['Kriya', 'Kriya']);
    const pkg = JSON.parse(read('package.json')) as { productName?: string; scripts: Record<string, string> };
    expect(pkg.productName).toBe('Kriya');
    expect(pkg.scripts.start).toContain('mac-app.mjs start');
    expect(pkg.scripts['package:mac']).toContain('mac-app.mjs package');
    expect(read('src', 'renderer', 'index.html')).toContain('<title>Kriya</title>');
  });

  it('the macOS application menu says About Kriya, Hide Kriya and Quit Kriya, and the word Electron appears nowhere in it', () => {
    const mac = applicationMenuTemplate(true);
    expect(labels(mac)).toEqual(expect.arrayContaining(['Kriya', 'About Kriya', 'Hide Kriya', 'Quit Kriya']));
    expect(JSON.stringify(mac)).not.toMatch(/electron/i);
    expect(JSON.stringify(applicationMenuTemplate(false))).not.toMatch(/electron/i);
    expect(labels(applicationMenuTemplate(true, 'Other'))).toContain('Quit Other'); // derived from the name, not from the binary
  });

  it('File > Open Folder... exists on every platform with Cmd/Ctrl+O, calls only the injected command, and the word Electron appears nowhere', () => {
    for (const mac of [true, false]) {
      const calls: string[] = [];
      const template = applicationMenuTemplate(mac, 'Kriya', { openFolder: () => calls.push('openFolder') });
      const file = template.find((m) => m.label === 'File');
      const item = (file?.submenu as Array<{ label?: string; accelerator?: string; click?: () => void }>).find((i) => i.label === 'Open Folder…');
      expect(item?.accelerator, `mac=${mac}`).toBe('CmdOrCtrl+O');
      item?.click?.();
      expect(calls).toEqual(['openFolder']);
      expect(JSON.stringify(template)).not.toMatch(/electron/i);
    }
    expect(() => applicationMenuTemplate(true)).not.toThrow(); // the default menu has an inert command, never undefined
    const withoutCommand = (applicationMenuTemplate(true).find((m) => m.label === 'File')?.submenu as Array<{ click?: () => void }>)[0]!;
    expect(withoutCommand.click?.()).toBeUndefined();
  });

  it('the About panel names Kriya and the build version, never Electron', () => {
    expect(aboutPanelOptions('0.0.1')).toEqual({ applicationName: 'Kriya', applicationVersion: '0.0.1', version: '0.0.1', copyright: 'Kriya' });
  });

  it('renaming the product does not orphan the settings earlier builds wrote: userData is pinned, and an explicit --user-data-dir wins', () => {
    expect(resolveUserData('/Users/x/Library/Application Support', false)).toBe('/Users/x/Library/Application Support/@kriya-ui/standalone');
    expect(resolveUserData('/Users/x/Library/Application Support', true)).toBeNull();
  });

  it('main.ts sets the identity before anything reads the name, titles the window, blocks page-driven renames and installs the menu', () => {
    const main = read('src', 'main', 'main.ts');
    expect(main.indexOf('app.setName(PRODUCT_NAME)')).toBeGreaterThan(-1);
    expect(main.indexOf('app.setName(PRODUCT_NAME)')).toBeLessThan(main.indexOf('const MEASURE'));
    expect(main).toContain('title: WINDOW_TITLE');
    expect(main).toContain("win.on('page-title-updated', (e) => e.preventDefault())");
    expect(main).toContain('Menu.setApplicationMenu(Menu.buildFromTemplate(applicationMenuTemplate(');
    expect(main).toContain("{ openFolder: () => command('openFolder') }");
    expect(main).toContain('app.setAboutPanelOptions(aboutPanelOptions(');
    expect(main).not.toMatch(/run inspector/);
  });
});

describe.skipIf(process.platform !== 'darwin')('bundle identity (macOS)', () => {
  it('the verifier rejects the stock Electron bundle (negative control) and names what is wrong', async () => {
    const { verifyBundle } = (await import('../mac-app.mjs')) as { verifyBundle(app: string): string[] };
    const stock = join(ROOT, '..', 'node_modules', 'electron', 'dist', 'Electron.app');
    if (!existsSync(stock)) return;
    const findings = verifyBundle(stock);
    expect(findings.join('\n')).toMatch(/CFBundleName is "Electron", not Kriya/);
    expect(findings.join('\n')).toMatch(/helper app still named Electron Helper/);
  });

  it.skipIf(!existsSync(join(__dirname, '..', 'out', 'Kriya.app')))('the packaged Kriya.app has no Electron identity left (run `npm run package:mac` first)', async () => {
    const { verifyBundle } = (await import('../mac-app.mjs')) as { verifyBundle(app: string): string[] };
    expect(verifyBundle(join(ROOT, 'out', 'Kriya.app')).filter((f) => !f.startsWith('WARNING'))).toEqual([]);
  });
});
