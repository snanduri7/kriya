#!/usr/bin/env node
/** Production-bundle check: a cockpit build made WITHOUT VITE_KRIYA_PROVIDER=fixture must contain no fixture provider or scenario data. */
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(dirname(fileURLToPath(import.meta.url)), 'dist', 'renderer', 'assets');
const markers = ['createFixtureProvider', 'FixtureHost', 'fx-greenfield-success', 'fx-brownfield-modify', 'Create a utility library', 'FixtureExecution', 'fx-launched', 'fixture-model:7b', '20261010T101010-abcdef01'];
// The M1 inspector is retired from the shipped UI: none of its panels may be in the product bundle (only an explicit legacy build contains them).
markers.push('Recovery workspace', 'Workspace (recovery assessment)');
const bad = [];
for (const f of readdirSync(dir).filter((n) => n.endsWith('.js'))) {
  const text = readFileSync(join(dir, f), 'utf8');
  for (const m of markers) if (text.includes(m)) bad.push(`${f}: ${m}`);
}
if (bad.length) { console.error('check-bundle: FAILED - fixture code reached a production bundle:\n ' + bad.join('\n ')); process.exit(1); }
console.log('check-bundle: OK (no fixture provider or scenario data in dist/renderer)');

// --package <Kriya.app>: the SHIPPED product carries no dev/evidence driver: not the files, not their environment variable names, not the fixture stand-in.
const at = process.argv.indexOf('--package');
if (at >= 0) {
  const app = process.argv[at + 1];
  const main = join(app, 'Contents', 'Resources', 'app', 'dist', 'main');
  const problems = [];
  for (const name of ['dev_drivers', 'capture', 'measure', 'soak']) for (const ext of ['.js', '.js.map']) if (existsSync(join(main, name + ext))) problems.push(`dev driver packaged: dist/main/${name}${ext}`);
  const forbiddenText = ['KRIYA_UI_CAPTURE', 'KRIYA_UI_MEASURE', 'KRIYA_UI_SOAK', 'KRIYA_UI_TRACE', 'KRIYA_UI_ALLOW_REAL_KRIYA', 'KRIYA_FAKE_', 'fake_kriya', 'fake-kriya', 'runCapture', 'runSoak', 'runMeasurement'];
  for (const f of readdirSync(main).filter((n) => n.endsWith('.js'))) {
    const text = readFileSync(join(main, f), 'utf8');
    for (const t of forbiddenText) if (text.includes(t)) problems.push(`${f} mentions ${t}`);
  }
  const resources = join(app, 'Contents', 'Resources', 'app');
  for (const name of ['fake-kriya', 'test', 'fixtures', 'measurements']) if (existsSync(join(resources, name))) problems.push(`${name}/ is inside the packaged app`);
  const tops = readdirSync(resources);
  for (const f of tops) if (/^capture-.*\.json$/.test(f)) problems.push(`${f} is inside the packaged app`);
  if (problems.length) { console.error('check-bundle --package: FAILED\n ' + problems.join('\n ')); process.exit(1); }
  console.log(`check-bundle --package: OK (${app} carries no dev driver, scripted chooser, fixture stand-in or measurement code)`);
}
