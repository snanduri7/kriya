#!/usr/bin/env node
/**
 * macOS application identity for the Kriya GUI (development tooling; macOS only; no dependencies).
 *
 * Electron's stock bundle is "Electron.app": the menu-bar title, the Dock label, "About/Hide/Quit Electron", the process name and the helper
 * apps all come from that bundle, so no amount of window-title code can rename them. This script rebrands a COPY of the stock bundle:
 * Kriya.app, executable `Kriya`, helpers `Kriya Helper*`, identifier com.kriya.app, the Kriya icon when one is supplied.
 *
 *   node mac-app.mjs package [--out DIR] [--icon FILE.icns]   build DIR/Kriya.app with the built app inside (default out/)
 *   node mac-app.mjs start   [--icon FILE.icns]               dev/test launch through a rebranded dev bundle (.dev/Kriya.app) pointing at this directory
 *   node mac-app.mjs verify  [PATH/Kriya.app]                 check that no user-visible part of the bundle still says Electron
 *
 * The icon is NOT invented here: it is taken from --icon or assets/Kriya.icns. Without one, Electron's default icon file stays and a warning
 * says so (an approved Kriya icon is an owner-supplied asset).
 */
import { execFileSync, spawn } from 'node:child_process';
import { cpSync, existsSync, mkdirSync, readFileSync, readdirSync, renameSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const PRODUCT = 'Kriya';
const BUNDLE_ID = 'com.kriya.app';
const ELECTRON_APP = join(HERE, '..', 'node_modules', 'electron', 'dist', 'Electron.app');
const DEFAULT_ICON = join(HERE, 'assets', 'Kriya.icns');
const pkg = JSON.parse(readFileSync(join(HERE, 'package.json'), 'utf8'));

const plutil = (args) => execFileSync('/usr/bin/plutil', args, { encoding: 'utf8' });
const plistSet = (file, key, value) => plutil(['-replace', key, '-string', value, file]);
const plistJson = (file) => JSON.parse(plutil(['-convert', 'json', '-o', '-', file]));
const argValue = (flag) => { const i = process.argv.indexOf(flag); return i >= 0 ? process.argv[i + 1] : undefined; };

function requireMac() {
  if (process.platform !== 'darwin') throw new Error('mac-app.mjs builds a macOS application bundle: run it on macOS');
  if (!existsSync(ELECTRON_APP)) throw new Error(`the stock Electron bundle is missing (${ELECTRON_APP}); run npm install in ui/`);
}

export function rebrand(dest, { icon, appDir }) {
  rmSync(dest, { recursive: true, force: true });
  mkdirSync(dirname(dest), { recursive: true });
  cpSync(ELECTRON_APP, dest, { recursive: true, verbatimSymlinks: true });
  const contents = join(dest, 'Contents');
  renameSync(join(contents, 'MacOS', 'Electron'), join(contents, 'MacOS', PRODUCT));
  const info = join(contents, 'Info.plist');
  plistSet(info, 'CFBundleName', PRODUCT);
  plistSet(info, 'CFBundleDisplayName', PRODUCT);
  plistSet(info, 'CFBundleExecutable', PRODUCT);
  plistSet(info, 'CFBundleIdentifier', BUNDLE_ID);
  plistSet(info, 'CFBundleShortVersionString', pkg.version);
  plistSet(info, 'CFBundleVersion', pkg.version);
  // Helper apps: renamed exactly as Electron derives them from the application name ("<Name> Helper", "<Name> Helper (GPU)", ...).
  const frameworks = join(contents, 'Frameworks');
  for (const entry of readdirSync(frameworks).filter((n) => /^Electron Helper.*\.app$/.test(n))) {
    const helperName = entry.replace(/^Electron/, PRODUCT).replace(/\.app$/, '');
    const oldName = entry.replace(/\.app$/, '');
    const oldApp = join(frameworks, entry);
    const newApp = join(frameworks, `${helperName}.app`);
    renameSync(join(oldApp, 'Contents', 'MacOS', oldName), join(oldApp, 'Contents', 'MacOS', helperName));
    const hinfo = join(oldApp, 'Contents', 'Info.plist');
    plistSet(hinfo, 'CFBundleName', helperName);
    plistSet(hinfo, 'CFBundleDisplayName', helperName);
    plistSet(hinfo, 'CFBundleExecutable', helperName);
    plistSet(hinfo, 'CFBundleIdentifier', plistJson(hinfo).CFBundleIdentifier.replace(/^com\.github\.Electron/, BUNDLE_ID));
    renameSync(oldApp, newApp);
  }
  let iconNote = 'no approved Kriya icon supplied: the Electron default icon file remains (supply assets/Kriya.icns or --icon)';
  if (icon) {
    if (!existsSync(icon)) throw new Error(`icon file not found: ${icon}`);
    cpSync(icon, join(contents, 'Resources', 'Kriya.icns'));
    rmSync(join(contents, 'Resources', 'electron.icns'), { force: true });
    plistSet(info, 'CFBundleIconFile', 'Kriya.icns');
    iconNote = `icon: ${icon}`;
  }
  if (appDir) {
    // Packaged application: the built main process and renderer live inside the bundle (no node_modules: main uses Electron and Node built-ins only).
    const app = join(contents, 'Resources', 'app');
    mkdirSync(join(app, 'dist'), { recursive: true });
    writeFileSync(join(app, 'package.json'), JSON.stringify({ name: pkg.name, productName: PRODUCT, version: pkg.version, main: pkg.main, private: true }, null, 2));
    cpSync(join(appDir, 'dist', 'main'), join(app, 'dist', 'main'), { recursive: true });
    cpSync(join(appDir, 'dist', 'renderer'), join(app, 'dist', 'renderer'), { recursive: true });
  }
  execFileSync('/usr/bin/codesign', ['--force', '--deep', '--sign', '-', dest], { stdio: 'pipe' }); // ad-hoc: a modified bundle must be re-signed to launch on Apple silicon
  return iconNote;
}

/** Findings (empty = the bundle presents itself as Kriya everywhere a user can see a name). */
export function verifyBundle(app) {
  const findings = [];
  const contents = join(app, 'Contents');
  if (!existsSync(contents)) return [`not an application bundle: ${app}`];
  const info = plistJson(join(contents, 'Info.plist'));
  const expect = (cond, msg) => { if (!cond) findings.push(msg); };
  expect(app.endsWith(`${PRODUCT}.app`), `bundle directory is not ${PRODUCT}.app: ${app}`);
  for (const key of ['CFBundleName', 'CFBundleDisplayName', 'CFBundleExecutable']) expect(info[key] === PRODUCT, `Info.plist ${key} is ${JSON.stringify(info[key])}, not ${PRODUCT}`);
  expect(info.CFBundleIdentifier === BUNDLE_ID, `CFBundleIdentifier is ${JSON.stringify(info.CFBundleIdentifier)}, not ${BUNDLE_ID}`);
  expect(existsSync(join(contents, 'MacOS', PRODUCT)), `executable Contents/MacOS/${PRODUCT} is missing`);
  expect(!existsSync(join(contents, 'MacOS', 'Electron')), 'Contents/MacOS/Electron still exists');
  const frameworks = join(contents, 'Frameworks');
  for (const entry of existsSync(frameworks) ? readdirSync(frameworks).filter((n) => n.endsWith('.app')) : []) {
    expect(!/electron/i.test(entry), `helper app still named ${entry}`);
    const hinfo = plistJson(join(frameworks, entry, 'Contents', 'Info.plist'));
    for (const key of ['CFBundleName', 'CFBundleDisplayName', 'CFBundleExecutable', 'CFBundleIdentifier']) if (hinfo[key] !== undefined) expect(!/electron/i.test(String(hinfo[key])), `helper ${entry}: ${key} is ${hinfo[key]}`);
    expect(existsSync(join(frameworks, entry, 'Contents', 'MacOS', entry.replace(/\.app$/, ''))), `helper ${entry} has no executable of its own name`);
  }
  for (const key of ['NSHumanReadableCopyright']) if (info[key] !== undefined) expect(!/electron/i.test(String(info[key])), `${key} mentions Electron`);
  if (info.CFBundleIconFile === 'electron.icns') findings.push('WARNING (not an identity failure): the Electron default icon is still in use; an approved Kriya icon is not supplied');
  return findings;
}

const command = process.argv[2];
if (process.argv[1] === fileURLToPath(import.meta.url)) try {
  if (command === 'package') {
    requireMac();
    const out = resolve(argValue('--out') ?? join(HERE, 'out'));
    const icon = argValue('--icon') ?? (existsSync(DEFAULT_ICON) ? DEFAULT_ICON : undefined);
    if (!existsSync(join(HERE, 'dist', 'main', 'main.js')) || !existsSync(join(HERE, 'dist', 'renderer', 'index.html'))) throw new Error('build first: npm run build:cockpit (or build)');
    // The packaged product is the cockpit and carries no fixture or retired-inspector code: refuse to package anything else.
    execFileSync(process.execPath, [join(HERE, 'scripts-check-bundle.mjs')], { stdio: 'inherit' });
    const mode = JSON.parse(readFileSync(join(HERE, 'dist', 'renderer', 'ui-mode.json'), 'utf8')).ui;
    if (mode !== 'cockpit') throw new Error(`dist/renderer is a "${mode}" build; only the cockpit is packaged (npm run build)`);
    const note = rebrand(join(out, `${PRODUCT}.app`), { icon, appDir: HERE });
    const findings = verifyBundle(join(out, `${PRODUCT}.app`));
    console.log(`packaged ${join(out, `${PRODUCT}.app`)}\n  ${note}`);
    for (const f of findings) console.log(`  ${f}`);
    process.exit(findings.some((f) => !f.startsWith('WARNING')) ? 1 : 0);
  } else if (command === 'start') {
    requireMac();
    const dev = join(HERE, '.dev', `${PRODUCT}.app`);
    const stamp = join(HERE, '.dev', 'electron-version');
    const wanted = JSON.parse(readFileSync(join(HERE, '..', 'node_modules', 'electron', 'package.json'), 'utf8')).version;
    const icon = argValue('--icon') ?? (existsSync(DEFAULT_ICON) ? DEFAULT_ICON : undefined);
    if (!existsSync(dev) || !existsSync(stamp) || readFileSync(stamp, 'utf8') !== wanted) {
      const note = rebrand(dev, { icon });
      writeFileSync(stamp, wanted);
      console.log(`dev bundle ${dev} (${note})`);
    }
    // dev/test launch: the rebranded bundle runs this directory as its app, exactly as `electron .` would (arguments after `start` are passed through).
    const passthrough = process.argv.slice(3).filter((a, i, all) => a !== '--icon' && all[i - 1] !== '--icon');
    const child = spawn(join(dev, 'Contents', 'MacOS', PRODUCT), [HERE, ...passthrough], { stdio: 'inherit', env: process.env });
    child.on('exit', (code) => process.exit(code ?? 0));
  } else if (command === 'verify') {
    requireMac();
    const app = resolve(process.argv[3] ?? join(HERE, 'out', `${PRODUCT}.app`));
    const findings = verifyBundle(app);
    for (const f of findings) console.log(f);
    if (!findings.length) console.log(`identity OK: ${app}`);
    process.exit(findings.some((f) => !f.startsWith('WARNING')) ? 1 : 0);
  } else {
    console.error('usage: node mac-app.mjs package|start|verify');
    process.exit(2);
  }
} catch (e) {
  console.error(`mac-app: ${e instanceof Error ? e.message : String(e)}`);
  process.exit(1);
}
