import { describe, expect, it } from 'vitest';
import css from './cockpit.css?raw';

/** The Demo V2 typography pass is a token scale, not scattered numbers: pin the scale and the places that must use it. */
describe('typography and spacing scale', () => {
  const token = (name: string) => Number(new RegExp(`${name}: (\\d+(?:\\.\\d+)?)px`).exec(css)?.[1]);
  it('page title 22-24, primary state 16-18, section 14-16, body 14-15, secondary 12-13, code 13-14', () => {
    expect(token('--ck-t-title')).toBeGreaterThanOrEqual(22); expect(token('--ck-t-title')).toBeLessThanOrEqual(24);
    expect(token('--ck-t-state')).toBeGreaterThanOrEqual(16); expect(token('--ck-t-state')).toBeLessThanOrEqual(18);
    expect(token('--ck-t-section')).toBeGreaterThanOrEqual(14); expect(token('--ck-t-section')).toBeLessThanOrEqual(16);
    expect(token('--ck-t-body')).toBeGreaterThanOrEqual(14); expect(token('--ck-t-body')).toBeLessThanOrEqual(15);
    expect(token('--ck-t-secondary')).toBeGreaterThanOrEqual(12); expect(token('--ck-t-secondary')).toBeLessThanOrEqual(13);
    expect(token('--ck-t-code')).toBeGreaterThanOrEqual(13); expect(token('--ck-t-code')).toBeLessThanOrEqual(14);
  });
  it('the spacing scale is 4 / 8 / 12 / 16 / 24 / 32', () => {
    expect([1, 2, 3, 4, 5, 6].map((n) => token(`--ck-s-${n}`))).toEqual([4, 8, 12, 16, 24, 32]);
  });
  it('titles, section labels, code and the approval footer use the tokens', () => {
    expect(css).toMatch(/\.ck-project-head h1 \{ font-size: var\(--ck-t-title\); font-weight: 600/);
    expect(css).toMatch(/\.ck-review \.ck-action-sub[^{]*\{ font-size: var\(--ck-t-section\); font-weight: 600/);
    expect(css).toMatch(/\.ck-diffpane[^{]*\{ font-size: var\(--ck-t-code\)/);
    expect(css).toMatch(/\.ck-review \.ck-decision \{ gap: var\(--ck-s-2\)/);
  });
});
