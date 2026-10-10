/** Structural tripwires for the module architecture (compile-time boundaries, checked on the source text). */
import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('./**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;
const files = Object.entries(sources).filter(([path]) => !/\.test\.tsx?$/.test(path) && !path.startsWith('./testing/'));
const importsOf = (text: string) => [...text.matchAll(/(?:from|import)\s+['"]([^'"]+)['"]/g)].map((m) => m[1]!);

describe('module boundaries', () => {
  const moduleFiles = files.filter(([p]) => p.startsWith('./modules/') && p.split('/').length > 3);

  it('there are module files to check', () => { expect(moduleFiles.length).toBeGreaterThan(5); });

  it('a feature module imports only itself, UI Core, host-independent model types and external packages; never another module', () => {
    const offenders: string[] = [];
    for (const [path, text] of moduleFiles) {
      const own = path.split('/')[2]!;
      for (const spec of importsOf(text)) {
        if (!spec.startsWith('.')) continue;
        const resolved = new URL(spec, `file:///src/cockpit/${path.slice(2)}`).pathname.slice(1);
        const inOwn = resolved.startsWith(`src/cockpit/modules/${own}/`) || resolved === `src/cockpit/modules/${own}`;
        const inCore = resolved === 'src/cockpit/core' || resolved.startsWith('src/cockpit/core/') || resolved.startsWith('src/cockpit/design');
        const model = resolved.startsWith('src/model/') || resolved.startsWith('src/state/') || resolved.startsWith('src/render/');
        if (!inOwn && !inCore && !model) offenders.push(`${path} -> ${spec}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it('modules never touch providers, the shell, Electron or Node', () => {
    for (const [path, text] of moduleFiles) {
      expect(/providers\/|shell\/|electron|from 'node:/.test(importsOf(text).join('\n')), path).toBe(false);
    }
  });

  it('the shell and core import no feature module; only the composition root does', () => {
    const offenders = files.filter(([p]) => (p.startsWith('./shell/') || p.startsWith('./core/')) && importsOf(sources[p]!).some((s) => /modules\//.test(s))).map(([p]) => p);
    expect(offenders).toEqual([]);
  });

  it('the shell never mentions a module id or dispatches the legacy selectComparison', () => {
    const ids = ['runs', 'overview', 'timeline', 'trust', 'changes', 'validation', 'why', 'context', 'plan', 'attempts', 'evidence', 'workspace', 'settings'];
    for (const [path, text] of files.filter(([p]) => p.startsWith('./shell/'))) {
      expect(text.includes('selectComparison'), path).toBe(false);
      for (const id of ids) expect(new RegExp(`['"\`]${id}['"\`]`).test(text), `${path} names module ${id}`).toBe(false);
    }
  });

  it('production code never imports fixture code', () => {
    const offenders = files.filter(([p]) => !p.startsWith('./providers/fixture/') && importsOf(sources[p]!).some((s) => /(^|\/)fixture(\/|Host|$)/.test(s))).map(([p]) => p);
    expect(offenders).toEqual([]);
  });

  it('the production provider has no knowledge of fixtures', () => {
    for (const p of ['./providers/hostProvider.ts', './providers/KriyaProvider.ts']) expect(sources[p]!.toLowerCase().includes('fixture'), p).toBe(false);
  });
});
