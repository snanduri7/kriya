/**
 * EvidenceClient: loads everything the run-scoped modules read, once per selected run, through the provider and nothing else.
 * One selection = one manifest, one verification and one paged pass over the records, shared by every module (so the store is
 * verified once per page, not once per module). Nothing is cached across selections: a fresh selection or refresh re-verifies.
 */
import type { EvidenceManifest, EvidenceVerify } from '../../model/kup';
import { failed, unavailable, type Availability, type UnavailableReason } from './availability';
import type { Provenance } from './evidence';
import type { ProviderError, ProviderResult, UIProvider } from './provider';
import type { EvidenceRecord } from './records';

export interface RunEvidence {
  runId: string;
  manifest: EvidenceManifest;
  verify: EvidenceVerify | null;
  records: readonly EvidenceRecord[];
  provenance: { manifest: Provenance; verify: Provenance | null; records: Provenance | null };
}

const ABSENCE_TO_REASON: Record<string, UnavailableReason> = {
  pruned: 'pruned', capture_off: 'capture_off', recorder_unavailable: 'recorder_unavailable', pre_recorder: 'pre_recorder', unknown: 'store_absent',
};
const PAGE = 1000;
const MAX_PAGES = 200;

function fromError<T>(error: ProviderError): Availability<T> {
  if (error.code === 'EVIDENCE_STORE_NOT_FOUND') return unavailable('store_absent', error.message);
  if (error.code === 'EVIDENCE_VERIFY_FAILED') return unavailable('unverified', `${error.message}${error.extra.broken_seq ? ` (first bad record ${String(error.extra.broken_seq)})` : ''}`);
  return failed(error.code, error.message);
}

export async function loadRunEvidence(provider: UIProvider, runId: string, snapshotId: string | null, signal?: AbortSignal): Promise<Availability<RunEvidence>> {
  const manifest = await provider.evidenceManifest(runId, snapshotId ?? undefined, signal);
  if (!manifest.ok) return fromError(manifest.error);
  const m = manifest.data;
  if (m.store === 'absent') {
    const why = m.absence;
    return unavailable(ABSENCE_TO_REASON[why?.reason ?? 'unknown'] ?? 'store_absent', why ? `${why.detail} (derived from the run's recorded event)` : 'no evidence store for this run');
  }
  const verify: ProviderResult<EvidenceVerify> = await provider.evidenceVerify(runId, signal);
  if (!verify.ok) return fromError(verify.error);
  const records: EvidenceRecord[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await provider.evidenceRecords(runId, { limit: PAGE, cursor }, signal);
    if (!res.ok) return fromError(res.error);
    records.push(...(res.data.records as EvidenceRecord[]));
    if (!res.data.next_cursor) {
      const base: RunEvidence = { runId, manifest: m, verify: verify.data, records, provenance: { manifest: manifest.provenance, verify: verify.provenance, records: res.provenance } };
      const reasons: string[] = [];
      if (!verify.data.sealed) reasons.push('The store is unsealed: the run crashed or is still writing; which one is not recorded.');
      if (res.data.truncated_tail) reasons.push('The store ends in a partial record, which is not shown.');
      return reasons.length ? { state: 'degraded', data: base, provenance: manifest.provenance, reasons } : { state: 'ready', data: base, provenance: manifest.provenance };
    }
    cursor = res.data.next_cursor;
  }
  return failed('PAGE_LIMIT', `more than ${MAX_PAGES} pages of records; the run is not loaded in full`);
}
