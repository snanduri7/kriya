/**
 * The typed allowlist of messages between the renderer and the native layer (gate A-1, A-2 P-R2).
 * Pure module (no Electron import) so the hardening test and the preload share one source of truth.
 * The JSON Schema for this contract lands in ui/kup in Phase B; this is its TypeScript face.
 */
export const IPC_CHANNELS = {
  query: 'kriya:kup:query',
  openInIde: 'kriya:host:openInIde',
  clipboardWrite: 'kriya:host:clipboard:write',
  settingGet: 'kriya:host:setting:get',
  settingSet: 'kriya:host:setting:set',
  hostInfo: 'kriya:host:info',
  // M5 workspace lifecycle. The renderer never names a folder to open: the NATIVE chooser (main) returns a path, and only a path the
  // person chose (or one remembered from an earlier choice) can be handed to Kriya as a workspace argument.
  chooseWorkspace: 'kriya:host:workspace:choose',
  recentWorkspaces: 'kriya:host:workspace:recent',
  rememberWorkspace: 'kriya:host:workspace:remember',
  forgetWorkspace: 'kriya:host:workspace:forget',
  // M6 execution. Start Run is the ONLY execution action: a typed request (workspace + goal), no command text, no options. The host launches the
  // one allowlisted Kriya command line itself (execution_host.ts); executionState reports the process, never the run's outcome.
  startRun: 'kriya:host:execution:start',
  executionState: 'kriya:host:execution:state',
  // GUI final: the review decision. ONE typed action (workspace + the identities Kriya gave the pending review + approve|reject); the host forwards it to
  // Kriya's review-decision action and reports Kriya's answer. The renderer cannot name a command, an option or a different authority.
  decideApproval: 'kriya:host:execution:decide',
} as const;
/** Main -> renderer notifications (one-way; the preload exposes a subscription, never a send). */
export const MENU_CHANNEL = 'kriya:host:menu';
export const MENU_COMMANDS = ['openFolder'] as const;
export type MenuCommand = (typeof MENU_COMMANDS)[number];
export const RECENT_WORKSPACES_MAX = 10;
export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
export const ALLOWED_CHANNELS: readonly IpcChannel[] = Object.values(IPC_CHANNELS);

export const EDITOR_IDS = ['vscode', 'intellij', 'eclipse'] as const;
export type EditorId = (typeof EDITOR_IDS)[number];
export const SETTING_KEYS = ['editor', 'kriyaExecutable', 'workspacePath', 'configDirectory'] as const;
export type SettingKey = (typeof SETTING_KEYS)[number];

export const LIMITS = {
  listMax: 200,
  evidencePageMax: 1000,
  evidenceFilterMax: 256,
  listDefault: 50,
  runIdMax: 256,
  cursorMax: 1024,
  pathMax: 4096,
  clipboardMax: 8 * 1024 * 1024,
  goalMax: 16000, // kriya/control/launch.py::GOAL_MAX_CHARS
} as const;
export const LAUNCH_ID_RE = /^\d{8}T\d{6}-[0-9a-f]{8}$/;
export const REVIEW_ID_RE = /^ra-[0-9a-f]{24}$/;
export const CANDIDATE_DIGEST_RE = /^sha256:[0-9a-f]{64}$/;

const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._:-]*$/;
// A cursor must start alphanumeric so it can never be read as an option by the kriya CLI (`--cursor --help`).
const CURSOR_RE = /^[A-Za-z0-9][A-Za-z0-9+/=_-]*$/;

export type ValidationResult<T> = { ok: true; value: T } | { ok: false; message: string };

const SNAPSHOT_ID_RE = /^\d{8}T\d{12}Z-[0-9a-f]{8}$/;

