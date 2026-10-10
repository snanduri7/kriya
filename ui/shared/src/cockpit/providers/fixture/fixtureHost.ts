/**
 * An in-memory HostAdapter that answers KUP requests from the deterministic scenarios. It speaks the same wire contract Kriya does
 * (full KUP envelopes with typed errors), and the provider layer validates every answer against the same generated schemas, so
 * FixtureProvider = HostProvider over this host. DEV/TEST ONLY: nothing outside fixture code and tests may import this file.
 */
import type { HostAdapter, HostInfo, HostSettings, OpenInIdeRequest, OpenInIdeResult } from '../../../host/HostAdapter';
import type { KupEnvelope, KupRequest } from '../../../model/kup';
import { SCENARIOS, type Scenario, type SeedRecord } from './scenarios';

// The content classes Kriya's SECURITY_AUTHORITY table knows (kriya/kup/policy.py BLOB_CLASSES); anything else is unclassified and never served.
const CANDIDATE_PARTS = ['before', 'after', 'diff', 'proposed', 'attempted_edits'];

export const FIXTURE_SNAPSHOT_ID = '20261010T090000000000Z-f1c70001';
const OBSERVED = '2026-10-10T09:30:00Z';
const SOURCE = { state_directory: '/fixture/state', trace_database: '/fixture/state/traces.db' };
const EVIDENCE_SOURCE = { ...SOURCE, evidence_directory: '/fixture/state/attempt-evidence' };
const HEAD = (records: readonly SeedRecord[]) => 'sha256:' + String(records.length).padStart(2, '0').repeat(32).slice(0, 64);

export interface FixtureHostOptions {
  /** capabilities.features.evidence_content. false (the production default) = content is not authorized. */
  evidenceContent?: boolean;
  scenarios?: readonly Scenario[];
}

type Json = Record<string, unknown>;

export class FixtureHost implements HostAdapter {
  private readonly scenarios: readonly Scenario[];
  private readonly evidenceContent: boolean;
  private settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace', configDirectory: null, kriyaExecutable: null };
  calls: KupRequest[] = [];

  constructor(options: FixtureHostOptions = {}) {
    this.scenarios = options.scenarios ?? SCENARIOS;
    this.evidenceContent = options.evidenceContent ?? false;
  }

  private env(operation: string, data: unknown, consistency: Json, source: Json | null = SOURCE): KupEnvelope {
    return { schema_version: 1, operation, request_id: 'fixture', observed_at: OBSERVED, source, consistency, data, error: null } as unknown as KupEnvelope;
  }
  private err(operation: string, code: string, message: string, extra: Json = {}): KupEnvelope {
    return { schema_version: 1, operation, request_id: 'fixture', observed_at: OBSERVED, source: null, consistency: null, data: null, error: { code, message, database_state: null, ...extra } } as unknown as KupEnvelope;
  }
  private live = { kind: 'live_observation', live_stream: false };
  private snapshotConsistency = () => ({ kind: 'snapshot_copy', live_stream: false, snapshot_id: FIXTURE_SNAPSHOT_ID });
  private snapshotSource = () => ({ ...SOURCE, snapshot_directory: `/fixture/state/kup-snapshots/${FIXTURE_SNAPSHOT_ID}`, snapshot_id: FIXTURE_SNAPSHOT_ID });

  private capabilities(): Json {
    return {
      kup_versions: [1],
      operations: ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify', 'evidence.manifest', 'evidence.verify', 'evidence.records', 'evidence.change_content'],
      identity: { kriya_version: 'fixture', commit: null, build_provenance: 'fixture' },
      limits: { list_default: 50, list_max: 200, max_response_bytes: 8388608, evidence_page_default: 200, evidence_page_max: 1000, evidence_page_max_bytes: 4194304, evidence_content_max_bytes: 1048576 },
      features: {
        capability_set: '1.1', snapshot: true, prompt: true, evidence: true, evidence_content: this.evidenceContent, evidence_content_classes: this.evidenceContent ? ['candidate_content'] : [], qualification: false, authority: false,
        knowledge: false, tools: false, experiments: false, live: false, actions: false,
        consistency_kinds: ['snapshot_copy', 'live_observation', 'not_applicable', 'sealed_store'],
      },
    };
  }

  private snapshotSummary(): Json {
    const meta = { size: 1048576, mtime_ns: 1, inode: 1, wal_size: null, shm_size: null, journal_size: null };
    return { snapshot_id: FIXTURE_SNAPSHOT_ID, acquisition_started_at: '2026-10-10T09:00:00.000000Z', acquisition_completed_at: '2026-10-10T09:00:00.010000Z', source: SOURCE.trace_database, source_metadata_at_acquisition: meta, source_metadata_now: meta, source_metadata_changed: false, rows: this.scenarios.length, size: 1048576, sqlite_version: '3.53.4' };
  }

