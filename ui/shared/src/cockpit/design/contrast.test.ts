/** WCAG 2.1 contrast of the cockpit palette, computed (4.5:1 for all text including metadata text, 3:1 for the focus ring). */
import { describe, expect, it } from 'vitest';
import css from './cockpit.css?raw';

const vars = Object.fromEntries([...css.split('}')[0]!.matchAll(/--ck-([a-z-]+):\s*(#[0-9a-fA-F]{6})/g)].map((m) => [m[1]!, m[2]!]));
const lum = (hex: string) => { const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4)); return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!; };
const ratio = (a: string, b: string) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p) as [number, number]; return (x + 0.05) / (y + 0.05); };

describe('cockpit palette contrast', () => {
  it('parses the whole palette', () => { for (const k of ['bg', 'panel', 'raised', 'fg', 'muted', 'accent', 'accent-bg', 'ok', 'bad', 'warn', 'add', 'del', 'focus']) expect(vars[k], k).toBeDefined(); });
  it('text, metadata text and status colours reach 4.5:1 on every surface they sit on', () => {
    for (const bg of ['bg', 'panel', 'raised', 'accent-bg'] as const) for (const fg of ['fg', 'muted', 'accent', 'ok', 'bad', 'warn'] as const) {
      expect(ratio(vars[fg]!, vars[bg]!), `${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
    }
    for (const bg of ['add', 'del'] as const) expect(ratio(vars.fg!, vars[bg]!), `fg on ${bg}`).toBeGreaterThanOrEqual(4.5);
  });
  it('the focus ring is visible on every surface (3:1)', () => { for (const bg of ['bg', 'panel', 'raised']) expect(ratio(vars.focus!, vars[bg]!)).toBeGreaterThanOrEqual(3); });
});
