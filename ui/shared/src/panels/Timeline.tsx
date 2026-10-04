import { useEffect, useMemo, useRef, useState } from 'react';
import type { RunDetail, RunEvent } from '../model/kup';
import { isRecorded } from '../model/availability';
import { eventsByAttempt, formatEventTime, unknownEventKeys } from '../model/normalize';
import { sanitizeText } from '../render/sanitize';
import { AvailabilityBadge, Recorded } from './Availability';
import { VirtualList } from './VirtualList';

export interface TimelineProps {
  detail: RunDetail | null;
  pending: boolean;
  error: { code: string; message: string } | null;
  attempt: string | null;
  eventIndex: number | null;
  onSelectAttempt: (attempt: string | null) => void;
  onSelectEvent: (index: number | null) => void;
  height: number;
}

/** Case-insensitive substring match over the RECORDED fields only: kind, source, message and the serialized details.
 * Nothing is inferred or normalized beyond lower-casing; a details value that is not an object is searched as text. */
export function eventMatchesQuery(event: RunEvent, query: string): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  const details = (event as { details?: unknown }).details;
  const detailsText = details === undefined || details === null ? '' : typeof details === 'string' ? details : JSON.stringify(details);
  return [event.kind, event.source ?? '', event.message ?? '', detailsText].some((field) => String(field).toLowerCase().includes(q));
}