  private summary(s: Scenario): Json {
    return { run_id: s.id, timestamp: s.timestamp, goal: s.goal, duration_sec: s.durationSec, attempts: s.attempts, status: s.status, failure_category: s.failureCategory, files_modified: null,
      milestone_group_id: s.milestone.groupId, milestone_index: s.milestone.index, milestone_total: s.milestone.total };
  }

  private detail(s: Scenario): Json {
    const absent = (reason: string, col: string): Json => ({ availability: 'not_recorded', provenance: `runs.${col}`, reason, data: null });
    const rec = (col: string, data: unknown): Json => ({ availability: 'recorded', provenance: `runs.${col}`, reason: null, data });
    return {
      run: this.summary(s), fields: {},
      run_events: rec('run_events', this.storeEvent(s) ? [{ kind: 'evidence.attempt_store', attempt: 0, source: 'workflow.run_generation_workflow', authority: 'auxiliary', message: 'attempt evidence store', failure_type: null, operation: null, details: this.storeEvent(s), created_at: 2 }] : []), evidence_records: absent('fixture', 'evidence_records'), gate_outcomes: absent('fixture', 'gate_outcomes'), model_hops: absent('fixture', 'model_hops'),
      generation_metrics: absent('fixture', 'generation_metrics'), failure_report: absent('fixture', 'failure_report'),
      context: absent('not persisted', 'not_persisted'), attribution: absent('not persisted', 'not_persisted'), diagnostics: absent('not persisted', 'not_persisted'),
      comparisons: absent('not persisted', 'not_persisted'), output: absent('not persisted', 'not_persisted'),
    };
  }

  /** The details of the trace row's recorded evidence.attempt_store event (what the real workflow writes), or null when none was recorded. */
  private storeEvent(s: Scenario): Json | null {
    if (s.seed.store === 'absent') return s.seed.absence.recorded_event;
    return { run_id: s.evidenceId, status: s.seed.sealed ? 'CLOSED' : 'OPEN', capture: s.seed.capture, seq_at_row: 4, path: `/fixture/state/attempt-evidence/${s.evidenceId}` };
  }

  private seal(s: Extract<Scenario['seed'], { store: 'present' }>): Json | null {
    if (!s.sealed) return null;
    return { schema: 'kriya.attempt_evidence/1', final_seq: s.records.length, head_digest: HEAD(s.records), record_count: s.records.length, blob_count: s.records.reduce((n, r) => n + Object.keys(r.blobs).length, 0), complete: true, gaps: 0, degraded: [], closed_reason: 'run_closed' };
  }

  private manifest(s: Scenario | undefined, runId: string, snapshotAsked: boolean, fromTrace: boolean): KupEnvelope {
    const event = s ? this.storeEvent(s) : null;
    const evidenceId = s?.evidenceId ?? null;
    const link = fromTrace && s && event ? { trace_run_id: s.id, evidence_run_id: evidenceId, resolved_from: 'history.detail run_events[evidence.attempt_store]', recorded_event: event } : null;
    const reportedId = fromTrace ? evidenceId : runId;
    if (!s || s.seed.store === 'absent') {
      const absence = s?.seed.store === 'absent' ? s.seed.absence : { reason: 'unknown', detail: `run ${runId} is not in snapshot ${FIXTURE_SNAPSHOT_ID}`, recorded_event: null };
      return this.env('evidence.manifest', { run_id: reportedId, store: 'absent', manifest: null, seal: null, line_count: null, bytes_on_disk: null, blob_file_count: null,
        absence: { reason: absence.reason, derived_from: 'history.detail run_events[evidence.attempt_store]', detail: absence.detail, snapshot_id: snapshotAsked ? FIXTURE_SNAPSHOT_ID : null, recorded_event: absence.recorded_event }, link }, this.live, EVIDENCE_SOURCE);
    }
    const seed = s.seed;
    return this.env('evidence.manifest', {
      run_id: s.evidenceId, store: 'present',
      manifest: { schema: 'kriya.attempt_evidence/1', writer_version: '1', redaction_policy_version: '1', capture: seed.capture, run_id: s.evidenceId, ...seed.manifestExtra },
      seal: this.seal(seed), line_count: seed.records.length, bytes_on_disk: 4096 * seed.records.length, blob_file_count: seed.records.reduce((n, r) => n + Object.keys(r.blobs).length, 0), absence: null, link,
    }, this.live, EVIDENCE_SOURCE);
  }

