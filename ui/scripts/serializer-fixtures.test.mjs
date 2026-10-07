// GUI-INTEGRATION-ENV-001: the serializer-fixture runner uses an explicit interpreter, never a directory name.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SCRIPTS, canImportKriya, resolveInterpreter } from './serializer-fixtures.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));

test('KRIYA_PYTHON wins; otherwise python3 from the active environment', () => {
  assert.equal(resolveInterpreter({ KRIYA_PYTHON: '/opt/py/bin/python' }), '/opt/py/bin/python');
  assert.equal(resolveInterpreter({ KRIYA_PYTHON: '  ' }), 'python3');
  assert.equal(resolveInterpreter({}), 'python3');
});

test('an interpreter that cannot import kriya is refused before any fixture script runs', () => {
  assert.equal(canImportKriya('/nonexistent/python-for-test'), false);
  const run = spawnSync(process.execPath, [join(HERE, 'serializer-fixtures.mjs'), '--check'],
    { env: { ...process.env, KRIYA_PYTHON: '/nonexistent/python-for-test' }, encoding: 'utf8' });
  assert.equal(run.status, 2);
  assert.match(run.stderr, /cannot import kriya - set KRIYA_PYTHON/);
});

test('the npm scripts use the runner, and no directory-name interpreter remains', () => {
  const scripts = JSON.parse(readFileSync(join(HERE, '..', 'package.json'), 'utf8')).scripts;
  assert.equal(scripts['fixtures:serializer'], 'node scripts/serializer-fixtures.mjs');
  assert.equal(scripts['fixtures:serializer:check'], 'node scripts/serializer-fixtures.mjs --check');
  assert.ok(!JSON.stringify(scripts).includes('.newvenv'));
  assert.deepEqual(SCRIPTS, ['serializer_events.py', 'serializer_gates.py', 'serializer_evidence.py']);
});
