/**
 * An in-memory HostAdapter that answers KUP requests from the deterministic scenarios. It speaks the same wire contract Kriya does
 * (full KUP envelopes with typed errors), and the provider layer validates every answer against the same generated schemas, so
 * FixtureProvider = HostProvider over this host. DEV/TEST ONLY: nothing outside fixture code and tests may import this file.
 */
import type { ExecutionHostPort, HostAdapter, HostInfo, HostSettings, MenuCommand, OpenInIdeRequest, OpenInIdeResult, ProjectCreateResult, ProjectParentChoice, WorkspaceChoice, WorkspaceHost } from '../../../host/HostAdapter';
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

/** What the fixture's `execution.context` / `execution.model_probe` report (DEV/TEST ONLY): the shapes Kriya returns, with scripted content. */
export type FixtureContextMode = 'full' | 'empty' | 'unavailable' | 'many';
export type FixtureProbeMode = 'present' | 'missing' | 'unreachable' | 'not_probed' | 'unavailable';

const fixtureRouting = (probe = false) => ['planner', 'architect', 'developer', 'reviewer', 'run_verifier', 'skill_gap', 'spec_compliance'].map((role) => ({
  role, provider: 'openai', model: role === 'planner' ? 'fixture-planner:14b' : 'fixture-model:7b', endpoint: 'http://localhost:11434/v1', context_window: 32768,
  source: role === 'planner' ? 'override' : 'default', fallback: role === 'developer' && !probe ? ['fixture-fallback:3b'] : [],
}));
const fixtureSkills = (mode: FixtureContextMode) => {
  const base = [
    { name: 'python-testing', description: 'Pytest conventions for generated tests', category: 'Language', tags: ['python', 'pytest'], supported_versions: '*', verified: true, verified_context: 'passed the runtime verification gate', verified_at: '2026-10-01', gap_acknowledged: false, origin: 'kriya_install', rule_count: 12, has_instructions: true, example_count: 2, staged_rule_count: 0 },
    { name: 'spring-boot', description: 'Spring Boot service layout', category: 'Framework', tags: ['java', 'spring'], supported_versions: '>=3.0.0', verified: false, verified_context: null, verified_at: null, gap_acknowledged: false, origin: 'workspace_or_configured', rule_count: 5, has_instructions: false, example_count: 0, staged_rule_count: 3 },
  ];
  const items = mode === 'empty' ? [] : mode === 'many' ? Array.from({ length: 300 }, (_, i) => ({ ...base[i % 2]!, name: `skill-${String(i).padStart(3, '0')}` })) : base;
  return { items, total: items.length, truncated: false, selection: 'automatic', selection_note: 'Kriya selects Skills automatically for each run from the goal and the repository; there is no manual selection. The Skills a run used are recorded with the run.', management: 'Skills are created, reviewed and promoted with `kriya skills` in a terminal; this application does not change them.' };
};
const fixtureTools = (mode: FixtureContextMode) => ({
  plugins: { directory: './plugins', enabled: mode === 'empty' ? [] : ['core_tools'] },
  mcp_servers: mode === 'empty' ? [] : [{ name: 'filesystem', declared_capabilities: { workspace_read: true, workspace_write: false, network: 'denied' } }, { name: 'issues', declared_capabilities: { workspace_read: false, workspace_write: false, network: 'allowed' } }],
  health: 'not_checked', management: 'Tools, MCP servers and their invocation approvals are operator-managed (configuration and `kriya mcp`); this application shows them and changes nothing.',
  health_note: "A server's health is learned only by starting it (`kriya mcp inspect`); opening a folder never starts one.",
});

