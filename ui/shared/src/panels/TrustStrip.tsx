import type { Capabilities, HistoryList, KupEnvelope, RunDetail, WorkspaceStatus } from '../model/kup';
import type { SlotState } from '../state/requests';
import { isRecorded } from '../model/availability';
import { sanitizeText } from '../render/sanitize';

export interface TrustStripProps {
  capabilities: SlotState<KupEnvelope<Capabilities>>;
  list: SlotState<KupEnvelope<HistoryList>>;
  status: SlotState<KupEnvelope<WorkspaceStatus>>;
  detail: RunDetail | null;
  workspacePath: string | null;
  hostKind: string;
  fixtureMode: boolean;
}

/** Recorded model / qualification facts for the selected run, or "unknown" (P-22). */
export function recordedModelFacts(detail: RunDetail | null): { model: string; qualification: string } {
  let model = 'unknown', qualification = 'unknown';
  if (detail && isRecorded(detail.run_events)) {
    for (const e of detail.run_events.data) {
      const p = (e.payload ?? {}) as Record<string, unknown>;
      if (e.event === 'model.role_metrics' || e.event === 'model.transition') {
        const m = p.model ?? p.to ?? (Array.isArray(p.rows) ? (p.rows[0] as Record<string, unknown> | undefined)?.model : undefined);
        if (typeof m === 'string') model = m;
      }
      if (typeof p.qualification === 'string') qualification = p.qualification;
    }
  }
  return { model, qualification };
}

export function TrustStrip({ capabilities, list, status, detail, workspacePath, hostKind, fixtureMode }: TrustStripProps) {
  const caps = capabilities.value?.data ?? null;
  const source = list.value?.source ?? null;
  const facts = recordedModelFacts(detail);
  const identity = caps ? `${caps.identity.kriya_version ?? 'unknown'}${caps.identity.commit ? ` @ ${String(caps.identity.commit).slice(0, 10)}` : ''}` : 'unknown';
  const runActive: string = status.value?.data ? (status.value.data.run_active === null ? 'unknown' : status.value.data.run_active ? 'RUN_ACTIVE' : 'idle') : 'unknown';
  const item = (label: string, value: string, extra?: string) => (
    <div className="trust-item">
      <span className="trust-label">{label}</span>
      <span className="trust-value" title={value}>{sanitizeText(value)}</span>
      {extra ? <span className="trust-extra">{extra}</span> : null}
    </div>
  );
  return (
    <header className="trust" role="region" aria-label="Trust strip: provenance and scope">
      {item('History store', source?.trace_database ?? 'unknown (no list response)', list.current ? 'current' : 'stale')}
      {item('Workspace', workspacePath ?? 'none selected', runActive)}
      {item('Kriya', identity, capabilities.current ? undefined : 'unverified')}
      {item('Model / qualification', `${facts.model} / ${facts.qualification}`, detail ? 'recorded for selected run' : undefined)}
      {item('Observed', list.observedAt ?? 'never', list.error ? `last refresh failed: ${list.error.code}` : undefined)}
      {item('Host', `${hostKind}${fixtureMode ? ' (fixtures - matrix protection D-9)' : ''}`)}
    </header>
  );
}
