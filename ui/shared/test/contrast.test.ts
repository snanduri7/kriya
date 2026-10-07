/** WCAG 2.1 contrast of the palette in styles.css, both schemes (P-35 "contrast"). Computed, not eyeballed. */
import { describe, expect, it } from 'vitest';
import css from '../src/styles.css?raw'; // a raw string import: no Node API even in the shared tests (P-R1)

const expand = (hex: string) => (hex.length === 4 ? `#${hex[1]}${hex[1]}${hex[2]}${hex[2]}${hex[3]}${hex[3]}` : hex);
function vars(block: string): Record<string, string> {
  return Object.fromEntries([...block.matchAll(/--([a-z-]+):\s*(#[0-9a-fA-F]{3,6})\b/g)].map((m) => [m[1]!, expand(m[2]!)]));
}
const light = vars(css.split('@media (prefers-color-scheme: dark)')[0]!);
const dark = vars(css.split('@media (prefers-color-scheme: dark)')[1]!.split('}')[0]!);
function lum(hex: string): number {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
}
const ratio = (a: string, b: string) => { const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x) as [number, number]; return (l1 + 0.05) / (l2 + 0.05); };

describe('palette contrast (WCAG AA: 4.5:1 text, 3:1 UI)', () => {
  it('parses every palette variable (3- and 6-digit hex)', () => {
    for (const p of [light, dark]) for (const k of ['bg', 'fg', 'muted', 'ok', 'bad', 'warn', 'focus', 'panel', 'sel', 'add', 'del']) expect(p[k], k).toMatch(/^#[0-9a-fA-F]{6}$/);
  });
  for (const [name, p] of [['light', light], ['dark', dark]] as const) {
    it(`${name}: body text, muted text and status colours on both backgrounds`, () => {
      for (const bg of [p.bg!, p.panel!, p.sel!]) {
        for (const fg of ['fg', 'muted', 'ok', 'bad', 'warn'] as const) {
          expect(ratio(p[fg]!, bg), `${name} ${fg} on ${bg}`).toBeGreaterThanOrEqual(4.5);
        }
      }
      expect(ratio(p.focus!, p.bg!), `${name} focus ring`).toBeGreaterThanOrEqual(3);
      expect(ratio(p.fg!, p.add!), `${name} text on added line`).toBeGreaterThanOrEqual(4.5);
      expect(ratio(p.fg!, p.del!), `${name} text on deleted line`).toBeGreaterThanOrEqual(4.5);
    });
  }
});
