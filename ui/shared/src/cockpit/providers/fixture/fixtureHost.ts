/**
 * An in-memory HostAdapter that answers KUP requests from the deterministic scenarios. It speaks the same wire contract Kriya does
 * (full KUP envelopes with typed errors), and the provider layer validates every answer against the same generated schemas, so
 * FixtureProvider = HostProvider over this host. DEV/TEST ONLY: nothing outside fixture code and tests may import this file.
 */
import type { ExecutionHostPort, HostAdapter, HostInfo, HostSettings, MenuCommand, OpenInIdeRequest, OpenInIdeResult, WorkspaceChoice, WorkspaceHost } from '../../../host/HostAdapter';
import type { KupEnvelope, KupRequest } from '../../../model/kup';
import { FixtureExecution, LAUNCHED_TRACE_ID, LAUNCH_ID, type FixtureExecutionOptions } from './fixtureExecution';
import { SCENARIOS, type Scenario, type SeedRecord } from './scenarios';

// The content classes Kriya's SECURITY_AUTHORITY table knows (kriya/kup/policy.py BLOB_CLASSES); anything else is unclassified and never served.
const CANDIDATE_PARTS = ['before', 'after', 'diff', 'proposed', 'attempted_edits'];
const MODEL_INPUT_PARTS = ['messages', 'tools', 'wire_body'];

export const FIXTURE_SNAPSHOT_ID = '20261010T090000000000Z-f1c70001';
const OBSERVED = '2026-10-10T09:30:00Z';
const SOURCE = { state_directory: '/fixture/state', trace_database: '/fixture/state/traces.db' };
const EVIDENCE_SOURCE = { ...SOURCE, evidence_directory: '/fixture/state/attempt-evidence' };
const HEAD = (records: readonly SeedRecord[]) => 'sha256:' + String(records.length).padStart(2, '0').repeat(32).slice(0, 64);

/** The folders the fixture host knows how to describe (DEV/TEST ONLY). Each is described by the same fields Kriya reports; none is classified. */
export const FIXTURE_FOLDERS: Readonly<Record<string, { checkout: Json; recovery?: string; runs: 'all' | 'none'; kriyaDirectory?: boolean }>> = {
  '/fixture/workspace': { checkout: { state: 'checkout_root', root: '/fixture/workspace', head: 'b2b5fc411394e307cf9422d8fbb5fed511bba8ce', branch: 'main', detached: false, dirty: false, changed_entries: 0, changed_truncated: false, reason: null }, runs: 'all' },
  '/fixture/dirty': { checkout: { state: 'checkout_root', root: '/fixture/dirty', head: '0f1e2d3c4b5a69788796a5b4c3d2e1f001122334', branch: 'feature/clamp', detached: false, dirty: true, changed_entries: 3, changed_truncated: false, reason: null }, runs: 'none' },
  '/fixture/empty': { checkout: { state: 'not_a_repository', root: null, head: null, branch: null, detached: null, dirty: null, changed_entries: null, changed_truncated: false, reason: null }, runs: 'none' },
  '/fixture/plain': { checkout: { state: 'not_a_repository', root: null, head: null, branch: null, detached: null, dirty: null, changed_entries: null, changed_truncated: false, reason: null }, runs: 'none', kriyaDirectory: true },
  '/fixture/nested': { checkout: { state: 'inside_repository', root: '/fixture/workspace', head: null, branch: null, detached: null, dirty: null, changed_entries: null, changed_truncated: false, reason: null }, runs: 'none' },
  '/fixture/blocked': { checkout: { state: 'checkout_root', root: '/fixture/blocked', head: 'a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1a1', branch: 'main', detached: false, dirty: false, changed_entries: 0, changed_truncated: false, reason: null }, recovery: 'RECOVERY_AVAILABLE', runs: 'none' },
};

