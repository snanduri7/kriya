/**
 * @kriya-ui/kup - KUP v1 contract for TypeScript consumers (06 Phase B). Types and validators are GENERATED from
 * ui/kup/schema (`npm run generate -w @kriya-ui/kup`); this module only adds the ergonomic wrappers. No dependency:
 * the validators are precompiled Ajv code with its one helper inlined, so they run under a CSP without eval.
 */
import type { ErrorObject } from 'ajv';
import type { Envelope, KupRequest, OpenInIdeRequest, OpenInIdeResult, HostInfo, Capabilities, HistoryList, RunDetail, Prompt, WorkspaceStatus, Section, RunEvent, Availability } from '../generated/ts/kup';
import * as validators from '../generated/validators.mjs';

export * from '../generated/ts/kup';
export const KUP_SCHEMA_VERSION = 1 as const;
export const KUP_OPERATIONS = ['capabilities', 'history.list', 'history.detail', 'history.prompt', 'workspace.status'] as const;
export const KUP_ERROR_CODES = ['UNSUPPORTED_SCHEMA_VERSION', 'INVALID_RESPONSE', 'STORE_BUSY', 'READ_ONLY_UNAVAILABLE', 'RESPONSE_TOO_LARGE', 'CONFIG_AUTHORITY_REFUSED'] as const;
export const AVAILABILITY_STATES: readonly Availability[] = ['recorded', 'not_recorded', 'unreadable', 'excluded', 'unsupported'];

/** A typed envelope: the generated Envelope with `data` narrowed to the operation payload (or null).
 * (An intersection, not Omit: Omit over an interface with an index signature widens every key to unknown.) */
export type KupEnvelope<T = unknown> = Envelope & { data: T | null };

type Validator = ((value: unknown) => boolean) & { errors?: ErrorObject[] | null };
const v = validators as unknown as Record<string, Validator>;

export interface ValidationFailure { ok: false; errors: string[] }
export type Validation<T> = { ok: true; value: T } | ValidationFailure;

function run<T>(name: string, value: unknown): Validation<T> {
  const fn = v[name];
  if (!fn) throw new Error(`no generated validator ${name}`);
  if (fn(value)) return { ok: true, value: value as T };
  return { ok: false, errors: (fn.errors ?? []).map((e) => `${e.instancePath || '/'} ${e.message ?? ''}${e.params && 'additionalProperty' in e.params ? ` (${String(e.params.additionalProperty)})` : ''}`.trim()) };
}

export const validate = {
  envelope: (x: unknown) => run<Envelope>('validateEnvelope', x),
  capabilities: (x: unknown) => run<Capabilities>('validateCapabilities', x),
  historyList: (x: unknown) => run<HistoryList>('validateHistoryList', x),
  runDetail: (x: unknown) => run<RunDetail>('validateRunDetail', x),
  runEvent: (x: unknown) => run<RunEvent>('validateRunEvent', x),
  prompt: (x: unknown) => run<Prompt>('validatePrompt', x),
  workspaceStatus: (x: unknown) => run<WorkspaceStatus>('validateWorkspaceStatus', x),
  section: (x: unknown) => run<Section>('validateSection', x),
  kupRequest: (x: unknown) => run<KupRequest>('validateKupRequest', x),
  openInIdeRequest: (x: unknown) => run<OpenInIdeRequest>('validateOpenInIdeRequest', x),
  openInIdeResult: (x: unknown) => run<OpenInIdeResult>('validateOpenInIdeResult', x),
  hostInfo: (x: unknown) => run<HostInfo>('validateHostInfo', x),
};

/** Validate the payload of an envelope by its operation (the envelope itself must already be valid). */
export function validateData(operation: string, data: unknown): Validation<unknown> {
  switch (operation) {
    case 'capabilities': return validate.capabilities(data);
    case 'history.list': return validate.historyList(data);
    case 'history.detail': return validate.runDetail(data);
    case 'history.prompt': return validate.prompt(data);
    case 'workspace.status': return validate.workspaceStatus(data);
    default: return { ok: false, errors: [`unknown operation ${operation}`] };
  }
}
