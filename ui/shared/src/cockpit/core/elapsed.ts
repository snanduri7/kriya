import { useEffect, useState } from 'react';

export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** Elapsed time since the launch record's own timestamp. Presentation only: it is a local clock difference and decides nothing. */
export function useElapsed(since: string | null, running: boolean): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (since === null) return null;
  const start = Date.parse(since);
  return Number.isFinite(start) ? clock(now - start) : null;
}