export type KupRequest =
  | { operation: 'capabilities' }
  | { operation: 'snapshot.acquire'; workspace?: string }
  | { operation: 'snapshot.list'; verify?: boolean }
  | { operation: 'snapshot.prune'; keep?: number }
  | { operation: 'snapshot.verify'; snapshot_id: string }
  | { operation: 'history.list'; snapshot_id: string; limit?: number; cursor?: string; workspace?: string }
  | { operation: 'history.detail'; snapshot_id: string; run_id: string }
  | { operation: 'history.prompt'; snapshot_id: string; run_id: string }
  | { operation: 'workspace.status'; workspace: string }
  | { operation: 'workspace.describe'; workspace: string }
  | { operation: 'execution.preflight'; workspace: string }
  | { operation: 'launch.status'; workspace: string; launch_id: string }
  | { operation: 'evidence.manifest'; run_id: string; snapshot_id?: string; from_trace?: boolean }
  | { operation: 'evidence.verify'; run_id: string }
  | { operation: 'evidence.change_content'; run_id: string; seq: number; part: string }
  | { operation: 'evidence.call_content'; run_id: string; seq: number; part: string }
  | { operation: 'evidence.records'; run_id: string; unit?: string; phase?: string; role?: string; attempt?: number; from_seq?: number; kind?: string[]; limit?: number; cursor?: string };

export function validateSnapshotId(v: unknown): ValidationResult<string> {
  if (typeof v !== 'string' || !SNAPSHOT_ID_RE.test(v)) return { ok: false, message: 'snapshot_id must be a published snapshot id (YYYYMMDDTHHMMSSffffffZ-xxxxxxxx)' };
  return { ok: true, value: v };
}

function isObj(v: unknown): v is Record<string, unknown> { return typeof v === 'object' && v !== null && !Array.isArray(v); }

export function validateRunId(v: unknown): ValidationResult<string> {
  if (typeof v !== 'string' || !v.length || v.length > LIMITS.runIdMax) return { ok: false, message: 'run_id must be a non-empty string of at most 256 chars' };
  if (!RUN_ID_RE.test(v)) return { ok: false, message: 'run_id contains characters outside [A-Za-z0-9._:-]' };
  return { ok: true, value: v };
}

export function validateCursor(v: unknown): ValidationResult<string> {
  if (typeof v !== 'string' || !v.length || v.length > LIMITS.cursorMax) return { ok: false, message: 'cursor must be a non-empty string of at most 1024 chars' };
  if (!CURSOR_RE.test(v)) return { ok: false, message: 'cursor contains characters outside the opaque-token alphabet' };
  return { ok: true, value: v };
}

export function validateWorkspacePath(v: unknown): ValidationResult<string> {
  if (typeof v !== 'string' || !v.length || v.length > LIMITS.pathMax) return { ok: false, message: 'workspace must be a non-empty path' };
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if (!v.startsWith('/') || /[\u0000-\u001f]/.test(v)) return { ok: false, message: 'workspace must be an absolute path without control characters' };
  return { ok: true, value: v };
}

