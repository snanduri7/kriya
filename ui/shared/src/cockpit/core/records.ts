import type { EvidenceRecords } from '../../model/kup';

export type EvidenceRecord = EvidenceRecords['records'][number];

/** A record's payload as a plain object (the recorder always writes an object; anything else is treated as empty, never invented). */
export function payloadOf(record: EvidenceRecord): Record<string, unknown> {
  const p = (record as { payload?: unknown }).payload;
  return p !== null && typeof p === 'object' && !Array.isArray(p) ? (p as Record<string, unknown>) : {};
}

export const ofKind = (records: readonly EvidenceRecord[], kind: string) => records.filter((r) => r.kind === kind);

/** `sha256:ab12...` shortened for display; the full digest stays available to the caller. */
export const shortDigest = (digest: unknown): string => (typeof digest === 'string' ? digest.replace(/^sha256:/, '').slice(0, 10) : '-');

/** The attempt a document should show: the selected one, else the highest attempt that has a record of one of `kinds`; null when none has. */
export function effectiveAttempt(records: readonly EvidenceRecord[], selected: string | null, kinds: readonly string[]): number | null {
  if (selected !== null && Number.isInteger(Number(selected))) return Number(selected);
  const numbers = records.filter((r) => kinds.includes(r.kind) && r.attempt_number != null).map((r) => r.attempt_number as number);
  return numbers.length ? Math.max(...numbers) : null;
}
