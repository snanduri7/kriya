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
  /** The trace row id the user selected (what history lists). */
  runId: string;
  /** The evidence run id Kriya resolved from that row's recorded event; every evidence read uses it. */
  evidenceRunId: string;
  manifest: EvidenceManifest;
  verify: EvidenceVerify | null;
  records: readonly EvidenceRecord[];
  provenance: { manifest: Provenance; verify: Provenance | null; records: Provenance | null };
}

const ABSENCE_TO_REASON: Record<string, UnavailableReason> = {
  pruned: 'pruned', capture_off: 'capture_off', recorder_unavailable: 'recorder_unavailable', pre_recorder: 'pre_recorder', unknown: 'store_absent',
};
const SEMANTIC_ABSENCE: Partial<Record<UnavailableReason, string>> = {
  pruned: 'The evidence store for this run was removed by retention.',
  capture_off: 'Evidence capture was off for this run.',
  pre_recorder: 'This run was recorded before the evidence recorder existed.',
  recorder_unavailable: 'The evidence recorder was unavailable for this run.',
  store_absent: 'No evidence store is available for this run.',
};
const PAGE = 1000;
const MAX_PAGES = 200;

function fromError<T>(error: ProviderError): Availability<T> {
  if (error.code === 'EVIDENCE_STORE_NOT_FOUND') return unavailable('store_absent', error.message);
  if (error.code === 'EVIDENCE_VERIFY_FAILED') return unavailable('unverified', `${error.message}${error.extra.broken_seq ? ` (first bad record ${String(error.extra.broken_seq)})` : ''}`);
  return failed(error.code, error.message);
}

export async function loadRunEvidence(provider: UIProvider, runId: string, snapshotId: string | null, signal?: AbortSignal): Promise<Availability<RunEvidence>> {
  if (snapshotId === null) return unavailable('unknown_run', 'No snapshot is pinned, so the run\'s recorded evidence link cannot be read.');
  // Trace ids and evidence ids differ in every real store: Kriya resolves the link from the row's recorded event.
  const manifest = await provider.evidenceManifest(runId, { snapshotId, fromTrace: true }, signal);
  return loadStore(provider, runId, manifest, signal);
}

/**
 * The evidence of a run a launch is STILL executing (GUI final: reviewing a change before approving it). Such a run has no trace row yet - Kriya writes it when the
 * run ends - so there is no recorded link to resolve: the launch id IS the evidence run id (the run claimed it), and the store is read live, verified like any other.
 * The store is unsealed until the run closes, which the modules already present as a degraded state.
 */
export async function loadLiveRunEvidence(provider: UIProvider, launchId: string, signal?: AbortSignal): Promise<Availability<RunEvidence>> {
  const manifest = await provider.evidenceManifest(launchId, {}, signal);
  return loadStore(provider, launchId, manifest, signal);
}

async function loadStore(provider: UIProvider, runId: string, manifest: ProviderResult<EvidenceManifest>, signal?: AbortSignal): Promise<Availability<RunEvidence>> {
  if (!manifest.ok) return fromError(manifest.error);
  const m = manifest.data;
  const evidenceRunId = m.run_id;
  if (m.store === 'absent' || evidenceRunId === null) {
    // Default text is the semantic state; the exact recorded detail (paths and all) stays available as secondary provenance.
    const why = m.absence;
    const reason = ABSENCE_TO_REASON[why?.reason ?? 'unknown'] ?? 'store_absent';
    return unavailable(reason, SEMANTIC_ABSENCE[reason] ?? SEMANTIC_ABSENCE.store_absent!, why ? `${why.detail} (derived from the run's recorded event)` : undefined);
  }
  const verify: ProviderResult<EvidenceVerify> = await provider.evidenceVerify(evidenceRunId, signal);
  if (!verify.ok) return fromError(verify.error);
  const records: EvidenceRecord[] = [];
  let cursor: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const res = await provider.evidenceRecords(evidenceRunId, { limit: PAGE, cursor }, signal);
    if (!res.ok) return fromError(res.error);
    records.push(...(res.data.records as EvidenceRecord[]));
    if (!res.data.next_cursor) {
      const base: RunEvidence = { runId, evidenceRunId, manifest: m, verify: verify.data, records, provenance: { manifest: manifest.provenance, verify: verify.provenance, records: res.provenance } };
      const reasons: string[] = [];
      if (!verify.data.sealed) reasons.push('The store is unsealed: the run crashed or is still writing; which one is not recorded.');
      if (res.data.truncated_tail) reasons.push('The store ends in a partial record, which is not shown.');
      return reasons.length ? { state: 'degraded', data: base, provenance: manifest.provenance, reasons } : { state: 'ready', data: base, provenance: manifest.provenance };
    }
    cursor = res.data.next_cursor;
  }
  return failed('PAGE_LIMIT', `more than ${MAX_PAGES} pages of records; the run is not loaded in full`);
}


/** A refused or failed content read as the Availability the user sees (Kriya states why; the UI never guesses). */
export function contentAvailability<T>(result: ProviderResult<T>): Availability<T> {
  if (result.ok) return { state: 'ready', data: result.data, provenance: result.provenance };
  const { code, message, extra } = result.error;
  const reason = typeof extra.reason === 'string' ? extra.reason : '';
  switch (code) {
    case 'EVIDENCE_CONTENT_POLICY': return unavailable('denied', reason === 'unclassified_key' ? 'This kind of content is not classified for display, so Kriya never serves it.' : "Kriya's content visibility policy does not authorize showing stored content.", message);
    case 'EVIDENCE_CONTENT_UNAVAILABLE': return reason === 'digest_only' ? unavailable('digest_only', 'This run kept a digest of this content, not the content itself.', message) : { state: 'not_recorded', detail: 'The recorder kept nothing for this part of the change.' };
    case 'EVIDENCE_BLOB_TOO_LARGE': return failed(code, `This part is ${String(extra.bytes_total ?? 'more than the limit of')} bytes; the display limit is 1 MiB, so it is not shown.`);
    case 'EVIDENCE_VERIFY_FAILED': return unavailable('unverified', `The store does not verify (${String(extra.status ?? 'status not given')}); nothing is served from it.`, message);
    case 'EVIDENCE_STORE_NOT_FOUND': return unavailable('store_absent', 'No evidence store is available for this run.', message);
    default: return failed(code, message);
  }
}