/** Validate an untrusted renderer message into a typed KUP request. Unknown operations and extra keys are refused. */
export function validateKupRequest(v: unknown): ValidationResult<KupRequest> {
  if (!isObj(v) || typeof v.operation !== 'string') return { ok: false, message: 'request must be an object with an operation' };
  const keys = Object.keys(v).sort();
  switch (v.operation) {
    case 'capabilities':
      return keys.join() === 'operation' ? { ok: true, value: { operation: 'capabilities' } } : { ok: false, message: 'capabilities takes no parameters' };
    case 'snapshot.acquire': {
      if (!keys.every((k) => ['operation', 'workspace'].includes(k))) return { ok: false, message: 'snapshot.acquire accepts only workspace' };
      if ('workspace' in v && v.workspace !== undefined) { const w = validateWorkspacePath(v.workspace); if (!w.ok) return w; return { ok: true, value: { operation: 'snapshot.acquire', workspace: w.value } }; }
      return { ok: true, value: { operation: 'snapshot.acquire' } };
    }
    case 'snapshot.list': {
      if (!keys.every((k) => ['operation', 'verify'].includes(k))) return { ok: false, message: 'snapshot.list accepts only verify' };
      if ('verify' in v && typeof v.verify !== 'boolean') return { ok: false, message: 'verify must be a boolean' };
      return { ok: true, value: 'verify' in v ? { operation: 'snapshot.list', verify: v.verify as boolean } : { operation: 'snapshot.list' } };
    }
    case 'snapshot.prune': {
      if (!keys.every((k) => ['operation', 'keep'].includes(k))) return { ok: false, message: 'snapshot.prune accepts only keep' };
      if ('keep' in v && (typeof v.keep !== 'number' || !Number.isInteger(v.keep) || v.keep < 0 || v.keep > 1000)) return { ok: false, message: 'keep must be an integer in 0..1000' };
      return { ok: true, value: 'keep' in v ? { operation: 'snapshot.prune', keep: v.keep as number } : { operation: 'snapshot.prune' } };
    }
    case 'snapshot.verify': {
      if (keys.join() !== 'operation,snapshot_id') return { ok: false, message: 'snapshot.verify takes exactly snapshot_id' };
      const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid;
      return { ok: true, value: { operation: 'snapshot.verify', snapshot_id: sid.value } };
    }
    case 'history.list': {
      if (!keys.every((k) => ['operation', 'snapshot_id', 'limit', 'cursor', 'workspace'].includes(k))) return { ok: false, message: 'history.list accepts only snapshot_id, limit, cursor and workspace' };
      const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid;
      const out: KupRequest = { operation: 'history.list', snapshot_id: sid.value };
      if ('limit' in v) {
        if (typeof v.limit !== 'number' || !Number.isInteger(v.limit) || v.limit < 1 || v.limit > LIMITS.listMax) return { ok: false, message: `limit must be an integer in 1..${LIMITS.listMax}` };
        out.limit = v.limit;
      }
      if ('cursor' in v) { const c = validateCursor(v.cursor); if (!c.ok) return c; out.cursor = c.value; }
      if ('workspace' in v) { const w = validateWorkspacePath(v.workspace); if (!w.ok) return w; out.workspace = w.value; }
      return { ok: true, value: out };
    }
    case 'history.detail':
    case 'history.prompt': {
      if (keys.join() !== 'operation,run_id,snapshot_id') return { ok: false, message: `${v.operation} takes exactly snapshot_id and run_id` };
      const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid;
      const r = validateRunId(v.run_id); if (!r.ok) return r;
      return { ok: true, value: { operation: v.operation, snapshot_id: sid.value, run_id: r.value } };
    }
    case 'evidence.manifest': {
      if (!keys.every((k) => ['operation', 'run_id', 'snapshot_id', 'from_trace'].includes(k))) return { ok: false, message: 'evidence.manifest accepts only run_id, snapshot_id and from_trace' };
      const r = validateRunId(v.run_id); if (!r.ok) return r;
      const out: KupRequest = { operation: 'evidence.manifest', run_id: r.value };
      if ('snapshot_id' in v) { const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid; out.snapshot_id = sid.value; }
      if ('from_trace' in v) { if (typeof v.from_trace !== 'boolean') return { ok: false, message: 'from_trace must be a boolean' }; out.from_trace = v.from_trace; }
      return { ok: true, value: out };
    }
    case 'evidence.verify': {
      if (keys.join() !== 'operation,run_id') return { ok: false, message: 'evidence.verify takes exactly run_id' };
      const r = validateRunId(v.run_id); if (!r.ok) return r;
      return { ok: true, value: { operation: 'evidence.verify', run_id: r.value } };
    }
    case 'evidence.change_content':
    case 'evidence.call_content': {
      if (keys.join() !== 'operation,part,run_id,seq') return { ok: false, message: `${v.operation} takes exactly run_id, seq and part` };
      const r = validateRunId(v.run_id); if (!r.ok) return r;
      if (!boundedInt(v.seq, 1, 1_000_000_000)) return { ok: false, message: 'seq must be an integer >= 1' };
      if (typeof v.part !== 'string' || !/^[a-z_]{1,32}$/.test(v.part)) return { ok: false, message: 'part must be a lowercase key name' };
      return { ok: true, value: { operation: v.operation, run_id: r.value, seq: v.seq, part: v.part } };
    }
    case 'evidence.records': return validateEvidenceRecords(v, keys);
    case 'workspace.status':
    case 'workspace.describe':
    case 'execution.preflight': {
      if (keys.join() !== 'operation,workspace') return { ok: false, message: `${v.operation} takes exactly workspace` };
      const w = validateWorkspacePath(v.workspace); if (!w.ok) return w;
      return { ok: true, value: { operation: v.operation, workspace: w.value } };
    }
    case 'launch.status': {
      if (keys.join() !== 'launch_id,operation,workspace') return { ok: false, message: 'launch.status takes exactly workspace and launch_id' };
      const w = validateWorkspacePath(v.workspace); if (!w.ok) return w;
      if (typeof v.launch_id !== 'string' || !LAUNCH_ID_RE.test(v.launch_id)) return { ok: false, message: 'launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx' };
      return { ok: true, value: { operation: 'launch.status', workspace: w.value, launch_id: v.launch_id } };
    }
    default:
      return { ok: false, message: `unknown operation ${String(v.operation)}` };
  }
}

