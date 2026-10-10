import { highlightLine, languageOf, type TokenClass } from '../../../render/highlight';
import { sanitizeText } from '../../../render/sanitize';

const COLOURS: Record<TokenClass, string | undefined> = { plain: undefined, comment: 'var(--ck-muted)', string: 'var(--ck-ok)', number: 'var(--ck-warn)', keyword: 'var(--ck-accent)' };

interface Row { kind: 'file' | 'hunk' | 'add' | 'del' | 'context' | 'note'; text: string }

/** A unified diff as it was recorded: file headers, hunk headers, additions, deletions and context. Text only; nothing is reinterpreted. */
export function parseUnified(diff: string): Row[] {
  return diff.split('\n').filter((l, i, a) => !(i === a.length - 1 && l === '')).map((line) => {
    if (line.startsWith('+++') || line.startsWith('---')) return { kind: 'file', text: line };
    if (line.startsWith('@@')) return { kind: 'hunk', text: line };
    if (line.startsWith('\\')) return { kind: 'note', text: line };
    if (line.startsWith('+')) return { kind: 'add', text: line.slice(1) };
    if (line.startsWith('-')) return { kind: 'del', text: line.slice(1) };
    return { kind: 'context', text: line.startsWith(' ') ? line.slice(1) : line };
  });
}

export function DiffView({ diff, path }: { diff: string; path: string | null }) {
  const language = languageOf(path);
  const rows = parseUnified(diff);
  return (
    <pre className="mono" aria-label="Unified diff" style={{ margin: 0, overflow: 'auto', maxHeight: 420 }}>
      {rows.map((row, i) => {
        const text = sanitizeText(row.text);
        const background = row.kind === 'add' ? 'var(--ck-add)' : row.kind === 'del' ? 'var(--ck-del)' : undefined;
        const sign = row.kind === 'add' ? '+' : row.kind === 'del' ? '-' : ' ';
        return (
          <div key={i} data-diff-row={row.kind} style={{ background, display: 'flex', whiteSpace: 'pre-wrap' }}>
            <span aria-hidden="true" style={{ width: 16, flex: 'none', color: 'var(--ck-muted)' }}>{row.kind === 'file' || row.kind === 'hunk' || row.kind === 'note' ? '' : sign}</span>
            {row.kind === 'file' || row.kind === 'hunk' || row.kind === 'note' ? <span style={{ color: 'var(--ck-muted)' }}>{text}</span>
              : <span>{highlightLine(text, language).map((t, j) => <span key={j} data-token={t.cls} style={{ color: COLOURS[t.cls] }}>{t.text}</span>)}</span>}
          </div>
        );
      })}
    </pre>
  );
}
