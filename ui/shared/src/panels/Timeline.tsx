import { useMemo } from 'react';
import type { RunDetail, RunEvent } from '../model/kup';
import { isRecorded } from '../model/availability';
import { eventsByAttempt, unknownEventKeys } from '../model/normalize';
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

export function Timeline({ detail, pending, error, attempt, eventIndex, onSelectAttempt, onSelectEvent, height }: TimelineProps) {
  const recordedEvents = detail && isRecorded(detail.run_events) ? detail.run_events.data : null;
  const events: RunEvent[] = useMemo(() => recordedEvents ?? [], [recordedEvents]);
  const groups = useMemo(() => eventsByAttempt(events), [events]);
  const groupKeys = [...groups.keys()];
  const visible = useMemo(() => {
    const indexed = events.map((e, i) => ({ e, i }));
    if (attempt === null) return indexed;
    return indexed.filter(({ e }) => (typeof e.attempt === 'number' ? `attempt ${e.attempt}` : 'no attempt recorded') === attempt);
  }, [events, attempt]);
  const selectedPos = eventIndex === null ? -1 : visible.findIndex((v) => v.i === eventIndex);

  if (!detail) {
    return <section className="timeline" aria-label="Timeline"><div className="placeholder">{pending ? 'loading run…' : error ? `could not load run: ${error.code} - ${sanitizeText(error.message)}` : 'select a run'}</div></section>;
  }
  const r = detail.run;
  return (
    <section className="timeline" aria-label="Timeline">
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
          <VirtualList
            items={visible}
            rowHeight={44}
            height={height}
            selectedIndex={selectedPos >= 0 ? selectedPos : null}
            onSelect={(pos) => { const v = visible[pos]; onSelectEvent(v ? v.i : null); }}
            getKey={(v) => String(v.i)}
            ariaLabel="Recorded events in recorded order"
            emptyText="no events recorded for this selection"
            renderRow={({ e, i }) => {
              const unknown = unknownEventKeys(e);
              return (
                <div className="evrow">
                  <span className="muted mono">#{i + 1}</span>
                  <span className="muted mono">{sanitizeText(e.at ?? '-')}</span>
                  <span className="evname">{sanitizeText(e.event ?? '(unnamed event)')}</span>
                  <span className="muted">{sanitizeText(e.source ?? '?')} / {sanitizeText(e.authority ?? '?')}</span>
                  {unknown.length ? <span className="badge" title={unknown.join(', ')}>{unknown.length} unknown field{unknown.length > 1 ? 's' : ''}</span> : null}
                </div>
              );
            }}
          />
        )}
      </Recorded>
      <p className="muted note">Stages that were not recorded are unknown; nothing here is inferred (P-30).</p>
    </section>
  );
}
