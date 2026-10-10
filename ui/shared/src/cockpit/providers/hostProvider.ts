/**
 * The single UIProvider implementation over a HostAdapter (the one host boundary: typed IPC to Electron main, which spawns the
 * allowlisted kriya command). KriyaProvider and the dev/test provider are both this class over different hosts, so they cannot differ
 * in behaviour: every response is validated against the generated KUP schemas, a transport failure or an invalid envelope is a
 * typed error, and there is no fallback path of any kind.
 */
import { validate, validateData } from '@kriya-ui/kup';
import type { HostAdapter, HostSettings } from '../../host/HostAdapter';
import type { Capabilities, EvidenceCallContent, EvidenceChangeContent, EvidenceManifest, EvidenceRecords, EvidenceVerify, HistoryList, KupRequest, RunDetail, SnapshotAcquireResult, SnapshotList, SnapshotVerify, WorkspaceDescribe, WorkspaceStatus, ExecutionPreflight, LaunchStatus } from '../../model/kup';
import type { ChangeContentQuery, ProviderKind, ProviderResult, RecordsQuery, UIProvider } from '../core/provider';

export class HostProvider implements UIProvider {
  constructor(private readonly host: HostAdapter, readonly kind: ProviderKind) {}

  get desktop() { return this.host.workspace ?? null; }
  get execution() { return this.host.execution ?? null; }

  private async call<T>(request: KupRequest, signal?: AbortSignal): Promise<ProviderResult<T>> {
    let raw: unknown;
    try {
      raw = await this.host.query(request, signal);
    } catch (error) {
      return { ok: false, error: { code: 'HOST_ERROR', message: error instanceof Error ? error.message : String(error), extra: {} } };
    }
    const checked = validate.envelope(raw);
    if (!checked.ok) return { ok: false, error: { code: 'INVALID_RESPONSE', message: `response is not a KUP envelope: ${checked.errors.slice(0, 3).join('; ')}`, extra: {} } };
    const env = checked.value;
    if (env.operation !== request.operation) return { ok: false, error: { code: 'INVALID_RESPONSE', message: `asked ${request.operation}, answered ${env.operation}`, extra: {} } };
    if (env.error) {
      const { code, message, ...extra } = env.error as { code: string; message: string } & Record<string, unknown>;
      return { ok: false, error: { code, message, extra } };
    }
    const data = validateData(env.operation, env.data);
    if (!data.ok) return { ok: false, error: { code: 'INVALID_RESPONSE', message: `${env.operation} payload does not match its schema: ${data.errors.slice(0, 3).join('; ')}`, extra: {} } };
    return {
      ok: true,
      data: env.data as T,
      provenance: { operation: env.operation, source: env.source, consistency: env.consistency, schemaVersion: env.schema_version, requestId: env.request_id, observedAt: env.observed_at },
    };
  }

  capabilities(signal?: AbortSignal) { return this.call<Capabilities>({ operation: 'capabilities' }, signal); }
  listSnapshots(signal?: AbortSignal) { return this.call<SnapshotList>({ operation: 'snapshot.list' }, signal); }
  acquireSnapshot(workspace?: string) { return this.call<SnapshotAcquireResult>(workspace ? { operation: 'snapshot.acquire', workspace } : { operation: 'snapshot.acquire' }); }
  verifySnapshot(snapshotId: string, signal?: AbortSignal) { return this.call<SnapshotVerify>({ operation: 'snapshot.verify', snapshot_id: snapshotId }, signal); }
  listRuns(snapshotId: string, page: { limit?: number; cursor?: string; workspace?: string } = {}, signal?: AbortSignal) {
    return this.call<HistoryList>({ operation: 'history.list', snapshot_id: snapshotId, ...(page.limit !== undefined ? { limit: page.limit } : {}), ...(page.cursor !== undefined ? { cursor: page.cursor } : {}), ...(page.workspace !== undefined ? { workspace: page.workspace } : {}) }, signal);
  }
  runDetail(snapshotId: string, runId: string, signal?: AbortSignal) { return this.call<RunDetail>({ operation: 'history.detail', snapshot_id: snapshotId, run_id: runId }, signal); }
  workspaceStatus(workspace: string, signal?: AbortSignal) { return this.call<WorkspaceStatus>({ operation: 'workspace.status', workspace }, signal); }
  describeWorkspace(workspace: string, signal?: AbortSignal) { return this.call<WorkspaceDescribe>({ operation: 'workspace.describe', workspace }, signal); }
  executionPreflight(workspace: string, signal?: AbortSignal) { return this.call<ExecutionPreflight>({ operation: 'execution.preflight', workspace }, signal); }
  launchStatus(workspace: string, launchId: string, signal?: AbortSignal) { return this.call<LaunchStatus>({ operation: 'launch.status', workspace, launch_id: launchId }, signal); }
  evidenceManifest(runId: string, options: { snapshotId?: string; fromTrace?: boolean } = {}, signal?: AbortSignal) {
    return this.call<EvidenceManifest>({ operation: 'evidence.manifest', run_id: runId, ...(options.snapshotId ? { snapshot_id: options.snapshotId } : {}), ...(options.fromTrace ? { from_trace: true } : {}) }, signal);
  }
  evidenceVerify(runId: string, signal?: AbortSignal) { return this.call<EvidenceVerify>({ operation: 'evidence.verify', run_id: runId }, signal); }
  evidenceRecords(runId: string, q: RecordsQuery = {}, signal?: AbortSignal) {
    const request = {
      operation: 'evidence.records', run_id: runId,
      ...(q.unit !== undefined ? { unit: q.unit } : {}), ...(q.phase !== undefined ? { phase: q.phase } : {}), ...(q.role !== undefined ? { role: q.role } : {}),
      ...(q.attempt !== undefined ? { attempt: q.attempt } : {}), ...(q.fromSeq !== undefined ? { from_seq: q.fromSeq } : {}), ...(q.kind !== undefined ? { kind: q.kind } : {}),
      ...(q.limit !== undefined ? { limit: q.limit } : {}), ...(q.cursor !== undefined ? { cursor: q.cursor } : {}),
    } as KupRequest;
    return this.call<EvidenceRecords>(request, signal);
  }
  evidenceChangeContent(runId: string, q: ChangeContentQuery, signal?: AbortSignal) {
    return this.call<EvidenceChangeContent>({ operation: 'evidence.change_content', run_id: runId, seq: q.seq, part: q.part }, signal);
  }
  evidenceCallContent(runId: string, q: ChangeContentQuery, signal?: AbortSignal) {
    return this.call<EvidenceCallContent>({ operation: 'evidence.call_content', run_id: runId, seq: q.seq, part: q.part }, signal);
  }
  getSetting<K extends keyof HostSettings>(key: K) { return this.host.getSetting(key); }
  setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]) { return this.host.setSetting(key, value); }
}