export interface FixtureHostOptions {
  /** The folders the scripted chooser returns, in order (null = the person cancels). Empty = every choice is a cancel. */
  choices?: readonly (string | null)[];
  /** Folders remembered from an earlier session (most recent first). */
  recent?: readonly string[];
  /** The workspace already open at construction. Default: '/fixture/workspace' (the cockpit tests start inside a workspace); null = the welcome screen. */
  workspace?: string | null;
  /** capabilities.features.evidence_content. false (the production default) = content is not authorized. */
  evidenceContent?: boolean;
  scenarios?: readonly Scenario[];
  /** Extra published snapshot ids the list reports (never readable: verification of them is refused, as for a pruned snapshot). */
  extraSnapshotIds?: readonly string[];
  /** Scripted Start Run (fixtureExecution.ts). Omit for the default scripted successful run; false = a host that cannot execute. */
  execution?: FixtureExecutionOptions | false;
}

type Json = Record<string, unknown>;

export class FixtureHost implements HostAdapter {
  private readonly baseScenarios: readonly Scenario[];
  private readonly extraSnapshotIds: readonly string[];
  /** The scripted execution (null when the host cannot execute). Tests advance its frames. */
  readonly exec: FixtureExecution | null;
  readonly execution?: ExecutionHostPort;
  private readonly evidenceContent: boolean;
  private settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace', configDirectory: null, kriyaExecutable: null };
  private readonly choices: (string | null)[];
  private recentList: string[];
  private readonly menuListeners = new Set<(command: MenuCommand) => void>();
  /** What the host was asked to do, for assertions: the chooser opened, folders remembered or forgotten. */
  readonly workspaceCalls: string[] = [];
  readonly workspace: WorkspaceHost;
  calls: KupRequest[] = [];

  constructor(options: FixtureHostOptions = {}) {
    this.baseScenarios = options.scenarios ?? SCENARIOS;
    this.extraSnapshotIds = options.extraSnapshotIds ?? [];
    this.exec = options.execution === false ? null : new FixtureExecution(options.execution ?? {});
    if (this.exec) this.execution = this.exec.port;
    this.evidenceContent = options.evidenceContent ?? false;
    this.choices = [...(options.choices ?? [])];
    this.recentList = [...(options.recent ?? [])];
    if (options.workspace !== undefined) this.settings.workspacePath = options.workspace;
    this.workspace = {
      choose: async (): Promise<WorkspaceChoice> => {
        this.workspaceCalls.push('choose');
        const next = this.choices.shift();
        if (this.exec?.active()) return { chosen: false, path: null, refused: 'RUN_ACTIVE' };
        return next === undefined || next === null ? { chosen: false, path: null } : { chosen: true, path: next };
      },
      recent: async () => [...this.recentList],
      remember: async (path) => { this.workspaceCalls.push(`remember:${path}`); this.recentList = [path, ...this.recentList.filter((p) => p !== path)].slice(0, 10); this.settings.workspacePath = path; return [...this.recentList]; },
      forget: async (path) => { this.workspaceCalls.push(`forget:${path}`); this.recentList = this.recentList.filter((p) => p !== path); if (this.settings.workspacePath === path) this.settings.workspacePath = null; return [...this.recentList]; },
      onMenuCommand: (listener) => { this.menuListeners.add(listener); return () => { this.menuListeners.delete(listener); }; },
    };
  }

  /** The scenarios the store holds now: the base set, plus the run this fixture launched once its records are durable (sealed, in history). */
  private get scenarios(): readonly Scenario[] {
    if (!this.exec || !this.exec.ended()) return this.baseScenarios;
    const template = this.baseScenarios.find((s) => s.id === 'fx-greenfield-success') ?? this.baseScenarios[0]!;
    const launched: Scenario = { ...template, id: LAUNCHED_TRACE_ID, evidenceId: LAUNCH_ID, goal: this.exec.goal(), timestamp: '2026-10-10T10:12:00Z', title: 'Run started from the cockpit' };
    return [...this.baseScenarios, launched];
  }

  /** While a launch waits on the human's review its evidence store is live (unsealed) and holds the candidate: the same recorded shapes a finished run's store has. */
  private liveReviewScenario(evidenceId: string): Scenario | undefined {
    if (!this.exec || evidenceId !== LAUNCH_ID || !this.exec.reviewPending()) return undefined;
    const template = this.baseScenarios.find((s) => s.id === 'fx-greenfield-success') ?? this.baseScenarios[0]!;
    return { ...template, id: LAUNCHED_TRACE_ID, evidenceId: LAUNCH_ID, goal: this.exec.goal(), seed: template.seed.store === 'present' ? { ...template.seed, sealed: false } : template.seed };
  }