/** DEV/TEST ONLY: scripted `skills.*` / `tools.*` content in the shapes Kriya returns (ui/kup/schema/catalog.schema.json). */
const noProvenance = { state: 'NONE_RECORDED', tracked_rule_count: 0, untracked_rule_count: 4, verified_tracked_count: 0, sources: [] as unknown[], sources_truncated: false };
const fixtureSkillDescriptors = (mode: FixtureContextMode) => {
  const base = [
    { id: 'python-testing', name: 'python-testing', description: 'Pytest conventions for generated tests', category: 'Language', tags: ['python', 'pytest'], supported_versions: '*', skill_version: null,
      digest: 'sha256:' + '1a'.repeat(32), source: 'local_directory', origin: 'kriya_install', scope: 'install', verified: true, verified_at: '2026-10-01', verified_context: 'passed the runtime verification gate',
      verification_gap_acknowledged: false, rule_count: 12, staged_rule_count: 0, example_count: 2, has_instructions: true,
      provenance: { state: 'RECORDED', tracked_rule_count: 8, untracked_rule_count: 4, verified_tracked_count: 8, sources_truncated: false, sources: [
        { source: 'https://docs.pytest.org/en/stable/how-to/fixtures.html', rule_count: 5, verified_count: 5, first_added: '2026-09-20', last_added: '2026-09-28' },
        { source: 'https://docs.pytest.org/en/stable/how-to/parametrize.html', rule_count: 3, verified_count: 3, first_added: '2026-09-28', last_added: '2026-09-28' }] },
      unknown_fields: ['skill_version'] },
    { id: 'spring-boot', name: 'spring-boot', description: 'Spring Boot service layout', category: 'Framework', tags: ['java', 'spring'], supported_versions: '>=3.0.0', skill_version: null,
      digest: 'sha256:' + '2b'.repeat(32), source: 'local_directory', origin: 'workspace_or_configured', scope: 'workspace', verified: false, verified_at: null, verified_context: null,
      verification_gap_acknowledged: false, rule_count: 4, staged_rule_count: 3, example_count: 0, has_instructions: false, provenance: noProvenance, unknown_fields: ['skill_version'] },
  ];
  return mode === 'empty' ? [] : mode === 'many' ? Array.from({ length: 120 }, (_, i) => ({ ...base[i % 2]!, id: `skill-${String(i).padStart(3, '0')}`, name: `skill-${String(i).padStart(3, '0')}` })) : base;
};
const fixtureToolDescriptors = (mode: FixtureContextMode) => {
  if (mode === 'empty') return [];
  const mcp = (name: string, caps: { workspace_read: boolean; workspace_write: boolean; network: string; network_hosts: string[] }, approval: unknown, env: string[]) => ({
    id: `mcp:${name}`, name, kind: 'mcp_server', provider: 'configured_mcp', version: null, status: 'CONFIGURED', health: 'NOT_CHECKED', declared_capabilities: caps,
    reference: { executable: `${name}-server`, argument_count: 2, environment_variable_names: env }, scope: null, approval, unknown_fields: ['version', 'scope'] });
  return [
    mcp('filesystem', { workspace_read: true, workspace_write: false, network: 'denied', network_hosts: [] },
      { state: 'UNKNOWN', reason: 'RECORDS_NEED_LIVE_TOOL_IDENTITY', records_on_file: 2, recorded_tool_names: ['list_directory', 'read_file'] }, []),
    mcp('issues', { workspace_read: false, workspace_write: false, network: 'explicit_destinations', network_hosts: ['issues.example.org'] },
      { state: 'NOT_APPROVED', reason: 'NO_RECORDS_FOR_SERVER', records_on_file: 0, recorded_tool_names: [] }, ['ISSUES_API_TOKEN']),
    { id: 'plugin:core_tools', name: 'core_tools', kind: 'plugin', provider: 'enabled_plugin', version: null, status: 'ENABLED', health: 'NOT_CHECKED', declared_capabilities: null,
      reference: { plugin: 'core_tools' }, scope: null, approval: { state: 'NOT_APPLICABLE', reason: 'NOT_AN_MCP_SERVER', records_on_file: null, recorded_tool_names: [] }, unknown_fields: ['version', 'declared_capabilities', 'scope'] },
  ];
};

