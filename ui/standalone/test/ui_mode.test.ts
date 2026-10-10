/** Which bundle the host loaded decides whether the narrow M1 compatibility may exist. Anything unexpected is the strict cockpit default. */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { resolveUiMode } from '../src/main/ui_mode';

describe('resolveUiMode', () => {
  it('only an explicit inspector marker is the inspector', () => {
    expect(resolveUiMode(() => '{"ui":"inspector"}')).toBe('inspector');
    expect(resolveUiMode(() => '{"ui":"cockpit"}')).toBe('cockpit');
  });
  it.each([
    ['missing file', () => null], ['empty', () => ''], ['not json', () => 'inspector'], ['array', () => '["inspector"]'], ['null', () => 'null'],
    ['wrong case', () => '{"ui":"Inspector"}'], ['other value', () => '{"ui":"legacy"}'], ['unreadable', () => { throw new Error('EACCES'); }],
  ])('%s -> cockpit (strict default)', (_name, read) => {
    expect(resolveUiMode(read as () => string | null)).toBe('cockpit');
  });
  it('the Vite build records the mode: inspector only for the explicit legacy build, cockpit for every other build', () => {
    const config = readFileSync(join(__dirname, '..', 'vite.config.ts'), 'utf8');
    expect(config).toContain("fileName: 'ui-mode.json'");
    expect(config).toContain("process.env.VITE_KRIYA_UI === 'inspector' ? 'inspector' : 'cockpit'");
    const entry = readFileSync(join(__dirname, '..', 'src', 'renderer', 'main.tsx'), 'utf8');
    expect(entry).toContain("import.meta.env.VITE_KRIYA_UI !== 'inspector'"); // the cockpit is the default; no runtime switch exists
    expect(entry).not.toMatch(/localStorage|location\.(search|hash)|URLSearchParams/);
    expect(JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8')).scripts['build:inspector-legacy']).toContain('VITE_KRIYA_UI=inspector');
  });
});
