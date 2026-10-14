import { highlightLine, languageOf, type TokenClass } from '../../render/highlight';
import { sanitizeText } from '../../render/sanitize';
import type { ChangeApprovalReview } from '../../model/kup';

type ReviewFile = ChangeApprovalReview['files'][number];

const COLOURS: Record<TokenClass, string | undefined> = { plain: undefined, comment: 'var(--ck-muted)', string: 'var(--ck-ok)', number: 'var(--ck-warn)', keyword: 'var(--ck-accent)' };
const CHANGE_WORDS: Record<ReviewFile['change_type'], string> = { added: 'new file', deleted: 'file removed', modified: 'modified' };

/** The typed hunks of ONE file of a verified review, as a unified diff: old/new line numbers, +/- signs, highlighted text. Text only (sanitised); nothing is reinterpreted. */
export function FileDiff({ file }: { file: ReviewFile }) {
  const language = languageOf(file.path);
  return (
    <section className="ck-filediff" aria-label={`Proposed change to ${file.path}`} data-change-type={file.change_type}>
      <div className="ck-filediff-head">
        <span className="mono ck-filepath">{file.path}</span>
        <span className="muted small">{CHANGE_WORDS[file.change_type]}</span>
        <span className="grow" />
        <span className="ck-plus" aria-label={`${file.additions} lines added`}>+{file.additions}</span>
        <span className="ck-minus" aria-label={`${file.deletions} lines removed`}>−{file.deletions}</span>
      </div>
      <pre className="mono ck-diffpane" aria-label={`Unified diff of ${file.path}`}>
        {file.hunks.map((hunk, h) => (
          <div key={h} data-hunk>
            {file.hunks.length > 1 || h > 0 ? <div className="ck-hunkhead" data-diff-row="hunk">{sanitizeText(hunk.header)}</div> : null}
            {hunk.lines.map((line, i) => {
              const text = sanitizeText(line.text);
              const sign = line.kind === 'add' ? '+' : line.kind === 'remove' ? '−' : line.kind === 'note' ? '' : ' ';
              return (
                <div key={i} data-diff-row={line.kind} className={`ck-diffrow ${line.kind}`}>
                  <span aria-hidden="true" className="ck-lineno">{line.old_line ?? ''}</span>
                  <span aria-hidden="true" className="ck-lineno">{line.new_line ?? ''}</span>
                  <span aria-hidden="true" className="ck-sign">{sign}</span>
                  {line.kind === 'note' ? <span className="muted">{text}</span>
                    : <span>{highlightLine(text, language).map((t, j) => <span key={j} data-token={t.cls} style={{ color: COLOURS[t.cls] }}>{t.text}</span>)}</span>}
                </div>
              );
            })}
          </div>
        ))}
        {file.hunks.length === 0 ? <div className="muted small" role="note" data-testid="empty-change">This file has no content.</div> : null}
        {file.truncated ? <div className="muted small" role="note">This file is longer than can be shown here; the rest is not displayed.</div> : null}
      </pre>
    </section>
  );
}
