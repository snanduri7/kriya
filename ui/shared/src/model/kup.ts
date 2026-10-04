/**
 * Provisional KUP v1 shapes (P-24, P-25). Phase B replaces this file with
 * types generated from ui/kup's JSON Schema; the panels only ever depend on
 * these names, so that swap is mechanical. Unknown fields are kept everywhere
 * (index signatures): the contract never silently drops recorded detail.
 */
export const KUP_SCHEMA_VERSION = 1;

export type Availability = 'recorded' | 'not_recorded' | 'unreadable' | 'excluded' | 'unsupported';
export const AVAILABILITY_STATES: readonly Availability[] = ['recorded', 'not_recorded', 'unreadable', 'excluded', 'unsupported'];

export type KupErrorCode =
  | 'UNSUPPORTED_SCHEMA_VERSION'
  | 'INVALID_RESPONSE'
  | 'STORE_BUSY'
  | 'READ_ONLY_UNAVAILABLE'
  | 'RESPONSE_TOO_LARGE'
  | 'CONFIG_AUTHORITY_REFUSED'
  | 'HOST_ERROR';

export interface KupError {
  code: KupErrorCode | string;
  message: string;
  [k: string]: unknown;
}

export type KupOperation = 'capabilities' | 'history.list' | 'history.detail' | 'history.prompt' | 'workspace.status';

export interface KupSource {
  state_directory: string;
  trace_database: string;
  [k: string]: unknown;
}

export interface KupConsistency {
  kind: string;
  live_stream: boolean;
  [k: string]: unknown;
}

export interface KupEnvelope<T = unknown> {
  schema_version: number;
  operation: KupOperation | string;
  request_id: string;
  observed_at: string;
  source: KupSource | null;
  consistency: KupConsistency | null;
  data: T | null;
  error: KupError | null;
  [k: string]: unknown;
}

/** An optional panel section: availability first, data only when recorded (P-24). */
export interface Section<T = unknown> {
  availability: Availability | string;
  provenance?: string | null;
  reason?: string | null;
  data?: T | null;
  [k: string]: unknown;
}

export interface Capabilities {
  kup_versions: number[];
  operations: string[];
  identity: { kriya_version?: string | null; commit?: string | null; [k: string]: unknown };
  limits: { list_default?: number; list_max?: number; max_response_bytes?: number; [k: string]: unknown };
  features: Record<string, unknown>;
  [k: string]: unknown;
}

export interface RunSummary {
  run_id: string;
  timestamp: string | null;
  goal: string | null;
  duration_sec: number | null;
  attempts: number | null;
  status: string | null;
  failure_category: string | null;
  files_modified: string | null; // raw comma-joined string, never split (P-30)
  milestone_group_id?: string | null;
  milestone_index?: number | null;
  milestone_total?: number | null;
  [k: string]: unknown;
}

export interface HistoryList {
  runs: RunSummary[];
  next_cursor: string | null;
  [k: string]: unknown;
}

/** A recorded run event: everything beyond the known keys is preserved verbatim. */
export interface RunEvent {
  event?: string;
  attempt?: number;
  source?: string;
  authority?: string;
  at?: string;
  payload?: unknown;
  [k: string]: unknown;
}

export interface RunDetail {
  run: RunSummary;
  /** Stored TEXT columns in recorded form (JSON text or raw string) with per-field availability. */
  fields: Record<string, Section<string>>;
  run_events: Section<RunEvent[]>;
  evidence_records: Section<unknown[]>;
  gate_outcomes: Section<unknown[]>;
  model_hops: Section<unknown[]>;
  generation_metrics: Section<Record<string, unknown>>;
  failure_report: Section<unknown[]>;
  context: Section<ContextRecord>;
  attribution: Section<AttributionRecord>;
  diagnostics: Section<unknown>;
  comparisons: Section<Comparison[]>;
  output: Section<unknown>;
  [k: string]: unknown;
}

export interface ContextItem {
  path: string;
  tier?: string | null;
  member_ids?: string[] | null;
  omitted?: boolean;
  omission_reason?: string | null;
  [k: string]: unknown;
}

export interface TokenAccounting {
  estimated?: Record<string, number> | null;
  provider_reported?: Record<string, number> | null;
  [k: string]: unknown;
}

export interface ContextRecord {
  items: ContextItem[];
  tokens: TokenAccounting | null;
  package_hash?: string | null;
  [k: string]: unknown;
}

export interface AttributionRecord {
  first_incorrect_state?: string | null;
  cause?: string | null;
  category?: string | null;
  evidence_ids?: string[] | null;
  [k: string]: unknown;
}

export interface Comparison {
  path: string;
  before: { text: string; provenance: string; revision?: string | null };
  after: { text: string; provenance: string; revision?: string | null };
  [k: string]: unknown;
}

export interface Prompt {
  prompt_rendered: string | null;
  role: string | null;
  scope: string | null;
  [k: string]: unknown;
}

export interface WorkspaceStatus {
  workspace: string;
  run_active: boolean | null;
  status: string | null;
  exit_code: number | null;
  assessment: unknown;
  [k: string]: unknown;
}

export type KupRequest =
  | { operation: 'capabilities' }
  | { operation: 'history.list'; limit?: number; cursor?: string }
  | { operation: 'history.detail'; run_id: string }
  | { operation: 'history.prompt'; run_id: string }
  | { operation: 'workspace.status'; workspace: string };