const EVIDENCE_RECORDS_KEYS = ['operation', 'run_id', 'unit', 'phase', 'role', 'attempt', 'from_seq', 'kind', 'limit', 'cursor'];
const KIND_RE = /^[a-z][a-z_]*(\.[a-z_]+)*$/;

function boundedInt(v: unknown, min: number, max: number): v is number { return typeof v === 'number' && Number.isInteger(v) && v >= min && v <= max; }

function validateEvidenceRecords(v: Record<string, unknown>, keys: string[]): ValidationResult<KupRequest> {
  if (!keys.every((k) => EVIDENCE_RECORDS_KEYS.includes(k))) return { ok: false, message: `evidence.records accepts only ${EVIDENCE_RECORDS_KEYS.slice(1).join(', ')}` };
  const r = validateRunId(v.run_id); if (!r.ok) return r;
  const out: Extract<KupRequest, { operation: 'evidence.records' }> = { operation: 'evidence.records', run_id: r.value };
  for (const name of ['unit', 'phase', 'role'] as const) {
    if (!(name in v)) continue;
    const t = v[name];
    // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
    if (typeof t !== 'string' || !t.length || t.length > LIMITS.evidenceFilterMax || /[\u0000-\u001f]/.test(t)) return { ok: false, message: `${name} must be a non-empty string without control characters, at most ${LIMITS.evidenceFilterMax} chars` };
    out[name] = t;
  }
  if ('attempt' in v) { if (!boundedInt(v.attempt, 0, 1_000_000)) return { ok: false, message: 'attempt must be an integer in 0..1000000' }; out.attempt = v.attempt; }
  if ('from_seq' in v) { if (!boundedInt(v.from_seq, 1, 1_000_000_000)) return { ok: false, message: 'from_seq must be an integer >= 1' }; out.from_seq = v.from_seq; }
  if ('limit' in v) { if (!boundedInt(v.limit, 1, LIMITS.evidencePageMax)) return { ok: false, message: `limit must be an integer in 1..${LIMITS.evidencePageMax}` }; out.limit = v.limit; }
  if ('kind' in v) {
    const k = v.kind;
    if (!Array.isArray(k) || !k.length || k.length > 40 || !k.every((x) => typeof x === 'string' && x.length <= 64 && KIND_RE.test(x))) return { ok: false, message: 'kind must be a non-empty list of record kind names' };
    out.kind = k as string[];
  }
  if ('cursor' in v) { const c = validateCursor(v.cursor); if (!c.ok) return c; out.cursor = c.value; }
  return { ok: true, value: out };
}

export interface StartRunRequest { workspace: string; goal: string }

/** Start Run: exactly {workspace, goal}. Any other key is refused - there are no options to carry (a launch is non-interactive and cannot widen anything). */
export function validateStartRun(v: unknown): ValidationResult<StartRunRequest> {
  if (!isObj(v) || Object.keys(v).sort().join() !== 'goal,workspace') return { ok: false, message: 'startRun takes exactly workspace and goal' };
  const w = validateWorkspacePath(v.workspace); if (!w.ok) return w;
  if (typeof v.goal !== 'string' || !v.goal.trim().length) return { ok: false, message: 'an engineering goal is required' };
  if (v.goal.length > LIMITS.goalMax) return { ok: false, message: `the goal is ${v.goal.length} characters; the limit is ${LIMITS.goalMax}` };
  if (v.goal.includes('\0')) return { ok: false, message: 'the goal contains a NUL character' };
  return { ok: true, value: { workspace: w.value, goal: v.goal } };
}

export interface DecideApprovalRequest { workspace: string; launch_id: string; review_id: string; candidate_digest: string; decision: 'approve' | 'reject' }

