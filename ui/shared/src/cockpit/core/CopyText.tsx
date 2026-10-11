import { useEffect, useRef, useState } from 'react';
import { useProvider } from './context';

/** A small "Copy" control: puts one string on the clipboard through the host and says, in a live region, whether it worked. The text is whatever the caller shows; nothing is read back. */
export function CopyText({ text, label, what }: { text: string; label?: string; what: string }) {
  const provider = useProvider();
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);
  const copy = () => {
    void provider.copyText(text).then(() => setState('copied'), () => setState('failed')).finally(() => {
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setState('idle'), 2500);
    });
  };
  return (
    <>
      <button type="button" className="ck-quiet" onClick={copy} aria-label={`Copy ${what}`}>{label ?? 'Copy'}</button>
      <span className="muted small" role="status" aria-live="polite">{state === 'copied' ? 'Copied' : state === 'failed' ? 'Could not copy' : ''}</span>
    </>
  );
}
