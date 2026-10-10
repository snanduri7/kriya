#!/usr/bin/env node
/** Production-bundle check: a cockpit build made WITHOUT VITE_KRIYA_PROVIDER=fixture must contain no fixture provider or scenario data. */
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const dir = join(dirname(fileURLToPath(import.meta.url)), 'dist', 'renderer', 'assets');
const markers = ['createFixtureProvider', 'FixtureHost', 'fx-greenfield-success', 'fx-brownfield-modify', 'Create a utility library'];
const bad = [];
for (const f of readdirSync(dir).filter((n) => n.endsWith('.js'))) {
  const text = readFileSync(join(dir, f), 'utf8');
  for (const m of markers) if (text.includes(m)) bad.push(`${f}: ${m}`);
}
if (bad.length) { console.error('check-bundle: FAILED - fixture code reached a production bundle:\n ' + bad.join('\n ')); process.exit(1); }
console.log('check-bundle: OK (no fixture provider or scenario data in dist/renderer)');
