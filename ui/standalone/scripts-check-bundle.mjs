#!/usr/bin/env node
/** Production-bundle check: a cockpit build made WITHOUT VITE_KRIYA_PROVIDER=fixture must contain no fixture provider or scenario data. */
import { readFileSync, readdirSync } from 'node:fs';
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

// --package <Kriya.app>: the SHIPPED product carries no dev/evidence driver (not the files, not their environment variable names, not the fixture stand-in) and its
// Electron fuses (RunAsNode, NODE_OPTIONS, --inspect) are off. The rules live in package_check.mjs so a test can run them on a deliberately bad package.
const at = process.argv.indexOf('--package');
if (at >= 0) {
  const app = process.argv[at + 1];
  const { packageFindings } = await import('./package_check.mjs');
  const problems = packageFindings(app);
  if (problems.length) { console.error('check-bundle --package: FAILED\n ' + problems.join('\n ')); process.exit(1); }
  console.log(`check-bundle --package: OK (${app} carries no dev driver, scripted chooser, fixture stand-in or measurement code; the product fuses are off)`);
}
