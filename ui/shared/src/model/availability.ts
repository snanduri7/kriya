import { AVAILABILITY_STATES, type Availability, type Section } from './kup';

export function isAvailability(value: unknown): value is Availability {
  return typeof value === 'string' && (AVAILABILITY_STATES as readonly string[]).includes(value);
}

/** A section is shown as data only when it says `recorded` AND carries data (D-5: nothing is reconstructed). */
export function isRecorded<T>(section: Section<T> | null | undefined): section is Section<T> & { data: T } {
  return !!section && section.availability === 'recorded' && section.data !== null && section.data !== undefined;
}

/** Human label for an availability state; an unknown value is shown literally, never mapped to success (P-24). */
export function availabilityLabel(section: Section | null | undefined): string {
  if (!section) return 'not recorded';
  switch (section.availability) {
    case 'recorded':
      return section.data === null || section.data === undefined ? 'recorded (empty)' : 'recorded';
    case 'not_recorded':
      return 'not recorded';
    case 'unreadable':
      return 'unreadable';
    case 'excluded':
      return 'excluded';
    case 'unsupported':
      return 'unsupported';
    default:
      return `unknown availability: ${String(section.availability)}`;
  }
}

export function sectionReason(section: Section | null | undefined): string | null {
  if (!section) return null;
  const parts: string[] = [];
  if (section.reason) parts.push(String(section.reason));
  if (section.provenance) parts.push(`provenance: ${String(section.provenance)}`);
  return parts.length ? parts.join(' - ') : null;
}

export function missingSection(reason = 'not persisted by this Kriya version'): Section<never> {
  return { availability: 'not_recorded', reason, provenance: null, data: null };
}
