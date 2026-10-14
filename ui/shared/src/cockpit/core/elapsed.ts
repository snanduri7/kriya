import { useEffect, useState } from 'react';

export function clock(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

/** The duration between two recorded timestamps as m:ss, or null when either is missing or unreadable. */
export function elapsedBetween(since: string | null, until: string | null): string | null {
  if (since === null || until === null) return null;
  const a = Date.parse(since);
  const b = Date.parse(until);
  return Number.isFinite(a) && Number.isFinite(b) ? clock(b - a) : null;
}

/** Elapsed time since the launch record's own timestamp; once the launch has ended (`until`, the record's own end time) it is the recorded duration and stops. Presentation only: a local clock difference that decides nothing. */
export function useElapsed(since: string | null, running: boolean, until: string | null = null): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [running]);
  if (!running && until !== null) return elapsedBetween(since, until);
  if (since === null) return null;
  const start = Date.parse(since);
  return Number.isFinite(start) ? clock(now - start) : null;
}
