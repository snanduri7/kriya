/** sanitizeText: text, never markup or hidden characters. The change-review screen shows code the person is asked to approve, so what is shown must be what is there. */
import { describe, expect, it } from 'vitest';
import { sanitizeText } from './sanitize';

describe('sanitizeText', () => {
  it('keeps newlines, tabs and ordinary text', () => {
    expect(sanitizeText('a\tb\nc <b>x</b>')).toBe('a\tb\nc <b>x</b>');
  });
  it('makes C0/C1 controls and line separators visible', () => {
    expect(sanitizeText('a\u0007b\u0085c d')).toBe('a\\u0007b\\u0085c\\u2028d');
  });
  it('makes bidirectional controls visible so displayed code cannot read differently from what it is (Trojan Source)', () => {
    const hostile = 'if (isAdmin‮ ⁦) { grant(); }‬';
    const shown = sanitizeText(hostile);
    expect(shown).not.toMatch(/[‪-‮⁦-⁩]/);
    expect(shown).toContain('\\u202e');
    expect(shown).toContain('\\u2066');
    expect(shown).toContain('\\u202c');
    for (const ch of ['‎', '‏', '؜']) expect(sanitizeText(`x${ch}y`), ch.codePointAt(0)!.toString(16)).toBe(`x\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}y`);
  });
  it('makes zero-width characters and the byte-order mark visible', () => {
    for (const ch of ['​', '‌', '‍', '⁠', '﻿']) expect(sanitizeText(`a${ch}b`), ch.codePointAt(0)!.toString(16)).toBe(`a\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}b`);
  });
  it('leaves ordinary non-ASCII text alone', () => {
    expect(sanitizeText('naïve – café – 日本語 – 🚀')).toBe('naïve – café – 日本語 – 🚀');
  });
});