  private verify(runId: string, s: Scenario | undefined): KupEnvelope {
    if (!s || s.seed.store === 'absent') return this.env('evidence.verify', { run_id: runId, status: 'NOT_FOUND', record_count: 0, sealed: false, broken_seq: null, detail: 'no store for this run', seal: null, blob_problems: [], head_digest: null, verified_at: OBSERVED }, this.live, EVIDENCE_SOURCE);
    const seed = s.seed;
    const head = HEAD(seed.records);
    const data = { run_id: runId, status: seed.sealed ? 'VERIFIED' : 'UNSEALED', record_count: seed.records.length, sealed: seed.sealed, broken_seq: null, detail: '', seal: this.seal(seed), blob_problems: [], head_digest: head, verified_at: OBSERVED };
    return this.env('evidence.verify', data, seed.sealed ? { kind: 'sealed_store', live_stream: false, head_digest: head } : this.live, EVIDENCE_SOURCE);
  }

  private changeContent(runId: string, s: Scenario | undefined, seq: number, part: string): KupEnvelope {
    const op = 'evidence.change_content';
    // Same order as Kriya: class table, then Kriya's visibility, then the store.
    if (!CANDIDATE_PARTS.includes(part)) return this.err(op, 'EVIDENCE_CONTENT_POLICY', `${part} is not a classified content key; unclassified content is never served`, { content_class: 'unclassified', reason: 'unclassified_key' });
    if (!this.evidenceContent) return this.err(op, 'EVIDENCE_CONTENT_POLICY', "Kriya's content visibility policy (evidence.attempt_recorder.content_visibility) is off", { content_class: 'candidate_content', reason: 'content_visibility_off' });
    if (!s || s.seed.store === 'absent') return this.err(op, 'EVIDENCE_STORE_NOT_FOUND', `no attempt-evidence store for run ${runId}`);
    const record = s.seed.records[seq - 1];
    if (!record || record.kind !== 'candidate.change') return this.err(op, 'INVALID_REQUEST', `record ${seq} of run ${runId} is not a candidate.change record`);
    const ref = record.blobs[part];
    if (ref === undefined) {
      const reason = part in record.content_digests ? 'digest_only' : 'part_not_recorded';
      return this.err(op, 'EVIDENCE_CONTENT_UNAVAILABLE', `record ${seq} kept no bytes for ${part} (${reason})`, { reason });
    }
    const text = s.seed.texts[ref] ?? '';
    const head = HEAD(s.seed.records);
    return this.env(op, { run_id: runId, seq, part, content_class: 'candidate_content', path: typeof record.payload.path === 'string' ? record.payload.path : null, attempt: record.attempt_number, ref, bytes_total: text.length, encoding: 'utf-8', content: text, digest_ok: true, head_digest: head },
      s.seed.sealed ? { kind: 'sealed_store', live_stream: false, head_digest: head } : this.live, EVIDENCE_SOURCE);
  }

