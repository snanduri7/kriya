import type { Section } from '../model/kup';
import { availabilityLabel, isRecorded, sectionReason } from '../model/availability';
import type { ReactNode } from 'react';

/** Renders children only for a recorded section; otherwise the honest state (D-5, P-30). */
export function Recorded<T>({ section, title, children }: { section: Section<T> | null | undefined; title: string; children: (data: T) => ReactNode }) {
  if (isRecorded(section)) {
    return <>{children(section.data)}</>;
  }
  const reason = sectionReason(section);
  return (
    <div className={`availability availability-${section?.availability ?? 'not_recorded'}`} role="note">
      <span className="availability-title">{title}:</span> <strong>{availabilityLabel(section)}</strong>
      {reason ? <span className="availability-reason"> - {reason}</span> : null}
    </div>
  );
}

export function AvailabilityBadge({ section }: { section: Section | null | undefined }) {
  return <span className={`avbadge av-${section?.availability ?? 'not_recorded'}`}>{availabilityLabel(section)}</span>;
}
