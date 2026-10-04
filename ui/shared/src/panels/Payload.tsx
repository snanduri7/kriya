import { useState } from 'react';
import { previewText, safeStringify, sanitizeText } from '../render/sanitize';

export const PAYLOAD_PREVIEW_CHARS = 64 * 1024;

/** Text-only, sanitized, bounded rendering of any recorded value (P-32). Large values are revealed in chunks. */
export function Payload({ value, onCopy, label }: { value: unknown; onCopy?: (text: string) => void; label: string }) {
  const [shown, setShown] = useState(PAYLOAD_PREVIEW_CHARS);
  const full = typeof value === 'string' ? value : safeStringify(value);
  const preview = previewText(full, shown);
  return (
    <div className="payload">
      <div className="payload-bar">
        <span className="muted">{label}: {full.length.toLocaleString()} chars</span>
        {onCopy ? <button type="button" className="small" onClick={() => onCopy(full)}>Copy</button> : null}
        {preview.truncated ? <button type="button" className="small" onClick={() => setShown((s) => s + PAYLOAD_PREVIEW_CHARS)}>Show more ({(preview.total - preview.text.length).toLocaleString()} more)</button> : null}
      </div>
      <pre className="payload-text" tabIndex={0}>{sanitizeText(preview.text)}{preview.truncated ? '\n…' : ''}</pre>
    </div>
  );
}
