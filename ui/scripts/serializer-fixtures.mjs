// Runs the Kriya serializer fixtures (fixtures/serializer_{events,gates,evidence}.py) with an explicit Python
// interpreter that has Kriya installed: $KRIYA_PYTHON if set, else `python3` from the active environment (e.g. an
// activated virtualenv). The interpreter is never inferred from a directory name; it must be able to `import kriya`,
// otherwise the run stops with exit 2 before any fixture script runs (GUI-INTEGRATION-ENV-001).
//
// usage (from ui/): node scripts/serializer-fixtures.mjs [--check]
import { spawnSync } from 'node:child_process';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const SCRIPTS = ['serializer_events.py', 'serializer_gates.py', 'serializer_evidence.py'];

export function resolveInterpreter(env = process.env) {
  const explicit = (env.KRIYA_PYTHON ?? '').trim();
  return explicit !== '' ? explicit : 'python3';
}

export function canImportKriya(interpreter) {
  const probe = spawnSync(interpreter, ['-c', 'import kriya'], { stdio: 'ignore' });
  return probe.status === 0;
}

function main(argv) {
  const check = argv.includes('--check');
  const interpreter = resolveInterpreter();
  if (!canImportKriya(interpreter)) {
    console.error(`serializer-fixtures: '${interpreter}' cannot import kriya - set KRIYA_PYTHON to a Python with ` +
      'Kriya installed (e.g. the repository .venv/bin/python) or activate that environment');
    return 2;
  }
  const fixtures = join(dirname(fileURLToPath(import.meta.url)), '..', 'fixtures');
  for (const script of SCRIPTS) {
    const run = spawnSync(interpreter, [join(fixtures, script), ...(check ? ['--check'] : [])], { stdio: 'inherit' });
    if (run.status !== 0) return run.status ?? 1;
  }
  return 0;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) process.exit(main(process.argv.slice(2)));