/** Review decision: exactly these five fields, each in its closed grammar. There is no field for 'always', 'whatever is current', an option or a mode. */
export function validateDecideApproval(v: unknown): ValidationResult<DecideApprovalRequest> {
  if (!isObj(v) || Object.keys(v).sort().join() !== 'candidate_digest,decision,launch_id,review_id,workspace') return { ok: false, message: 'decideApproval takes exactly workspace, launch_id, review_id, candidate_digest and decision' };
  const w = validateWorkspacePath(v.workspace); if (!w.ok) return w;
  if (typeof v.launch_id !== 'string' || !LAUNCH_ID_RE.test(v.launch_id)) return { ok: false, message: 'launch_id must have the shape YYYYMMDDTHHMMSS-xxxxxxxx' };
  if (typeof v.review_id !== 'string' || !REVIEW_ID_RE.test(v.review_id)) return { ok: false, message: 'review_id is malformed' };
  if (typeof v.candidate_digest !== 'string' || !CANDIDATE_DIGEST_RE.test(v.candidate_digest)) return { ok: false, message: 'candidate_digest is malformed' };
  if (v.decision !== 'approve' && v.decision !== 'reject') return { ok: false, message: 'decision must be approve or reject' };
  return { ok: true, value: { workspace: w.value, launch_id: v.launch_id, review_id: v.review_id, candidate_digest: v.candidate_digest, decision: v.decision } };
}

export function validateOpenInIde(v: unknown): ValidationResult<{ path: string; line?: number }> {
  if (!isObj(v) || typeof v.path !== 'string' || !v.path.length || v.path.length > LIMITS.pathMax) return { ok: false, message: 'path must be a non-empty string' };
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if (/[\u0000-\u001f]/.test(v.path)) return { ok: false, message: 'path contains control characters' };
  if (!Object.keys(v).every((k) => k === 'path' || k === 'line')) return { ok: false, message: 'openInIde accepts only path and line' };
  if ('line' in v && v.line !== undefined) {
    if (typeof v.line !== 'number' || !Number.isInteger(v.line) || v.line < 1 || v.line > 10_000_000) return { ok: false, message: 'line must be a positive integer' };
    return { ok: true, value: { path: v.path, line: v.line } };
  }
  return { ok: true, value: { path: v.path } };
}

/**
 * Which settings the RENDERER may write (Phase 0, owner decision 2026-10-11). `kriyaExecutable` and `configDirectory` are host/operator-owned: they decide WHICH program the
 * app runs and WHICH configuration it discovers, so a (possibly compromised) renderer must never change them. In the cockpit (the shipped product) the renderer may write
 * only harmless UI preferences (`editor`); `workspacePath` is set by the host's own Open Folder path, not typed. The explicitly separate legacy M1 inspector build keeps its
 * narrow compatibility (all four keys) and never ships. Operators set the two keys in the host settings file or, for development, the test harness. (Closing a workspace clears `workspacePath`, which is allowed.)
 */
export const HOST_OWNED_SETTINGS = ['kriyaExecutable', 'configDirectory'] as const;
export function rendererMayWrite(key: SettingKey, uiMode: 'cockpit' | 'inspector', value: string | null): boolean {
  if (uiMode === 'inspector') return true;
  // clearing the remembered workspace (closing it) is harmless; SETTING it is the host's job (Open Folder), never the renderer's
  return key === 'editor' || (key === 'workspacePath' && value === null);
}

export function validateSettingKey(v: unknown): ValidationResult<SettingKey> {
  return typeof v === 'string' && (SETTING_KEYS as readonly string[]).includes(v) ? { ok: true, value: v as SettingKey } : { ok: false, message: 'unknown setting' };
}

export function validateSettingValue(key: SettingKey, v: unknown): ValidationResult<string | null> {
  if (v === null) return key === 'editor' ? { ok: false, message: 'editor cannot be null' } : { ok: true, value: null };
  if (typeof v !== 'string' || v.length > LIMITS.pathMax || v.includes('\0')) return { ok: false, message: 'setting value must be a string' };
  if (key === 'editor' && !(EDITOR_IDS as readonly string[]).includes(v)) return { ok: false, message: `editor must be one of ${EDITOR_IDS.join(', ')}` };
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if ((key === 'configDirectory' || key === 'workspacePath' || key === 'kriyaExecutable') && (!v.startsWith('/') || /[\u0000-\u001f]/.test(v))) return { ok: false, message: `${key} must be an absolute path without control characters` };
  return { ok: true, value: v };
}
