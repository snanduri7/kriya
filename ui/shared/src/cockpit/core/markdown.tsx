import type { ReactNode } from 'react';
import { sanitizeText } from '../../render/sanitize';

/**
 * A small, SAFE Markdown renderer for recorded review text (Demo V2). Subset: ATX headings, paragraphs, bullet and numbered lists, fenced code, `inline code`, **bold**, *italic*.
 * Output is React elements only - never HTML, never a link, image or raw tag (a "<img onerror>" in a review is shown as the text it is). Every text node is sanitized
 * (control and bidi characters escaped). Unknown syntax stays visible as plain text; nothing is hidden or reinterpreted.
 */
const FENCE = /^\s*```/;
const HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const BULLET = /^\s*[-*+]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\*[^*\n]+\*)/g;
  let last = 0; let n = 0;
  for (const m of text.matchAll(pattern)) {
    const at = m.index ?? 0;
    if (at > last) out.push(sanitizeText(text.slice(last, at)));
    const token = m[0];
    const key = `${keyBase}-${n++}`;
    if (token.startsWith('`')) out.push(<code key={key}>{sanitizeText(token.slice(1, -1))}</code>);
    else if (token.startsWith('**')) out.push(<strong key={key}>{sanitizeText(token.slice(2, -2))}</strong>);
    else out.push(<em key={key}>{sanitizeText(token.slice(1, -1))}</em>);
    last = at + token.length;
  }
  if (last < text.length) out.push(sanitizeText(text.slice(last)));
  return out;
}

export function renderMarkdown(source: string): ReactNode {
  const lines = source.replace(/\r\n?/g, '\n').split('\n');
  const blocks: ReactNode[] = [];
  let i = 0; let key = 0;
  while (i < lines.length) {
    const line = lines[i]!;
    if (line.trim() === '') { i += 1; continue; }
    if (FENCE.test(line)) {
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !FENCE.test(lines[i]!)) { code.push(lines[i]!); i += 1; }
      i += 1;
      blocks.push(<pre key={key++} className="mono ck-output">{sanitizeText(code.join('\n'))}</pre>);
      continue;
    }
    const h = HEADING.exec(line);
    if (h) {
      const level = Math.min(6, h[1]!.length + 2);   // review headings sit below the page's own h2/h3
      const Tag = `h${level}` as 'h3' | 'h4' | 'h5' | 'h6';
      blocks.push(<Tag key={key++} className="ck-md-heading">{inline(h[2]!, `h${key}`)}</Tag>);
      i += 1;
      continue;
    }
    const list = BULLET.test(line) ? BULLET : NUMBERED.test(line) ? NUMBERED : null;
    if (list) {
      const items: ReactNode[] = [];
      const ordered = list === NUMBERED;
      while (i < lines.length && list.test(lines[i]!)) { items.push(<li key={items.length}>{inline(list.exec(lines[i]!)![1]!, `l${key}-${items.length}`)}</li>); i += 1; }
      blocks.push(ordered ? <ol key={key++}>{items}</ol> : <ul key={key++}>{items}</ul>);
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i]!.trim() !== '' && !FENCE.test(lines[i]!) && !HEADING.test(lines[i]!) && !BULLET.test(lines[i]!) && !NUMBERED.test(lines[i]!)) { para.push(lines[i]!); i += 1; }
    blocks.push(<p key={key++}>{inline(para.join(' '), `p${key}`)}</p>);
  }
  return <div className="ck-markdown" data-testid="markdown">{blocks}</div>;
}

/** Recorded text as prose: headings are labels, not sentences (dropped), list markers and emphasis syntax removed. */
function plain(text: string): string {
  return text.replace(/```[\s\S]*?```/g, ' ').replace(/^\s{0,3}#{1,6}\s+.*$/gm, ' ').replace(/^\s*[-*+]\s+/gm, '').replace(/^\s*\d+[.)]\s+/gm, '').replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*]+)\*/g, '$1').replace(/`([^`]+)`/g, '$1').replace(/\s+/g, ' ').trim();
}

/** The Why in one to three recorded sentences: deterministic (first sentences of the recorded text, markdown removed), bounded, and never a new model call. null when there is nothing. */
export function conciseWhy(source: string | null | undefined, maxSentences = 3, maxChars = 320): string | null {
  if (!source) return null;
  const text = plain(source);
  if (text === '') return null;
  const sentences = text.match(/[^.!?]+(?:[.!?]+(?=\s|$)|$)/g)?.map((s) => s.trim()).filter(Boolean) ?? [text];
  let out = '';
  for (const s of sentences.slice(0, maxSentences)) { if ((out + ' ' + s).trim().length > maxChars && out !== '') break; out = (out + ' ' + s).trim(); }
  return out.length > maxChars ? `${out.slice(0, maxChars - 1).trimEnd()}…` : out;
}

/** The reviewer's report without Kriya's own preamble: the policy line and the "=== Automated Code Review ===" marker are bookkeeping, not part of the review. */
export function reviewBody(reason: string | null | undefined): string | null {
  if (!reason) return null;
  const marker = reason.indexOf('=== Automated Code Review ===');
  const body = (marker >= 0 ? reason.slice(marker + '=== Automated Code Review ==='.length) : reason).trim();
  return body === '' ? null : body;
}
