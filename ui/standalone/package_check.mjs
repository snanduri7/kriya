/** Findings for a PRODUCT package (empty = clean). Separate from the CLI so a test can run it on a deliberately bad synthetic package (a real negative control). */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { FRAMEWORK_BINARY, productFuseFindings } from './fuses.mjs';

export const DEV_DRIVERS = ['dev_drivers', 'capture', 'measure', 'soak'];
export const FORBIDDEN_TEXT = ['KRIYA_UI_CAPTURE', 'KRIYA_UI_MEASURE', 'KRIYA_UI_SOAK', 'KRIYA_UI_TRACE', 'KRIYA_UI_ALLOW_REAL_KRIYA', 'KRIYA_FAKE_', 'fake_kriya', 'fake-kriya', 'runCapture', 'runSoak', 'runMeasurement'];

function* walk(dir) {
  for (const name of readdirSync(dir)) {
    const full = join(dir, name);
    if (statSync(full).isDirectory()) yield* walk(full); else yield full;
  }
}

export function packageFindings(app, { checkFuses = true } = {}) {
  const problems = [];
  const resources = join(app, 'Contents', 'Resources', 'app');
  if (!existsSync(resources)) return [`no packaged application at ${resources}`];
  const main = join(resources, 'dist', 'main');
  for (const name of DEV_DRIVERS) for (const ext of ['.js', '.js.map', '.cjs', '.mjs']) if (existsSync(join(main, name + ext))) problems.push(`dev driver packaged: dist/main/${name}${ext}`);
  // EVERY file under the packaged app (all extensions, source maps, sub-directories, the renderer assets, package.json) is scanned for the dev-only names.
  for (const file of walk(resources)) {
    const rel = file.slice(resources.length + 1);
    if (/\.(png|icns|ico|woff2?|ttf)$/i.test(file)) continue;
    const text = readFileSync(file, 'utf8');
    for (const t of FORBIDDEN_TEXT) if (text.includes(t)) problems.push(`${rel} mentions ${t}`);
  }
  for (const name of ['fake-kriya', 'test', 'fixtures', 'measurements']) if (existsSync(join(resources, name))) problems.push(`${name}/ is inside the packaged app`);
  for (const f of readdirSync(resources)) if (/^capture-.*\.json$/.test(f)) problems.push(`${f} is inside the packaged app`);
  try {
    const pkg = JSON.parse(readFileSync(join(resources, 'package.json'), 'utf8'));
    if (typeof pkg.main !== 'string' || !existsSync(join(resources, pkg.main))) problems.push(`package.json main ${JSON.stringify(pkg.main)} does not name a packaged file`);
    else if (DEV_DRIVERS.some((n) => pkg.main.includes(n))) problems.push(`package.json main is a dev driver: ${pkg.main}`);
  } catch { problems.push('package.json of the packaged app is missing or unreadable'); }
  if (checkFuses) {
    const framework = join(app, ...FRAMEWORK_BINARY);
    if (!existsSync(framework)) problems.push('Electron Framework binary not found (cannot verify fuses)');
    else { try { problems.push(...productFuseFindings(readFileSync(framework))); } catch (error) { problems.push(`fuses unreadable: ${error.message}`); } }
  }
  return problems;
}