export function Timeline({ detail, pending, error, attempt, eventIndex, onSelectAttempt, onSelectEvent, height }: TimelineProps) {
  const [query, setQuery] = useState('');
  const [authority, setAuthority] = useState<string | null>(null);
  const recordedEvents = detail && isRecorded(detail.run_events) ? detail.run_events.data : null;
  const events: RunEvent[] = useMemo(() => recordedEvents ?? [], [recordedEvents]);
  const groups = useMemo(() => eventsByAttempt(events), [events]);
  const groupKeys = [...groups.keys()];
  // Authority values exactly as recorded (closed set in Kriya, but shown literally here, never interpreted).
  const authorities = useMemo(() => [...new Set(events.map((e) => (typeof e.authority === 'string' ? e.authority : '(not recorded)')))], [events]);
  // Recorded order is preserved and every row keeps its ORIGINAL index `i`: filtering never renumbers or reorders.
  const visible = useMemo(() => {
    const indexed = events.map((e, i) => ({ e, i }));
    return indexed.filter(({ e }) =>
      (attempt === null || (typeof e.attempt === 'number' ? `attempt ${e.attempt}` : 'no attempt recorded') === attempt)
      && (authority === null || (typeof e.authority === 'string' ? e.authority : '(not recorded)') === authority)
      && eventMatchesQuery(e, query));
  }, [events, attempt, authority, query]);
  const selectedPos = eventIndex === null ? -1 : visible.findIndex((v) => v.i === eventIndex);
  const selectedHidden = eventIndex !== null && selectedPos < 0 ? events[eventIndex] ?? null : null;
  const filterActive = attempt !== null || authority !== null || query.trim() !== '';
  const clearFilter = () => { setQuery(''); setAuthority(null); if (attempt !== null) onSelectAttempt(null); };
  const root = useRef<HTMLElement>(null);
  // Clearing from the "selected event hidden" notice removes that notice (and its button) from the DOM, so focus is moved
  // deliberately to the events list, where the still-selected event is the active option - never left on <body>.
  const clearFilterAndFocusList = () => { clearFilter(); requestAnimationFrame(() => root.current?.querySelector<HTMLElement>('[role="listbox"]')?.focus()); };
  // The visible count updates on every keystroke; the live region announces it once typing has paused (not every render).
  const summary = `${visible.length} of ${events.length} recorded events shown${filterActive ? ' (filtered; recorded order kept)' : ''}`;
  const [announced, setAnnounced] = useState(summary);
  useEffect(() => { const t = setTimeout(() => setAnnounced(summary), 350); return () => clearTimeout(t); }, [summary]);

  if (!detail) {
    return <section className="timeline" aria-label="Timeline"><div className="placeholder">{pending ? 'loading run…' : error ? `could not load run: ${error.code} - ${sanitizeText(error.message)}` : 'select a run'}</div></section>;
  }
  const r = detail.run;
  return (
    <section className="timeline" aria-label="Timeline" ref={root}>
      <div className="run-header">
        <h2 className="goal">{sanitizeText(r.goal ?? '(no goal recorded)')}</h2>
        <dl className="facts">
          <dt>Outcome</dt><dd>{sanitizeText(r.status ?? 'unknown')}{r.failure_category ? ` (${sanitizeText(r.failure_category)})` : ''}</dd>
          <dt>Attempts</dt><dd>{r.attempts ?? 'not recorded'}</dd>
          <dt>Duration</dt><dd>{r.duration_sec === null || r.duration_sec === undefined ? 'not recorded' : `${r.duration_sec.toFixed(2)} s`}</dd>
          <dt>Recorded at</dt><dd>{sanitizeText(r.timestamp ?? 'not recorded')} <span className="muted">(as stored; timezone not recorded)</span></dd>
          <dt>Files modified (raw)</dt><dd className="mono">{sanitizeText(r.files_modified ?? 'not recorded')}</dd>
          {r.milestone_group_id ? <><dt>Milestone</dt><dd>{sanitizeText(String(r.milestone_group_id))} {r.milestone_index ?? '?'} / {r.milestone_total ?? '?'}</dd></> : null}
        </dl>
        {error ? <div className="warn" role="alert">refresh failed: {error.code} - this view is no longer current</div> : null}
      </div>
      <div className="attempts" role="group" aria-label="Attempts">
        <button type="button" className={`chip${attempt === null ? ' active' : ''}`} aria-pressed={attempt === null} onClick={() => onSelectAttempt(null)}>all events ({events.length})</button>
        {groupKeys.map((k) => (
          <button key={k} type="button" className={`chip${attempt === k ? ' active' : ''}`} aria-pressed={attempt === k} onClick={() => onSelectAttempt(k)}>{k} ({groups.get(k)?.length ?? 0})</button>
        ))}
        <AvailabilityBadge section={detail.run_events} />
      </div>
      <Recorded section={detail.run_events} title="Run events">
        {() => (
          <div>
            <div className="event-filter" role="group" aria-label="Event filter">
              <label className="filter">
                <span className="sr-only">Search recorded events</span>
                <input type="search" placeholder="Search kind, source, message, recorded details" value={query} onChange={(e) => setQuery(e.target.value)} />
              </label>
              <label className="muted">authority
                <select aria-label="Filter by authority" value={authority ?? ''} onChange={(e) => setAuthority(e.target.value === '' ? null : e.target.value)}>
                  <option value="">all</option>
                  {[...new Set([...authorities, ...(authority !== null && !authorities.includes(authority) ? [authority] : [])])].map((a) => <option key={a} value={a}>{sanitizeText(a)}{authorities.includes(a) ? '' : ' (none in this run)'}</option>)}
                </select>
              </label>
              <button type="button" className="small" onClick={clearFilter} disabled={!filterActive}>Clear filter</button>
              <span className="muted filter-count" aria-hidden="true">{summary}</span>
              <span className="sr-only" role="status" aria-label="Event filter result">{announced}</span>
            </div>
            {selectedHidden ? (
              <div className="warn">
                selected event #{(eventIndex ?? 0) + 1} ({sanitizeText(selectedHidden.kind || '(unnamed event)')}) is hidden by the current filter; it stays selected and its evidence stays shown.
                <button type="button" className="small" onClick={clearFilterAndFocusList}>Clear filter to show the selected event</button>
              </div>
            ) : null}
            <VirtualList
              items={visible}
              rowHeight={44}
              height={height}
              selectedIndex={selectedPos >= 0 ? selectedPos : null}
              onSelect={(pos) => { const v = visible[pos]; onSelectEvent(v ? v.i : null); }}
              getKey={(v) => String(v.i)}
              ariaLabel="Recorded events in recorded order"
              emptyText={events.length === 0 ? 'no events recorded for this selection' : `no recorded events match the filter (${events.length} recorded, all hidden by the filter)`}
              renderRow={({ e, i }) => {
                const unknown = unknownEventKeys(e);
                return (
                  <div className="evrow">
                    <span className="muted mono">#{i + 1}</span>
                    <span className="muted mono" title={`created_at ${String(e.created_at)}`}>{formatEventTime(e.created_at)}</span>
                    <span className="evname" title={typeof e.message === 'string' ? sanitizeText(e.message) : undefined}>{sanitizeText(e.kind || '(unnamed event)')}</span>
                    <span className="muted">{sanitizeText(e.source ?? '?')} / {sanitizeText(e.authority ?? '?')}</span>
                    {unknown.length ? <span className="badge" title={unknown.join(', ')}>{unknown.length} unknown field{unknown.length > 1 ? 's' : ''}</span> : null}
                  </div>
                );
              }}
            />
          </div>
        )}
      </Recorded>
      <p className="muted note">Stages that were not recorded are unknown; nothing here is inferred (P-30). Event times are the recorded created_at (epoch seconds) shown as UTC. The filter only hides rows; it never changes, reorders or renumbers recorded events.</p>
    </section>
  );
}
