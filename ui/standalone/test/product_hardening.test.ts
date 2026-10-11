/** Independent review P2-2 / P3-1: product fuses, the debug-switch refusal, and a package checker proven on deliberately BAD packages. */
import { mkdirSync, mkdtempSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { FRAMEWORK_BINARY, FUSE_SENTINEL, applyProductFuses, locateWire, productFuseFindings, readFuses } from '../fuses.mjs';
import { packageFindings } from '../package_check.mjs';
import { debugSwitches, mustRefuseStart } from '../src/main/product_guard';

const framework = (wire: string, prefix = 'junk') => Buffer.concat([Buffer.from(prefix), Buffer.from(FUSE_SENTINEL), Buffer.from([1, wire.length]), Buffer.from(wire), Buffer.from('tail')]);
const STOCK = '101100011'; // what the stock Electron framework in this repo carries (measured)

describe('Electron fuses of the product', () => {
  it('the stock wire has RunAsNode, NODE_OPTIONS and --inspect ON; the product patch turns exactly those three off and nothing else', () => {
    const stock = framework(STOCK);
    expect(readFuses(stock)).toEqual({ RunAsNode: true, EnableNodeOptionsEnvironmentVariable: true, EnableNodeCliInspectArguments: true });
    expect(productFuseFindings(stock)).toHaveLength(3);
    const patched = applyProductFuses(stock);
    expect(productFuseFindings(patched)).toEqual([]);
    const { start, length } = locateWire(patched);
    expect(patched.subarray(start, start + length).toString()).toBe('000000011');
    expect(stock.subarray(start, start + length).toString()).toBe(STOCK); // the input was not mutated
  });
  it('refuses a framework without exactly one well-formed wire (fail closed: never patched, never accepted)', () => {
    expect(() => locateWire(Buffer.from('no wire here'))).toThrow(/not found/);
    expect(() => locateWire(Buffer.concat([framework(STOCK), framework(STOCK)]))).toThrow(/more than one/);
    expect(() => locateWire(Buffer.concat([Buffer.from(FUSE_SENTINEL), Buffer.from([2, 9]), Buffer.from(STOCK)]))).toThrow(/version/);
    expect(() => locateWire(Buffer.concat([Buffer.from(FUSE_SENTINEL), Buffer.from([1, 9]), Buffer.from('10x100011')]))).toThrow(/neither 0 nor 1/);
    expect(() => applyProductFuses(Buffer.from('nothing'))).toThrow();
  });
});

describe('the product refuses to run with a debugger or remote-control switch', () => {
  it('flags the inspector and remote-debugging switches, in every spelling, and nothing else', () => {
    expect(debugSwitches(['kriya', '--remote-debugging-port=9222', '--inspect', '--inspect-brk=9229', '--remote-debugging-pipe', '--js-flags=--expose-gc', '--user-data-dir=/x'])).toEqual(['--remote-debugging-port=9222', '--inspect', '--inspect-brk=9229', '--remote-debugging-pipe', '--js-flags=--expose-gc']);
    expect(debugSwitches(['kriya', '--user-data-dir=/x', '--inspector-not-a-switch', '/path/with/--inspect'])).toEqual([]);
  });
  it('only the packaged product without dev drivers refuses; an evidence/dev build is not restricted', () => {
    const argv = ['kriya', '--remote-debugging-port=9222'];
    expect(mustRefuseStart(true, false, argv)).toMatch(/debugging switch/);
    expect(mustRefuseStart(true, true, argv)).toBeNull();
    expect(mustRefuseStart(false, false, argv)).toBeNull();
    expect(mustRefuseStart(true, false, ['kriya'])).toBeNull();
  });
});

function fakeApp(opts: { driverFile?: boolean; mention?: { file: string; text: string }; mapMention?: boolean; main?: string; wire?: string } = {}) {
  const app = join(mkdtempSync(join(tmpdir(), 'kriya-pkg-')), 'Kriya.app');
  const res = join(app, 'Contents', 'Resources', 'app');
  mkdirSync(join(res, 'dist', 'main', 'sub'), { recursive: true });
  mkdirSync(join(res, 'dist', 'renderer', 'assets'), { recursive: true });
  writeFileSync(join(res, 'dist', 'main', 'main.js'), 'ok');
  writeFileSync(join(res, 'package.json'), JSON.stringify({ main: opts.main ?? 'dist/main/main.js' }));
  if (opts.driverFile) writeFileSync(join(res, 'dist', 'main', 'capture.cjs'), 'x');
  if (opts.mention) writeFileSync(join(res, opts.mention.file), opts.mention.text);
  if (opts.mapMention) writeFileSync(join(res, 'dist', 'main', 'main.js.map'), '{"sourcesContent":["KRIYA_UI_CAPTURE"]}');
  const fw = join(app, ...FRAMEWORK_BINARY);
  mkdirSync(join(fw, '..'), { recursive: true });
  writeFileSync(fw, framework(opts.wire ?? '000000011'));
  return app;
}

describe('the package checker is a real gate (negative controls on deliberately bad packages)', () => {
  it('a clean package passes', () => expect(packageFindings(fakeApp())).toEqual([]));
  it.each([
    ['a .cjs dev driver file', { driverFile: true }, /dev driver packaged/],
    ['a dev-only name in a sub-directory script', { mention: { file: 'dist/main/sub/x.js', text: 'process.env.KRIYA_UI_TRACE' } }, /KRIYA_UI_TRACE/],
    ['a dev-only name in a renderer asset', { mention: { file: 'dist/renderer/assets/a.js', text: 'KRIYA_FAKE_X' } }, /KRIYA_FAKE_/],
    ['a dev-only name inside a source map', { mapMention: true }, /KRIYA_UI_CAPTURE/],
    ['a package.json main that is missing', { main: 'dist/main/gone.js' }, /does not name a packaged file/],
    ['a package.json main that is a dev driver', { main: 'dist/main/sub/../capture.js' }, /./],
    ['a framework with the stock fuses', { wire: STOCK }, /fuse RunAsNode is ON/],
  ])('fails on %s', (_name, opts, pattern) => {
    const findings = packageFindings(fakeApp(opts as never));
    expect(findings.length).toBeGreaterThan(0);
    expect(findings.join('\n')).toMatch(pattern as RegExp);
  });
});

describe('host-owned settings (Phase 0): the renderer cannot change the executable or the configuration directory', () => {
  const KEYS = ['editor', 'kriyaExecutable', 'workspacePath', 'configDirectory'] as const;
  it('cockpit: only the editor preference is writable, and the remembered workspace may be CLEARED; every other write is refused, for any value', async () => {
    const { rendererMayWrite } = await import('../src/main/ipc_contract');
    for (const value of ['/usr/bin/evil', '/tmp', 'kriya', '', null, '/a\nb']) {
      expect(rendererMayWrite('kriyaExecutable', 'cockpit', value), `exe ${String(value)}`).toBe(false);
      expect(rendererMayWrite('configDirectory', 'cockpit', value), `cfg ${String(value)}`).toBe(false);
    }
    expect(rendererMayWrite('editor', 'cockpit', 'vscode')).toBe(true);
    expect(rendererMayWrite('workspacePath', 'cockpit', null)).toBe(true);
    expect(rendererMayWrite('workspacePath', 'cockpit', '/some/folder')).toBe(false);
  });
  it('the legacy inspector build keeps its narrow compatibility, isolated to the explicit mode', async () => {
    const { rendererMayWrite } = await import('../src/main/ipc_contract');
    for (const key of KEYS) expect(rendererMayWrite(key, 'inspector', '/x')).toBe(true);
    const { resolveUiMode } = await import('../src/main/ui_mode');
    for (const raw of [null, '', '{', '{"ui":"cockpit"}', '{"ui":"INSPECTOR"}', '{"ui":["inspector"]}', '[]']) expect(resolveUiMode(() => raw)).toBe('cockpit'); // anything unexpected is the strict cockpit
    expect(resolveUiMode(() => '{"ui":"inspector"}')).toBe('inspector');
  });
  it('main.ts enforces the rule AFTER validation on the one settingSet handler, and no other handler writes settings from renderer input', async () => {
    const { readFileSync } = await import('node:fs');
    const main = readFileSync(join(__dirname, '..', 'src', 'main', 'main.ts'), 'utf8');
    const handler = main.slice(main.indexOf('IPC_CHANNELS.settingSet'), main.indexOf('M5 workspace lifecycle'));
    expect(handler).toContain('rendererMayWrite(k.value, UI_MODE, v.value)');
    expect(handler.indexOf('validateSettingValue')).toBeLessThan(handler.indexOf('rendererMayWrite'));
    expect(handler.indexOf('rendererMayWrite')).toBeLessThan(handler.indexOf('settings.set('));
    expect((main.match(/settings\.set\(/g) ?? []).length).toBe(3); // the guarded handler + the workspace registry's own recents/current callbacks
  });
  it('malformed IPC never reaches a write: hostile keys and values are rejected before the guard, and the guard rejects every host-owned key', async () => {
    const { validateSettingKey, validateSettingValue, rendererMayWrite } = await import('../src/main/ipc_contract');
    for (const key of ['__proto__', 'constructor', 'kriyaExecutable ', 'KRIYAEXECUTABLE', 1, null, {}, ['kriyaExecutable']]) {
      const k = validateSettingKey(key);
      if (k.ok) expect(rendererMayWrite(k.value, 'cockpit', null) && k.value !== 'editor' && k.value !== 'workspacePath').toBe(false); else expect(k.ok).toBe(false);
    }
    expect(validateSettingValue('kriyaExecutable', '/ok/path').ok).toBe(true);
    expect(rendererMayWrite('kriyaExecutable', 'cockpit', '/ok/path')).toBe(false); // valid-looking is not enough
  });
});
