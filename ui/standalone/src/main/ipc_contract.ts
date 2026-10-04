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
} as const;
export type IpcChannel = (typeof IPC_CHANNELS)[keyof typeof IPC_CHANNELS];
export const ALLOWED_CHANNELS: readonly IpcChannel[] = Object.values(IPC_CHANNELS);

export const EDITOR_IDS = ['vscode', 'intellij', 'eclipse'] as const;
export type EditorId = (typeof EDITOR_IDS)[number];
export const SETTING_KEYS = ['editor', 'kriyaExecutable', 'workspacePath'] as const;
export type SettingKey = (typeof SETTING_KEYS)[number];

export const LIMITS = {
  listMax: 200,
  listDefault: 50,
  runIdMax: 256,
  cursorMax: 1024,
  pathMax: 4096,
  clipboardMax: 8 * 1024 * 1024,
} as const;

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
  | { operation: 'history.list'; snapshot_id: string; limit?: number; cursor?: string }
  | { operation: 'history.detail'; snapshot_id: string; run_id: string }
  | { operation: 'history.prompt'; snapshot_id: string; run_id: string }
  | { operation: 'workspace.status'; workspace: string };

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
    case 'history.list': {
      if (!keys.every((k) => ['operation', 'snapshot_id', 'limit', 'cursor'].includes(k))) return { ok: false, message: 'history.list accepts only snapshot_id, limit and cursor' };
      const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid;
      const out: KupRequest = { operation: 'history.list', snapshot_id: sid.value };
      if ('limit' in v) {
        if (typeof v.limit !== 'number' || !Number.isInteger(v.limit) || v.limit < 1 || v.limit > LIMITS.listMax) return { ok: false, message: `limit must be an integer in 1..${LIMITS.listMax}` };
        out.limit = v.limit;
      }
      if ('cursor' in v) { const c = validateCursor(v.cursor); if (!c.ok) return c; out.cursor = c.value; }
      return { ok: true, value: out };
    }
    case 'history.detail':
    case 'history.prompt': {
      if (keys.join() !== 'operation,run_id,snapshot_id') return { ok: false, message: `${v.operation} takes exactly snapshot_id and run_id` };
      const sid = validateSnapshotId(v.snapshot_id); if (!sid.ok) return sid;
      const r = validateRunId(v.run_id); if (!r.ok) return r;
      return { ok: true, value: { operation: v.operation, snapshot_id: sid.value, run_id: r.value } };
    }
    case 'workspace.status': {
      if (keys.join() !== 'operation,workspace') return { ok: false, message: 'workspace.status takes exactly workspace' };
      const w = validateWorkspacePath(v.workspace); if (!w.ok) return w;
      return { ok: true, value: { operation: 'workspace.status', workspace: w.value } };
    }
    default:
      return { ok: false, message: `unknown operation ${String(v.operation)}` };
  }
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

export function validateSettingKey(v: unknown): ValidationResult<SettingKey> {
  return typeof v === 'string' && (SETTING_KEYS as readonly string[]).includes(v) ? { ok: true, value: v as SettingKey } : { ok: false, message: 'unknown setting' };
}

export function validateSettingValue(key: SettingKey, v: unknown): ValidationResult<string | null> {
  if (v === null) return key === 'editor' ? { ok: false, message: 'editor cannot be null' } : { ok: true, value: null };
  if (typeof v !== 'string' || v.length > LIMITS.pathMax || v.includes('\0')) return { ok: false, message: 'setting value must be a string' };
  if (key === 'editor' && !(EDITOR_IDS as readonly string[]).includes(v)) return { ok: false, message: `editor must be one of ${EDITOR_IDS.join(', ')}` };
  return { ok: true, value: v };
}
