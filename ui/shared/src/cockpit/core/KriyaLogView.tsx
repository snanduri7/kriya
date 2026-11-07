import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { shouldStickToBottom } from './activity';
import { CopyText } from './CopyText';
import { useRunLog } from './useRunLog';

type Filter = 'all' | 'warnings' | 'errors';
const RANK: Record<string, number> = { DEBUG: 0, INFO: 1, WARNING: 2, ERROR: 3, CRITICAL: 3 };
const MIN_RANK: Record<Filter, number> = { all: 0, warnings: 2, errors: 3 };
const WHY_NONE: Record<string, string> = {
  NO_RUN_LOG: 'Kriya has not written a log for this run yet (or run logging is turned off in its configuration).',
  LOG_DIRECTORY_UNAVAILABLE: 'Kriya could not resolve its log directory, so there is no log to show.',
  RUN_LOG_UNREADABLE: 'The run log exists but could not be read safely, so it is not shown.',
};

/** One line the way the log writes it. Copy puts exactly these (already redacted by the backend) lines on the clipboard. */
export const logLineText = (r: { time: string; level: string; logger: string; message: string }) => `${r.time} ${r.level} ${r.logger}: ${r.message}`;

/**
 * KRIYA LOG: Kriya's own application log for this run (the real backend log, redacted before it leaves Kriya), live while the run is. It is NOT the Activity feed (concise product events) and NOT build/test output.
 * It follows new lines only while the person is at the bottom: scrolling up stops it, and "Jump to latest" brings the person back. No phase is guessed from a line - the stepper shows the phase.
 */
export function KriyaLogView({ runId, live }: { runId: string | null; live: boolean }) {
  const log = useRunLog(runId, live, true);
  const [filter, setFilter] = useState<Filter>('all');
  const lines = useMemo(() => (log.state === 'ready' || log.state === 'unavailable' || log.state === 'error' ? log.lines : []), [log]);
  const shown = useMemo(() => lines.filter((r) => (RANK[r.level] ?? 1) >= MIN_RANK[filter]), [lines, filter]);
  const box = useRef<HTMLDivElement | null>(null);
  const stick = useRef(true);
  const [away, setAway] = useState(false);
  useLayoutEffect(() => {
    const el = box.current;
    if (el && stick.current) el.scrollTop = el.scrollHeight;
  }, [shown.length]);
  const jump = () => { const el = box.current; stick.current = true; setAway(false); if (el) el.scrollTop = el.scrollHeight; };
  return (
    <div className="ck-kriyalog" data-testid="kriya-log">
      <div className="ck-kriyalog-bar">
        <label className="muted small">Show <select aria-label="Log level" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}><option value="all">All</option><option value="warnings">Warnings and errors</option><option value="errors">Errors only</option></select></label>
        <span className="muted small" data-testid="kriya-log-count">{shown.length === lines.length ? `${lines.length} line${lines.length === 1 ? '' : 's'}` : `${shown.length} of ${lines.length} lines`}</span>
        {shown.length ? <CopyText text={shown.map(logLineText).join('\n')} what="visible log lines" label="Copy visible lines" /> : null}
        {away ? <button type="button" className="ck-quiet" data-testid="jump-to-latest" onClick={jump}>Jump to latest</button> : null}
      </div>
      <div className="ck-kriyalog-lines" ref={box} data-testid="kriya-log-scroll" role="log" aria-label="Kriya log" onScroll={(e) => { const at = shouldStickToBottom(e.currentTarget); stick.current = at; setAway(!at); }}>
        {log.state === 'idle' || log.state === 'loading' ? <p className="muted" role="status">Reading the log…</p> : null}
        {log.state === 'unavailable' ? <p className="muted" role="status" data-testid="kriya-log-none">{WHY_NONE[log.reason] ?? 'There is no log to show for this run.'}</p> : null}
        {log.state === 'error' ? <p className="muted" role="alert">The log could not be read ({log.code}).</p> : null}
        {shown.map((r) => (
          <div key={r.offset} className="ck-logline" data-level={r.level}>
            <span className="mono muted ck-logtime">{r.time.slice(11, 19)}</span> <span className="ck-loglevel">{r.level}</span> <span className="muted ck-loglogger">{r.logger.replace(/^kriya\./, '')}</span> <span className="ck-logmsg">{r.message}</span>
          </div>
        ))}
        {log.state === 'ready' && lines.length === 0 ? <p className="muted" role="status">The log is empty so far.</p> : null}
      </div>
    </div>
  );
}
