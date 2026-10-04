#!/usr/bin/env node
/** Dependency check for gate A-2 / P-R1: ui/shared declares no electron or node-only dependency, and no source
 * file imports one. Complements the ESLint rule (a lint rule can be disabled inline; this cannot). Exit 1 on any finding. */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { ALLOWED_SHARED_DEPENDENCIES, FORBIDDEN_IMPORT_PATTERNS, NODE_BUILTINS } from './forbidden-imports.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..', 'shared');
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'));
const findings = [];
for (const section of ['dependencies', 'peerDependencies', 'optionalDependencies']) {
  for (const name of Object.keys(pkg[section] ?? {})) {
    if (!ALLOWED_SHARED_DEPENDENCIES.includes(name)) findings.push(`package.json ${section}: "${name}" is not in the allowlist [${ALLOWED_SHARED_DEPENDENCIES.join(', ')}]`);
  }
}
if (pkg.devDependencies && Object.keys(pkg.devDependencies).some((n) => n === 'electron' || n.startsWith('@electron/'))) findings.push('package.json devDependencies name electron');

function walk(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (/\.(ts|tsx|js|mjs)$/.test(name)) out.push(p);
  }
  return out;
}
const importRe = /(?:import\s+(?:[^'"]*?\s+from\s+)?|export\s+[^'"]*?\s+from\s+|require\(|import\()\s*['"]([^'"]+)['"]/g;
const forbidden = new Set(['electron', ...NODE_BUILTINS]);
for (const file of walk(join(root, 'src'))) {
  const text = readFileSync(file, 'utf8');
  for (const m of text.matchAll(importRe)) {
    const spec = m[1];
    const bare = spec.replace(/^node:/, '').split('/')[0];
    if (spec.startsWith('node:') || forbidden.has(bare) || spec.startsWith('@electron/')) findings.push(`${file.replace(root + '/', 'shared/')}: imports "${spec}"`);
  }
}
if (findings.length) {
  console.error('check-shared-deps: FAILED');
  for (const f of findings) console.error(' - ' + f);
  process.exit(1);
}
console.log(`check-shared-deps: OK (${FORBIDDEN_IMPORT_PATTERNS.length} forbidden patterns, deps allowlist [${ALLOWED_SHARED_DEPENDENCIES.join(', ')}])`);
