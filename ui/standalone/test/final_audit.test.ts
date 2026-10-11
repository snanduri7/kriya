/** GUI v1 hardening, Phase A: static architecture/security guards that were not yet pinned by an automated test. Each one fails on the violation it names. */
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { IPC_CHANNELS, ALLOWED_CHANNELS } from '../src/main/ipc_contract';

const ROOT = join(__dirname, '..');
const MAIN = join(ROOT, 'src', 'main');
const read = (...p: string[]) => readFileSync(join(...p), 'utf8');
const mainFiles = readdirSync(MAIN).filter((f) => f.endsWith('.ts'));
const DEV_ONLY = new Set(['dev_drivers.ts', 'capture.ts', 'measure.ts', 'soak.ts']);
const productMain = mainFiles.filter((f) => !DEV_ONLY.has(f));

describe('Electron main (product files) - items 2, 7, 10, 14', () => {
  it('main never parses Kriya trace or evidence content: no recorder vocabulary appears in any product main file', () => {
    const vocabulary = /candidate\.change|gate\.result|attempt\.opened|attempt\.closed|model\.request|model\.response|run_events|evidence_records|developer\.parse|unit\.opened|unit\.closed|run\.closed|payload\./;
    for (const f of productMain) { // operation NAMES (evidence.records) are request grammar; no file reads a response's records, kinds, seqs or payloads
      const src = read(MAIN, f).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''); // code, not comments
      expect(src, f).not.toMatch(vocabulary);
    }
  });
  it('the IPC surface is exactly the typed allowlist - a new channel fails here until it is classified', () => {
    expect(Object.keys(IPC_CHANNELS).sort()).toEqual(['chooseProjectParent', 'chooseWorkspace', 'clipboardWrite', 'createProject', 'decideApproval', 'executionState', 'forgetWorkspace', 'hostInfo', 'openInIde', 'query', 'recentWorkspaces', 'rememberWorkspace', 'settingGet', 'settingSet', 'startRun']);
    expect(ALLOWED_CHANNELS).toHaveLength(15);
    const handled = [...read(MAIN, 'main.ts').matchAll(/ipcMain\.handle\(IPC_CHANNELS\.(\w+)/g)].map((m) => m[1]).sort();
    expect(handled).toEqual(Object.keys(IPC_CHANNELS).sort()); // every channel has exactly one handler, and nothing is handled that is not in the allowlist
    expect(read(MAIN, 'main.ts')).not.toMatch(/ipcMain\.(on|once|handleOnce)\(/);
  });
  it('no shell anywhere: nothing spawns through a shell and no exec/eval-style API is used in a product main file', () => {
    for (const f of productMain) {
      const src = read(MAIN, f).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
      expect(src, f).not.toMatch(/shell:\s*true|\bexecSync\(|\bexec\(|execFile\(|\beval\(|new Function\(|child_process'\)\.exec\b/);
      expect(src, f).not.toMatch(/`\$\{[^}]*\}[^`]*`\s*,\s*\{\s*shell/);
    }
  });
  it('Electron cannot broaden content visibility or any Kriya configuration: no main file names a Kriya config field, writes configuration, or passes config overrides', () => {
    for (const f of productMain) {
      const src = read(MAIN, f).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '');
      expect(src, f).not.toMatch(/content_visibility|attempt_recorder|KRIYA_CONFIG|--config|--trust-file|autonomy|sensitive_paths|SECURITY_AUTHORITY/);
    }
  });
});

describe('the shipped product package - item 6', () => {
  const product = join(ROOT, 'out', 'Kriya.app');
  it.skipIf(!existsSync(product))('carries no dev driver, scripted chooser, fixture stand-in, measurement code or capture environment variable', () => {
    const out = execFileSync(process.execPath, [join(ROOT, 'scripts-check-bundle.mjs'), '--package', product], { encoding: 'utf8' });
    expect(out).toContain('check-bundle --package: OK');
  });
  it('the CLI check delegates to the tested package_check rules, the packager runs it on every product package, and the product package is patched with the fuses (the rules themselves have real bad-package negative controls in product_hardening.test.ts)', () => {
    expect(read(ROOT, 'scripts-check-bundle.mjs')).toContain("import('./package_check.mjs')");
    const packager = read(ROOT, 'mac-app.mjs');
    expect(packager).toContain("if (!evidence) execFileSync(process.execPath, [join(HERE, 'scripts-check-bundle.mjs'), '--package'");
    expect(packager).toContain('applyProductFuses(');
    expect(packager).toContain("--evidence requires an explicit --out");
  });
  it('main.ts reaches the dev drivers only through one guarded require, so a package without the file has none of their behaviour', () => {
    const main = read(MAIN, 'main.ts');
    expect(main.match(/require\('\.\/dev_drivers'\)/g)).toHaveLength(1);
    expect(main).toContain("function loadDevDrivers(): DevDrivers | null {\n  try { return (require('./dev_drivers') as typeof import('./dev_drivers')).createDevDrivers(process.env); } catch { return null; }\n}");
    for (const f of productMain.filter((x) => x !== 'main.ts')) expect(read(MAIN, f), f).not.toMatch(/dev_drivers|capture|runSoak|runMeasurement/);
  });

  it('the host never decides greenfield versus brownfield: the words appear in no product main file (Kriya\'s recorded facts decide; the cockpit has one path)', () => {
    for (const f of productMain) expect(read(MAIN, f).replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, ''), f).not.toMatch(/greenfield|brownfield/i);
  });

  it('the host builds its error messages WITHOUT stderr: a child\'s stderr tail travels in its own field (shown only behind a Technical detail disclosure)', () => {
    const main = read(MAIN, 'main.ts');
    expect(main).not.toMatch(/\| stderr:/);
    expect(main).toContain('served.message, served.stderrTail || undefined');
    expect(main).toContain("outcome.message ?? 'unknown', outcome.stderrTail || undefined");
    expect(main).toContain('detail.slice(-600)'); // bounded
  });

  it('the packager removes exactly the dev driver files from a product package, and the checker names the same set (layered: removal, then a post-package check that fails the build)', () => {
    const packager = read(ROOT, 'mac-app.mjs');
    expect(packager).toContain("export const DEV_DRIVER_FILES = ['dev_drivers', 'capture', 'measure', 'soak'];");
    expect(packager).toContain("    if (!evidence) {\n      // The shipped product carries NO dev/evidence driver");
    expect(packager).toContain("for (const name of DEV_DRIVER_FILES) for (const ext of ['.js', '.js.map']) rmSync(join(app, 'dist', 'main', `${name}${ext}`), { force: true });");
    for (const name of ['dev_drivers', 'capture', 'measure', 'soak']) expect(existsSync(join(MAIN, `${name}.ts`)), name).toBe(true);
    // every main file that imports a driver module is itself a driver: nothing else in the product depends on them
    for (const f of productMain) expect(read(MAIN, f), f).not.toMatch(/from '\.\/(capture|measure|soak)'/);
  });
});
