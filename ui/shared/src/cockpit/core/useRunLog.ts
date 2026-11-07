import { useEffect, useRef, useState } from 'react';
import type { RunLogRecord } from '../../model/kup';
import { useProvider } from './context';

/** The most lines kept in the window at once: older lines scroll out of memory, never off the disk (the backend log is untouched). */
export const MAX_LOG_LINES = 5000;
const POLL_MS = 1500;
const PAGE = 200;

export type RunLogState =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; lines: RunLogRecord[]; atEnd: boolean }
  | { state: 'unavailable'; reason: string; lines: RunLogRecord[] }
  | { state: 'error'; code: string; message: string; lines: RunLogRecord[] };

/**
 * The run's REAL application log, read page by page through the backend projection (KUP `run.log`: bounded, cursor-based, redacted before it leaves Kriya; the renderer names a run id and a cursor the
 * backend issued, never a path). While the run is live it keeps asking for what is new; once the log is exhausted for a finished run it stops. `enabled` is false until the person opens the Kriya Log.
 */
export function useRunLog(runId: string | null, live: boolean, enabled: boolean): RunLogState {
  const provider = useProvider();
  const [fetched, setView] = useState<RunLogState & { runId?: string }>({ state: 'idle' });
  const lines = useRef<RunLogRecord[]>([]);
  const cursor = useRef<string | undefined>(undefined);
  const forRun = useRef<string | null>(null);

  useEffect(() => {
    if (!enabled || runId === null) return undefined;
    if (forRun.current !== runId) { forRun.current = runId; lines.current = []; cursor.current = undefined; }
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout> | null = null;
    let stopped = false;
    const pull = async (): Promise<void> => {
      let atEnd = false;
      let unavailable: string | null = null;
      for (let page = 0; page < 20 && !atEnd && !stopped; page += 1) {
        const res = await provider.runLog(runId, { ...(cursor.current !== undefined ? { cursor: cursor.current } : {}), limit: PAGE }, abort.signal);
        if (stopped) return;
        if (!res.ok) { setView({ state: 'error', code: res.error.code, message: res.error.message, lines: lines.current, runId }); return; }
        const data = res.data;
        if (data.reset) lines.current = [];
        if (!data.available) { unavailable = data.reason ?? 'NO_RUN_LOG'; atEnd = true; break; }
        if (data.records.length) lines.current = [...lines.current, ...data.records].slice(-MAX_LOG_LINES);
        cursor.current = data.next_cursor ?? cursor.current;
        atEnd = data.at_end;
      }
      if (stopped) return;
      setView(unavailable ? { state: 'unavailable', reason: unavailable, lines: lines.current, runId } : { state: 'ready', lines: lines.current, atEnd, runId });
      if (live || unavailable !== null || !atEnd) timer = setTimeout(() => void pull(), unavailable !== null && !live ? 0 : POLL_MS);
      if (!live && unavailable !== null && timer !== null) { clearTimeout(timer); timer = null; }
    };
    void pull();
    return () => { stopped = true; abort.abort(); if (timer !== null) clearTimeout(timer); };
  }, [provider, runId, live, enabled]);

  // What was fetched belongs to ONE run: for any other run (or before the first page) the view is "loading", and nothing is shown while the log is not requested.
  if (!enabled || runId === null) return { state: 'idle' };
  return fetched.runId === runId ? fetched : { state: 'loading' };
}
