import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { conciseWhy, renderMarkdown, reviewBody } from './markdown';

describe('renderMarkdown: safe subset, elements only', () => {
  it('renders headings, lists, emphasis, inline code and fenced code as elements - no literal #, **, backticks', () => {
    const { container } = render(<>{renderMarkdown('## Summary\n\nThe change is **correct** and uses `max`.\n\n### Notes\n- first\n- second\n\n1. one\n2. two\n\n```\nx = 1\n```')}</>);
    expect(container.querySelector('h4')!.textContent).toBe('Summary');
    expect(container.querySelector('h5')!.textContent).toBe('Notes');
    expect(container.querySelector('strong')!.textContent).toBe('correct');
    expect(container.querySelector('code')!.textContent).toBe('max');
    expect(Array.from(container.querySelectorAll('ul li')).map((l) => l.textContent)).toEqual(['first', 'second']);
    expect(Array.from(container.querySelectorAll('ol li')).map((l) => l.textContent)).toEqual(['one', 'two']);
    expect(container.querySelector('pre')!.textContent).toBe('x = 1');
    expect(container.textContent).not.toMatch(/##|\*\*|`/);
  });
  it('never makes HTML, links or images: markup in a review is shown as the text it is', () => {
    const { container } = render(<>{renderMarkdown('<img src=x onerror=alert(1)> [click](javascript:alert(1)) ![i](http://x/y.png)')}</>);
    expect(container.querySelector('img')).toBeNull();
    expect(container.querySelector('a')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
    expect(container.textContent).toContain('[click](javascript:alert(1))');
  });
  it('escapes control and bidirectional characters in every text node', () => {
    const { container } = render(<>{renderMarkdown('safe‮evil `code\u0000`')}</>);
    expect(container.textContent).toContain('\\u202e');
    expect(container.textContent).toContain('\\u0000');
  });
  it('an unterminated fence still ends and shows its text; unknown syntax stays visible', () => {
    const { container } = render(<>{renderMarkdown('```\nnever closed\n> quote | table')}</>);
    expect(container.querySelector('pre')!.textContent).toContain('never closed');
  });
});

describe('conciseWhy: one to three recorded sentences, deterministic', () => {
  it('keeps a sentence that names a file with a dot (measured: the Why began "py successfully prints" for `app.py`)', () => {
    const review = '## Code Review for app.py\n\n### Summary of Findings\nThe provided Python script `app.py` successfully prints "Hello Kriya" as requested. The change from "Hello World" to "Hello Kriya" is correctly implemented. No functional issues were identified in the code.';
    const why = conciseWhy(review)!;
    expect(why.startsWith('The provided Python script app.py successfully prints')).toBe(true);
    expect(why).not.toMatch(/^py /);
    expect(why).toContain('No functional issues were identified in the code.');
  });
  it('splits only on terminal punctuation followed by whitespace, and keeps unterminated text whole', () => {
    expect(conciseWhy('Version 1.2.3 is used. Second one! Third? Fourth sentence here.')).toBe('Version 1.2.3 is used. Second one! Third?');
    expect(conciseWhy('no terminal punctuation but app.py is named')).toBe('no terminal punctuation but app.py is named');
    expect(conciseWhy('Ends with a dot.')).toBe('Ends with a dot.');
  });
  it('takes the first three sentences with the markdown removed and never adds a word', () => {
    const why = conciseWhy('## Review\n\nThe change adds `clamp`. It keeps the public signature. Tests cover the edge cases. A fourth sentence is not shown.');
    expect(why).toBe('The change adds clamp. It keeps the public signature. Tests cover the edge cases.');
  });
  it('is bounded, is the text itself when it is short, and is null when there is nothing', () => {
    expect(conciseWhy('Short reason.')).toBe('Short reason.');
    expect(conciseWhy('x'.repeat(2000))!.length).toBeLessThanOrEqual(320);
    expect(conciseWhy('')).toBeNull();
    expect(conciseWhy(null)).toBeNull();
    expect(conciseWhy('   \n ## \n')).toBeNull();
  });
  it('is the same every time for the same recorded text', () => {
    const text = '# Title\n\nFirst. Second! Third? Fourth.';
    expect(conciseWhy(text)).toBe(conciseWhy(text));
  });
});

describe('reviewBody', () => {
  it('drops Kriya\'s own policy line and marker, keeps the report', () => {
    expect(reviewBody('Human-in-the-loop review policy\n\n=== Automated Code Review ===\n## Report\nLooks right.')).toBe('## Report\nLooks right.');
    expect(reviewBody('plain text')).toBe('plain text');
    expect(reviewBody('=== Automated Code Review ===\n')).toBeNull();
    expect(reviewBody(null)).toBeNull();
  });
});