  /** Test hook: the application menu asks the renderer to open the chooser. */
  menu(command: MenuCommand): void { for (const l of [...this.menuListeners]) l(command); }

  private describeFolder(workspace: string): KupEnvelope {
    const known = FIXTURE_FOLDERS[workspace];
    if (!known) return this.err('workspace.describe', 'WORKSPACE_UNAVAILABLE', `no such folder: ${workspace}`, { reason: 'not_found' });
    const name = workspace.slice(workspace.lastIndexOf('/') + 1);
    return this.env('workspace.describe', {
      workspace, requested: workspace, name, identity: 'ab'.repeat(32), checkout: known.checkout, recovery: { status: known.recovery ?? 'CLEAN', run_active: false },
      kriya_directory_present: known.kriyaDirectory ?? false,
      configuration: { file_in_workspace: false, note: 'The cockpit reads with its own configuration directory (KUP host setting); a kriya.yaml inside the opened folder is not applied to these reads.' },
      state: { directory: SOURCE.state_directory, source: 'env', trace_database_present: true },
    }, this.live, SOURCE);
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
      operations: ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify', 'workspace.describe', 'execution.preflight', 'launch.status', 'evidence.manifest', 'evidence.verify', 'evidence.records', 'evidence.change_content', 'evidence.call_content'],
      identity: { kriya_version: 'fixture', commit: null, build_provenance: 'fixture' },
      limits: { list_default: 50, list_max: 200, max_response_bytes: 8388608, evidence_page_default: 200, evidence_page_max: 1000, evidence_page_max_bytes: 4194304, evidence_content_max_bytes: 1048576 },
      features: {
        capability_set: '1.1', snapshot: true, prompt: true, evidence: true, evidence_content: this.evidenceContent, evidence_content_classes: this.evidenceContent ? ['candidate_content', 'model_input'] : [], qualification: false, authority: false,
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
      run_events: rec('run_events', [...(s.s1Events ?? []), ...(this.storeEvent(s) ? [{ kind: 'evidence.attempt_store', attempt: 0, source: 'workflow.run_generation_workflow', authority: 'auxiliary', message: 'attempt evidence store', failure_type: null, operation: null, details: this.storeEvent(s), created_at: 2 }] : [])]),
      evidence_records: s.s1Evidence ? rec('evidence_records', s.s1Evidence) : absent('fixture', 'evidence_records'), gate_outcomes: absent('fixture', 'gate_outcomes'), model_hops: absent('fixture', 'model_hops'),
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

  private changeContent(runId: string, s: Scenario | undefined, seq: number, part: string, op: 'evidence.change_content' | 'evidence.call_content'): KupEnvelope {
    const candidate = op === 'evidence.change_content';
    const served = candidate ? 'candidate_content' : 'model_input';
    const classOf = CANDIDATE_PARTS.includes(part) ? 'candidate_content' : MODEL_INPUT_PARTS.includes(part) ? 'model_input' : 'unclassified';
    // Same order as Kriya: class table, then Kriya's visibility, then the store.
    if (classOf === 'unclassified') return this.err(op, 'EVIDENCE_CONTENT_POLICY', `${part} is not a classified content key; unclassified content is never served`, { content_class: 'unclassified', reason: 'unclassified_key' });
    if (classOf !== served) return this.err(op, 'EVIDENCE_CONTENT_POLICY', `${part} is ${classOf} content, which ${op} does not serve`, { content_class: classOf, reason: 'class_not_served_here' });
    if (!this.evidenceContent) return this.err(op, 'EVIDENCE_CONTENT_POLICY', "Kriya's content visibility policy (evidence.attempt_recorder.content_visibility) is off", { content_class: served, reason: 'content_visibility_off' });
    if (!s || s.seed.store === 'absent') return this.err(op, 'EVIDENCE_STORE_NOT_FOUND', `no attempt-evidence store for run ${runId}`);
    const record = s.seed.records[seq - 1];
    const kind = candidate ? 'candidate.change' : 'model.request';
    if (!record || record.kind !== kind) return this.err(op, 'INVALID_REQUEST', `record ${seq} of run ${runId} is not a ${kind} record`);
    const ref = record.blobs[part];
    if (ref === undefined) {
      const reason = part in record.content_digests ? 'digest_only' : 'part_not_recorded';
      return this.err(op, 'EVIDENCE_CONTENT_UNAVAILABLE', `record ${seq} kept no bytes for ${part} (${reason})`, { reason });
    }
    const text = s.seed.texts[ref] ?? '';
    const head = HEAD(s.seed.records);
    const base = { run_id: runId, seq, part, content_class: served, attempt: record.attempt_number, ref, bytes_total: text.length, encoding: 'utf-8', content: text, digest_ok: true, head_digest: head };
    const data = candidate ? { ...base, path: typeof record.payload.path === 'string' ? record.payload.path : null } : { ...base, role: record.role, call_seq: record.call_seq };
    return this.env(op, data, s.seed.sealed ? { kind: 'sealed_store', live_stream: false, head_digest: head } : this.live, EVIDENCE_SOURCE);
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
    const findEvidence = (id: string) => this.scenarios.find((s) => s.evidenceId === id) ?? this.liveReviewScenario(id);
    switch (request.operation) {
      case 'capabilities': return this.env('capabilities', this.capabilities(), { kind: 'not_applicable', live_stream: false });
      case 'snapshot.list': return this.env('snapshot.list', { snapshot_directory: '/fixture/state/kup-snapshots', snapshots: [this.snapshotSummary(), ...this.extraSnapshotIds.map((id) => ({ ...this.snapshotSummary(), snapshot_id: id, acquisition_completed_at: '2026-10-09T09:00:00.010000Z' }))], retain: 3 }, this.live);
      case 'snapshot.acquire': return this.env('snapshot.acquire', { ...this.snapshotSummary(), orphans_removed: [], pruned: [], backup_steps: 1, duration_ms: 10 }, this.snapshotConsistency(), this.snapshotSource());
      case 'snapshot.prune': return this.env('snapshot.prune', { removed: [], orphans_removed: [], kept: [FIXTURE_SNAPSHOT_ID] }, this.live);
      case 'snapshot.verify':
        if (request.snapshot_id !== FIXTURE_SNAPSHOT_ID) return this.err('snapshot.verify', 'SNAPSHOT_UNAVAILABLE', `snapshot ${request.snapshot_id} is not published`, { snapshot_id: request.snapshot_id });
        return this.env('snapshot.verify', { snapshot_id: FIXTURE_SNAPSHOT_ID, digest_verified: true, sha256: 'f1c70001'.repeat(8), size: 1048576, verified_at: OBSERVED, duration_ms: 1, guarantee: 'digest verified at this request; size and mtime are checked on every later query' }, this.live, this.snapshotSource());
      case 'history.list': {
        if (request.snapshot_id !== FIXTURE_SNAPSHOT_ID) return this.err('history.list', 'SNAPSHOT_UNAVAILABLE', `snapshot ${request.snapshot_id} is not published`, { snapshot_id: request.snapshot_id });
        const ordered = [...this.scenarios].sort((a, b) => b.timestamp.localeCompare(a.timestamp) || b.id.localeCompare(a.id));
        if (request.workspace !== undefined) {
          // Scoped like Kriya: only a workspace whose manifests record it has runs; the rest of the store is counted, never listed.
          const mine = FIXTURE_FOLDERS[request.workspace]?.runs === 'all' ? ordered : [];
          return this.env('history.list', { runs: mine.map((s) => this.summary(s)), next_cursor: null, workspace_scope: { workspace_identity: 'ab'.repeat(32), basis: 'evidence manifest workspace_identity', matched: mine.length, other: ordered.length - mine.length, unattributed: 2, scanned: ordered.length + 2, truncated: false } }, this.snapshotConsistency(), this.snapshotSource());
        }
        return this.env('history.list', { runs: ordered.map((s) => this.summary(s)), next_cursor: null }, this.snapshotConsistency(), this.snapshotSource());
      }
      case 'history.detail': {
        const s = find(request.run_id);
        if (!s) return this.err('history.detail', 'INVALID_REQUEST', `no run ${request.run_id} in snapshot ${request.snapshot_id}`);
        return this.env('history.detail', this.detail(s), this.snapshotConsistency(), this.snapshotSource());
      }
      case 'history.prompt': return this.err('history.prompt', 'INVALID_REQUEST', 'the fixture host stores no prompts');
      case 'workspace.status': return this.env('workspace.status', { workspace: request.workspace, run_active: false, status: 'CLEAN', exit_code: 0, assessment: { workspace_path: request.workspace, status: 'CLEAN', run_active: null, records: [], evidence: [], unreadable_records: [], evidence_error: null } }, this.live, null);
      case 'workspace.describe': return this.describeFolder(request.workspace);
      case 'execution.preflight': {
        const known = FIXTURE_FOLDERS[request.workspace];
        if (!known || !this.exec) return this.err('execution.preflight', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const blocked = known.recovery && known.recovery !== 'CLEAN' ? { code: 'RECOVERY_REQUIRED', message: `the folder's last run left it in state ${known.recovery}; recover it (kriya runs recover) before starting another` } : null;
        return this.env('execution.preflight', this.exec.preflight(request.workspace, SOURCE.state_directory, blocked), this.live, SOURCE);
      }
      case 'launch.status': {
        const status = this.exec?.launchStatus() ?? null;
        if (!status || request.launch_id !== LAUNCH_ID) return this.err('launch.status', 'LAUNCH_NOT_FOUND', `no launch ${request.launch_id} is recorded for this folder`, { reason: 'not_recorded' });
        return this.env('launch.status', status, this.live, SOURCE);
      }
      case 'evidence.manifest':
        if (request.from_trace === true && !request.snapshot_id) return this.err('evidence.manifest', 'INVALID_REQUEST', 'from_trace needs the snapshot_id the trace row is read from');
        return request.from_trace === true ? this.manifest(find(request.run_id), request.run_id, true, true) : this.manifest(findEvidence(request.run_id), request.run_id, request.snapshot_id !== undefined, false);
      case 'evidence.verify': return this.verify(request.run_id, findEvidence(request.run_id));
      case 'evidence.change_content': return this.changeContent(request.run_id, findEvidence(request.run_id), request.seq, request.part, 'evidence.change_content');
      case 'evidence.call_content': return this.changeContent(request.run_id, findEvidence(request.run_id), request.seq, request.part, 'evidence.call_content');
      case 'evidence.records': {
        if (this.exec && request.run_id === LAUNCH_ID && !this.exec.ended() && !this.exec.reviewPending()) {
          const page = this.exec.records(request.from_seq ?? 1, request.limit ?? 200);
          const head = 'sha256:' + String(this.exec.lineCount()).padStart(2, '0').repeat(32).slice(0, 64);
          return this.env('evidence.records', { run_id: LAUNCH_ID, head_digest: head, records: page, next_cursor: null, filters_applied: {}, truncated: false, truncated_tail: false }, this.live, EVIDENCE_SOURCE);
        }
        return this.records(request.run_id, findEvidence(request.run_id), request as unknown as Json);
      }
      default: { const never: never = request; return this.err('unknown', 'INVALID_REQUEST', `fixture host does not answer ${JSON.stringify(never)}`); }
    }
  }

  /** What the person asked the host to do, for assertions (the fixture opens and copies nothing). */
  readonly ideCalls: OpenInIdeRequest[] = [];
  readonly copied: string[] = [];
  async openInIde(request: OpenInIdeRequest): Promise<OpenInIdeResult> { this.ideCalls.push(request); return { ok: true, editor: 'vscode', verified: false, message: `fixture: code --goto ${request.path}${request.line ? `:${request.line}` : ''}` }; }
  async copyToClipboard(text: string): Promise<void> { this.copied.push(text); }
  async getSetting<K extends keyof HostSettings>(key: K) { return this.settings[key] as HostSettings[K] | undefined; }
  async setSetting<K extends keyof HostSettings>(key: K, value: HostSettings[K]) { this.settings[key] = value; }
  hostInfo(): HostInfo { return { kind: 'fixture', hostVersion: '0', fixtureMode: true, configDirectory: '/fixture/config', configDirectorySource: 'setting', configDirectoryProblem: null }; }
}
