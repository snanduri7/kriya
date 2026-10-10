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