export interface FixtureHostOptions {
  /** The parent folders the scripted New Project chooser returns, in order (null = the person cancels). */
  projectParents?: readonly (string | null)[];
  /** Scripted refusals of the next project creations, in order (consumed one per attempt; none left = success). */
  projectRefusals?: readonly { code: string; message: string; reason?: string | null }[];
  /** What `execution.context` reports (default 'full'), optionally per folder. */
  contextMode?: FixtureContextMode;
  contextModeByWorkspace?: Readonly<Record<string, FixtureContextMode>>;
  /** What `execution.model_probe` reports (default 'present'), optionally per folder. */
  probeMode?: FixtureProbeMode;
  probeModeByWorkspace?: Readonly<Record<string, FixtureProbeMode>>;
  /** The folders the scripted chooser returns, in order (null = the person cancels). Empty = every choice is a cancel. */
  choices?: readonly (string | null)[];
  /** Folders remembered from an earlier session (most recent first). */
  recent?: readonly string[];
  /** The workspace already open at construction. Default: '/fixture/workspace' (the cockpit tests start inside a workspace); null = the welcome screen. */
  workspace?: string | null;
  /** capabilities.features.evidence_content. false (the production default) = content is not authorized. */
  evidenceContent?: boolean;
  /** The capture mode of the evidence store a launch writes while it waits on a review (default 'full'; 'digest_only' keeps digests, not content). */
  capture?: 'full' | 'digest_only';
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
  private readonly liveCapture: 'full' | 'digest_only';
  private settings: Partial<HostSettings> = { editor: 'vscode', workspacePath: '/fixture/workspace', configDirectory: null, kriyaExecutable: null };
  private readonly choices: (string | null)[];
  private readonly projectParents: (string | null)[];
  private projectRefusals: { code: string; message: string; reason?: string | null }[];
  private readonly contextMode: FixtureContextMode;
  private readonly contextModeByWorkspace: Readonly<Record<string, FixtureContextMode>>;
  private readonly probeModeByWorkspace: Readonly<Record<string, FixtureProbeMode>>;
  probeMode: FixtureProbeMode;
  /** Folders created through New Project in this session (empty plain folders, no runs). */
  readonly createdFolders = new Set<string>();
  private nextProject = 0;
  /** token -> parent for the parent choices the fixture issued (a request with any other token is refused, as the real host does). */
  private readonly projectTokens = new Map<string, string>();
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
    this.liveCapture = options.capture ?? 'full';
    this.choices = [...(options.choices ?? [])];
    this.projectParents = [...(options.projectParents ?? [])];
    this.projectRefusals = [...(options.projectRefusals ?? [])];
    this.contextMode = options.contextMode ?? 'full';
    this.probeMode = options.probeMode ?? 'present';
    this.contextModeByWorkspace = options.contextModeByWorkspace ?? {};
    this.probeModeByWorkspace = options.probeModeByWorkspace ?? {};
    this.recentList = [...(options.recent ?? [])];
    if (options.workspace !== undefined) this.settings.workspacePath = options.workspace;
    this.workspace = {
      choose: async (): Promise<WorkspaceChoice> => {
        this.workspaceCalls.push('choose');
        const next = this.choices.shift();
        if (this.exec?.active()) return { chosen: false, path: null, refused: 'RUN_ACTIVE' };
        return next === undefined || next === null ? { chosen: false, path: null } : { chosen: true, path: next };
      },
      chooseProjectParent: async (): Promise<ProjectParentChoice> => {
        this.workspaceCalls.push('chooseProjectParent');
        if (this.exec?.active()) return { chosen: false, token: null, parent: null, refused: 'RUN_ACTIVE' };
        const next = this.projectParents.shift();
        if (next === undefined || next === null) return { chosen: false, token: null, parent: null };
        const token = `pt-${String(++this.nextProject).padStart(32, '0')}`;
        this.projectTokens.set(token, next);
        return { chosen: true, token, parent: next };
      },
      createProject: async (request): Promise<ProjectCreateResult> => {
        this.workspaceCalls.push(`createProject:${request.name}`);
        if (!this.projectTokens.has(request.token)) return { created: false, path: null, code: 'PARENT_EXPIRED', message: 'the parent folder choice has expired; choose the folder again', reason: null };
        const refusal = this.projectRefusals.shift();
        if (refusal) return { created: false, path: null, code: refusal.code, message: refusal.message, reason: refusal.reason ?? null };
        const path = `/fixture/new/${request.name}`;
        this.createdFolders.add(path);
        this.projectTokens.delete(request.token);
        return { created: true, path, code: null, message: null, reason: null };
      },
      revealWorkspace: async () => { this.workspaceCalls.push('reveal'); return this.settings.workspacePath ? { revealed: true, reason: null } : { revealed: false, reason: 'no workspace is open' }; },
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
    return { ...template, id: LAUNCHED_TRACE_ID, evidenceId: LAUNCH_ID, goal: this.exec.goal(), seed: template.seed.store === 'present' ? { ...template.seed, sealed: false, capture: this.liveCapture } : template.seed };
  }

