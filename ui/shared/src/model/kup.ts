/**
 * KUP v1 shapes for the panels (P-24, P-25): re-exported from @kriya-ui/kup, which is GENERATED from the JSON Schema
 * (Phase B). Only the generic refinements the panels rely on live here. Unknown fields are kept everywhere.
 */
import type { Section as KupSection, RunDetail as KupRunDetail, Envelope } from '@kriya-ui/kup';
export type {
  Availability, Capabilities, HistoryList, RunSummary, RunEvent, ContextItem, TokenAccounting, ContextRecord,
  AttributionRecord, Comparison, Prompt, WorkspaceStatus, KupError, KupRequest, KupEnvelope, Envelope, Consistency,
  SnapshotSummary, SnapshotAcquireResult, SnapshotList, SnapshotPrune, SnapshotVerify,
} from '@kriya-ui/kup';
export { KUP_SCHEMA_VERSION, AVAILABILITY_STATES, KUP_ERROR_CODES, CONSISTENCY_KINDS, SNAPSHOT_ID_RE } from '@kriya-ui/kup';
import type { ContextRecord, AttributionRecord, Comparison, RunEvent, RunSummary } from '@kriya-ui/kup';

export type KupErrorCode = (typeof import('@kriya-ui/kup').KUP_ERROR_CODES)[number] | 'HOST_ERROR';
export type KupOperation = Envelope['operation'];
export type KupSource = NonNullable<Envelope['source']>;
export type KupConsistency = NonNullable<Envelope['consistency']>;

/** An optional panel section with its data typed for the panel that reads it. */
export type Section<T = unknown> = Omit<KupSection, 'data'> & { data?: T | null };

/** The generated RunDetail with each section's data typed. */
export type RunDetail = Omit<KupRunDetail, 'run' | 'fields' | 'run_events' | 'evidence_records' | 'gate_outcomes' | 'model_hops' | 'generation_metrics' | 'failure_report' | 'context' | 'attribution' | 'diagnostics' | 'comparisons' | 'output'> & {
  run: RunSummary;
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
};
