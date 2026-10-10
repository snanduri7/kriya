import { describe, expect, it } from 'vitest';
import { highlightLine, languageOf } from './highlight';

const join = (line: string, lang: Parameters<typeof highlightLine>[1]) => highlightLine(line, lang).map((t) => t.text).join('');

describe('highlighter', () => {
  it('never changes or drops a character, for any input', () => {
    for (const [line, lang] of [['public static int clamp(final int v) { // x "y"', 'c'], ['def f(x): return "a\\"b" # c', 'py'], ['  \t weird "unterminated', 'c'], ['', 'c'], ["it's 0x1F + 3.14 ' ", 'go'], ['plain <b>not html</b>', 'plain']] as const) expect(join(line, lang)).toBe(line);
  });
  it('classifies comments, strings, numbers and keywords; identifiers that contain a keyword are plain', () => {
    const tokens = highlightLine('return count + 42; // done', 'c');
    expect(tokens.filter((t) => t.cls !== 'plain').map((t) => [t.text, t.cls])).toEqual([['return', 'keyword'], ['42', 'number'], ['// done', 'comment']]);
    expect(highlightLine('returned = 1', 'c').some((t) => t.cls === 'keyword')).toBe(false);
    expect(highlightLine('x = "a // b"', 'c').find((t) => t.cls === 'string')?.text).toBe('"a // b"');
    expect(highlightLine('x = 1 # c', 'py').at(-1)).toEqual({ text: '# c', cls: 'comment' });
  });
  it('picks the language from the recorded path only; unknown is plain', () => {
    expect([languageOf('a/B.java'), languageOf('x.py'), languageOf('x.go'), languageOf('README'), languageOf(null)]).toEqual(['c', 'py', 'go', 'plain', 'plain']);
    expect(highlightLine('return x // y', 'plain').every((t) => t.cls === 'plain')).toBe(true);
  });
});