  /** Test hook: the application menu asks the renderer to open the chooser. */
  menu(command: MenuCommand): void { for (const l of [...this.menuListeners]) l(command); }

  /** A known fixture folder, or a project created in this session (an empty plain folder). */
  private folder(workspace: string) {
    return FIXTURE_FOLDERS[workspace] ?? (this.createdFolders.has(workspace) ? FIXTURE_FOLDERS['/fixture/empty'] : undefined);
  }

  private describeFolder(workspace: string): KupEnvelope {
    const known = this.folder(workspace);
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
      operations: ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status', 'snapshot.acquire', 'snapshot.list', 'snapshot.prune', 'snapshot.verify', 'workspace.describe', 'execution.preflight', 'launch.status', 'evidence.manifest', 'evidence.verify', 'evidence.records', 'evidence.change_content', 'evidence.call_content', 'skills.list', 'skills.describe', 'tools.list', 'tools.describe', 'tools.approval_state', 'configuration.approval_status', 'configuration.approve', 'change_approval.review'],
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
    const display = s.displayGoal === undefined ? s.goal : s.displayGoal;
    return { run_id: s.id, timestamp: s.timestamp, goal: s.goal, display_goal: display, display_goal_source: display === null ? 'unavailable' : s.displayGoal === undefined ? 'own' : 'enforce_row', duration_sec: s.durationSec, attempts: s.attempts, status: s.status, failure_category: s.failureCategory, files_modified: null,
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
        const known = this.folder(request.workspace);
        if (!known || !this.exec) return this.err('execution.preflight', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const blocked = known.recovery && known.recovery !== 'CLEAN' ? { code: 'RECOVERY_REQUIRED', message: `the folder's last run left it in state ${known.recovery}; recover it (kriya runs recover) before starting another` } : null;
        return this.env('execution.preflight', this.exec.preflight(request.workspace, SOURCE.state_directory, blocked), this.live, SOURCE);
      }
      case 'workspace.create': return this.err('workspace.create', 'INVALID_REQUEST', 'the fixture host creates projects through its workspace port only');
      case 'configuration.approval_status': {
        if (!this.folder(request.workspace) || !this.exec) return this.err('configuration.approval_status', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        return this.env('configuration.approval_status', this.exec.approvalStatus(request.workspace), this.live, null);
      }
      case 'change_approval.review': {
        if (!this.folder(request.workspace) || !this.exec) return this.err('change_approval.review', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const answer = this.exec.changeReview(request);
        return 'data' in answer ? this.env('change_approval.review', answer.data, this.live, null) : this.err('change_approval.review', answer.code, answer.message, answer.extra);
      }
      case 'project.initialize': return this.err('project.initialize', 'INVALID_REQUEST', 'project.initialize is not available through the query channel');
      case 'configuration.approve': return this.err('configuration.approve', 'INVALID_REQUEST', 'configuration.approve is not available through the query channel');
      case 'execution.context': {
        if (!this.folder(request.workspace)) return this.err('execution.context', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const name = request.workspace.slice(request.workspace.lastIndexOf('/') + 1);
        const mode = this.contextModeByWorkspace[request.workspace] ?? this.contextMode;
        const unavailable = mode === 'unavailable';
        return this.env('execution.context', {
          workspace: { path: request.workspace, name, identity: 'ab'.repeat(32) }, available: !unavailable,
          unavailable_reason: unavailable ? { code: 'CONFIG_LOAD_FAILED', message: 'ValueError: Failed to load configuration (fixture)' } : null, authority_status: unavailable ? 'failed' : 'no_findings',
          routing: unavailable ? [] : fixtureRouting(), skills: unavailable ? null : fixtureSkills(mode), tools: unavailable ? null : fixtureTools(mode),
          egress: unavailable ? null : { policy: 'local_only', web_lookup_enabled: false },
        }, this.live, null);
      }
      case 'execution.model_probe': {
        if (!this.folder(request.workspace)) return this.err('execution.model_probe', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const mode = this.probeModeByWorkspace[request.workspace] ?? this.probeMode;
        if (mode === 'unavailable') return this.env('execution.model_probe', { available: false, unavailable_reason: { code: 'CONFIG_LOAD_FAILED', message: 'ValueError: Failed to load configuration (fixture)' }, authority_status: 'failed', endpoints: [], roles: [] }, this.live, null);
        const state = mode === 'present' || mode === 'missing' ? 'reachable' : mode;
        const roleState = mode === 'present' ? 'model_present' : mode === 'missing' ? 'model_missing' : mode;
        return this.env('execution.model_probe', {
          available: true, unavailable_reason: null, authority_status: 'no_findings', egress_policy: 'local_only', timeout_seconds: 3,
          endpoints: [{ endpoint: 'http://localhost:11434/v1', state, reason: state === 'unreachable' ? 'Connection refused' : state === 'not_probed' ? 'the endpoint is not local and the egress policy is local_only' : null, model_count: state === 'reachable' ? 4 : 0 }],
          roles: [{ for: 'default', model: 'fixture-model:7b', endpoint: 'http://localhost:11434/v1', available: roleState === 'model_present' ? true : roleState === 'model_missing' ? false : null, state: roleState },
                  { for: 'planner', model: 'fixture-planner:14b', endpoint: 'http://localhost:11434/v1', available: roleState === 'model_present' ? true : roleState === 'model_missing' ? false : null, state: roleState }],
          note: 'A single bounded GET /models per distinct local endpoint, made now. It proves the server answered and lists the models it serves; it does not prove a model can complete a request.',
        }, this.live, null);
      }
      case 'skills.list':
      case 'skills.describe':
      case 'tools.list':
      case 'tools.describe':
      case 'tools.approval_state': {
        if (!this.folder(request.workspace)) return this.err(request.operation, 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const mode = this.contextModeByWorkspace[request.workspace] ?? this.contextMode;
        const unavailable = mode === 'unavailable';
        const head = {
          workspace: { path: request.workspace, name: request.workspace.slice(request.workspace.lastIndexOf('/') + 1), identity: 'ab'.repeat(32) }, available: !unavailable,
          unavailable_reason: unavailable ? { code: 'CONFIG_LOAD_FAILED', message: 'ValueError: Failed to load configuration (fixture)' } : null, authority_status: unavailable ? 'failed' : 'no_findings',
        };
        const skills = unavailable ? [] : fixtureSkillDescriptors(mode);
        const tools = unavailable ? [] : fixtureToolDescriptors(mode);
        if (request.operation === 'skills.list') return this.env('skills.list', { ...head, items: skills, total: skills.length, truncated: false }, this.live, null);
        if (request.operation === 'skills.describe') { const hit = skills.find((x) => x.id === request.skill_id.toLowerCase()) ?? null; return this.env('skills.describe', { ...head, found: hit !== null, skill: hit }, this.live, null); }
        if (request.operation === 'tools.list') return this.env('tools.list', { ...head, items: tools, total: tools.length }, this.live, null);
        const tool = tools.find((x) => x.id === request.tool_id) ?? null;
        if (request.operation === 'tools.describe') return this.env('tools.describe', { ...head, found: tool !== null, tool }, this.live, null);
        return this.env('tools.approval_state', { ...head, found: tool !== null, tool_id: unavailable ? null : request.tool_id, approval: tool ? tool.approval : unavailable ? null : { state: 'UNKNOWN', reason: 'TOOL_NOT_FOUND', records_on_file: null, recorded_tool_names: [] } }, this.live, null);
      }
      case 'launch.status': {
        const status = this.exec?.launchStatus() ?? null;
        const fault = this.exec?.statusBroken() ?? null;
        if (fault) return this.err('launch.status', fault, 'the launch record cannot be read', { reason: 'fixture_fault' });
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
      case 'runtime_command.review': {
        if (!this.folder(request.workspace) || !this.exec) return this.err('runtime_command.review', 'WORKSPACE_UNAVAILABLE', `no such folder: ${request.workspace}`, { reason: 'not_found' });
        const answer = this.exec.runtimeReview(request);
        return 'data' in answer ? this.env('runtime_command.review', answer.data, this.live, null) : this.err('runtime_command.review', answer.code, answer.message, answer.extra);
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
