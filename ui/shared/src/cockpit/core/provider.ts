/**
 * The ONE typed provider contract every module consumes. Two implementations exist and satisfy the same contract tests:
 * KriyaProvider (production, supported Kriya read APIs) and FixtureProvider (development and tests only). A module never knows
 * which one it holds, and never contains separate "real" and "fake" logic.
 */
import type {
  Capabilities, EvidenceChangeContent, EvidenceManifest, EvidenceRecords, EvidenceVerify, HistoryList, RunDetail, SnapshotAcquireResult, SnapshotList, SnapshotVerify, WorkspaceStatus,
} from '../../model/kup';
import type { HostSettings } from '../../host/HostAdapter';
import type { Provenance } from './evidence';

export type ProviderKind = 'kriya' | 'fixture';

export interface ProviderError {
  /** A KUP error code, or one the provider mints: HOST_ERROR (transport), INVALID_RESPONSE (not a valid KUP envelope for the operation). */
  code: string;
  message: string;
  /** The typed extras Kriya returned (status, broken_seq, head_then, ...), verbatim. */
  extra: Record<string, unknown>;
}

export type ProviderResult<T> = { ok: true; data: T; provenance: Provenance } | { ok: false; error: ProviderError };

export interface RecordsQuery {
  unit?: string; phase?: string; role?: string; attempt?: number; fromSeq?: number; kind?: string[]; limit?: number; cursor?: string;
}

export interface ChangeContentQuery {
  /** The seq of the recorded candidate.change record. */
  seq: number;
  /** Which kept part of it: diff, before, after, proposed or attempted_edits. Kriya classifies the key; an unlisted key is never served. */
  part: string;
}

export interface UIProvider {
  readonly kind: ProviderKind;
  capabilities(signal?: AbortSignal): Promise<ProviderResult<Capabilities>>;
  listSnapshots(signal?: AbortSignal): Promise<ProviderResult<SnapshotList>>;
  acquireSnapshot(workspace?: string): Promise<ProviderResult<SnapshotAcquireResult>>;
  verifySnapshot(snapshotId: string, signal?: AbortSignal): Promise<ProviderResult<SnapshotVerify>>;
  listRuns(snapshotId: string, page?: { limit?: number; cursor?: string }, signal?: AbortSignal): Promise<ProviderResult<HistoryList>>;
  runDetail(snapshotId: string, runId: string, signal?: AbortSignal): Promise<ProviderResult<RunDetail>>;
  workspaceStatus(workspace: string, signal?: AbortSignal): Promise<ProviderResult<WorkspaceStatus>>;
  /** `fromTrace`: `runId` is a trace row id (as listRuns returns it) and the evidence run id is resolved by Kriya from that row's recorded
   * event (needs `snapshotId`). Trace ids and evidence ids differ in every real store, so a module never assumes they are equal. */
  evidenceManifest(runId: string, options?: { snapshotId?: string; fromTrace?: boolean }, signal?: AbortSignal): Promise<ProviderResult<EvidenceManifest>>;
  evidenceVerify(runId: string, signal?: AbortSignal): Promise<ProviderResult<EvidenceVerify>>;
  evidenceRecords(runId: string, query?: RecordsQuery, signal?: AbortSignal): Promise<ProviderResult<EvidenceRecords>>;
  /** ONE authorized part of ONE recorded candidate change. Kriya decides (content_visibility, class table, caps); a refusal is a typed error. */
  evidenceChangeContent(runId: string, query: ChangeContentQuery, signal?: AbortSignal): Promise<ProviderResult<EvidenceChangeContent>>;
  getSetting<K extends keyof HostSettings>(key: K): Promise<HostSettings[K] | undefined>;
  setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]): Promise<void>;
}