  private records(runId: string, s: Scenario | undefined, q: Json): KupEnvelope {
    if (!s || s.seed.store === 'absent') return this.err('evidence.records', 'EVIDENCE_STORE_NOT_FOUND', `no attempt-evidence store for run ${runId}`);
    const seed = s.seed;
    const head = HEAD(seed.records);
    const kinds = q.kind as string[] | undefined;
    const matches = (r: SeedRecord) => (q.unit === undefined || r.unit_id === q.unit) && (q.phase === undefined || r.phase === q.phase) && (q.role === undefined || r.role === q.role)
      && (q.attempt === undefined || r.attempt_number === q.attempt) && (kinds === undefined || kinds.includes(r.kind));
    const after = q.cursor !== undefined ? Number(JSON.parse(atob(String(q.cursor).replace(/-/g, '+').replace(/_/g, '/'))).l) : 0;
    const from = Math.max((q.from_seq as number | undefined) ?? 1, after + 1);
    const limit = (q.limit as number | undefined) ?? 200;
    const pool = seed.records.filter((r) => r.seq >= from && matches(r));
    const page = pool.slice(0, limit);
    const last = page[page.length - 1];
    const next = pool.length > limit && last ? btoa(JSON.stringify({ l: last.seq })).replace(/\+/g, '-').replace(/\//g, '_') : null;
    const filters: Json = {};
    for (const key of ['unit', 'phase', 'role', 'attempt', 'from_seq', 'kind']) if (q[key] !== undefined) filters[key] = q[key];
    return this.env('evidence.records', { run_id: runId, head_digest: head, records: page, next_cursor: next, filters_applied: filters, truncated: false, truncated_tail: false },
      seed.sealed ? { kind: 'sealed_store', live_stream: false, head_digest: head } : this.live, EVIDENCE_SOURCE);
  }

  async query(request: KupRequest): Promise<KupEnvelope> {
    this.calls.push(request);
    const find = (id: string) => this.scenarios.find((s) => s.id === id);
    const findEvidence = (id: string) => this.scenarios.find((s) => s.evidenceId === id);
    switch (request.operation) {
      case 'capabilities': return this.env('capabilities', this.capabilities(), { kind: 'not_applicable', live_stream: false });
      case 'snapshot.list': return this.env('snapshot.list', { snapshot_directory: '/fixture/state/kup-snapshots', snapshots: [this.snapshotSummary()], retain: 3 }, this.live);
      case 'snapshot.acquire': return this.env('snapshot.acquire', { ...this.snapshotSummary(), orphans_removed: [], pruned: [], backup_steps: 1, duration_ms: 10 }, this.snapshotConsistency(), this.snapshotSource());
      case 'snapshot.prune': return this.env('snapshot.prune', { removed: [], orphans_removed: [], kept: [FIXTURE_SNAPSHOT_ID] }, this.live);
      case 'snapshot.verify':
        if (request.snapshot_id !== FIXTURE_SNAPSHOT_ID) return this.err('snapshot.verify', 'SNAPSHOT_UNAVAILABLE', `snapshot ${request.snapshot_id} is not published`, { snapshot_id: request.snapshot_id });
        return this.env('snapshot.verify', { snapshot_id: FIXTURE_SNAPSHOT_ID, digest_verified: true, sha256: 'f1c70001'.repeat(8), size: 1048576, verified_at: OBSERVED, duration_ms: 1, guarantee: 'digest verified at this request; size and mtime are checked on every later query' }, this.live, this.snapshotSource());
      case 'history.list': {
        if (request.snapshot_id !== FIXTURE_SNAPSHOT_ID) return this.err('history.list', 'SNAPSHOT_UNAVAILABLE', `snapshot ${request.snapshot_id} is not published`, { snapshot_id: request.snapshot_id });
        const ordered = [...this.scenarios].sort((a, b) => b.timestamp.localeCompare(a.timestamp) || b.id.localeCompare(a.id));
        return this.env('history.list', { runs: ordered.map((s) => this.summary(s)), next_cursor: null }, this.snapshotConsistency(), this.snapshotSource());
      }
      case 'history.detail': {
        const s = find(request.run_id);
        if (!s) return this.err('history.detail', 'INVALID_REQUEST', `no run ${request.run_id} in snapshot ${request.snapshot_id}`);
        return this.env('history.detail', this.detail(s), this.snapshotConsistency(), this.snapshotSource());
      }
      case 'history.prompt': return this.err('history.prompt', 'INVALID_REQUEST', 'the fixture host stores no prompts');
      case 'workspace.status': return this.env('workspace.status', { workspace: request.workspace, run_active: false, status: 'CLEAN', exit_code: 0, assessment: { workspace_path: request.workspace, status: 'CLEAN', run_active: null, records: [], evidence: [], unreadable_records: [], evidence_error: null } }, this.live, null);
      case 'evidence.manifest':
        if (request.from_trace === true && !request.snapshot_id) return this.err('evidence.manifest', 'INVALID_REQUEST', 'from_trace needs the snapshot_id the trace row is read from');
        return request.from_trace === true ? this.manifest(find(request.run_id), request.run_id, true, true) : this.manifest(findEvidence(request.run_id), request.run_id, request.snapshot_id !== undefined, false);
      case 'evidence.verify': return this.verify(request.run_id, findEvidence(request.run_id));
      case 'evidence.change_content': return this.changeContent(request.run_id, findEvidence(request.run_id), request.seq, request.part);
      case 'evidence.records': return this.records(request.run_id, findEvidence(request.run_id), request as unknown as Json);
      default: { const never: never = request; return this.err('unknown', 'INVALID_REQUEST', `fixture host does not answer ${JSON.stringify(never)}`); }
    }
  }

  async openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { return { ok: false, editor: 'vscode', verified: false, message: `fixture host cannot open ${request.path}` }; }
  async copyToClipboard(): Promise<void> { /* no clipboard in fixtures */ }
  async getSetting<K extends keyof HostSettings>(key: K) { return this.settings[key] as HostSettings[K] | undefined; }
  async setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]) { this.settings[key] = value; }
  hostInfo(): HostInfo { return { kind: 'fixture', hostVersion: '0', fixtureMode: true, configDirectory: '/fixture/config', configDirectorySource: 'setting', configDirectoryProblem: null }; }
}
