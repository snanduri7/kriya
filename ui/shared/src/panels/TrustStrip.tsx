import type { Capabilities, HistoryList, KupEnvelope, RunDetail, WorkspaceStatus } from '../model/kup';
import type { SlotState } from '../state/requests';
import { isRecorded } from '../model/availability';
import { eventDetails } from '../model/normalize';
import { sanitizeText } from '../render/sanitize';
import type { FreshnessLabel, SnapshotSession } from '../state/snapshot';

export interface TrustStripProps {
  capabilities: SlotState<KupEnvelope<Capabilities>>;
  list: SlotState<KupEnvelope<HistoryList>>;
  status: SlotState<KupEnvelope<WorkspaceStatus>>;
  detail: RunDetail | null;
  workspacePath: string | null;
  hostKind: string;
  fixtureMode: boolean;
  snapshot: SnapshotSession;
  label: FreshnessLabel;
}

/** Recorded model / qualification facts for the selected run, or "unknown" (P-22), read from Kriya's own event
 * shapes: model.transition details.to (a ModelRequestProfile: model, qualification) and model.role_metrics
 * details.rows (RoleRuntimeMetrics rows: role, model, ...; the developer row preferred). A recorded Developer request
 * profile (transition) is the more specific fact and outranks the aggregate metrics row; among transitions the last wins. */
export function recordedModelFacts(detail: RunDetail | null): { model: string; qualification: string } {
  let model = 'unknown', qualification = 'unknown', fromProfile = false;
  if (detail && isRecorded(detail.run_events)) {
    for (const e of detail.run_events.data) {
      const d = eventDetails(e);
      if (e.kind === 'model.transition') {
        const to = typeof d.to === 'object' && d.to !== null ? (d.to as Record<string, unknown>) : {};
        if (typeof to.model === 'string') { model = to.model; fromProfile = true; }
        if (typeof to.qualification === 'string') qualification = to.qualification;
      }
      if (e.kind === 'model.role_metrics' && !fromProfile && Array.isArray(d.rows)) {
        const rows = d.rows.filter((r): r is Record<string, unknown> => typeof r === 'object' && r !== null);
        const row = rows.find((r) => r.role === 'developer') ?? rows[0];
        if (row && typeof row.model === 'string') model = row.model;
      }
    }
  }
  return { model, qualification };
}

export function TrustStrip({ capabilities, list, status, detail, workspacePath, hostKind, fixtureMode, snapshot, label }: TrustStripProps) {
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
      {item('History store', source?.trace_database ?? 'unknown (no response yet)')}
      {item('Displayed snapshot', snapshot.pinnedId ?? 'none', snapshot.pinnedId ? (snapshot.pinnedBy === 'acquired' ? 'acquired by this session' : 'chosen from the published list') : undefined)}
      {item('Snapshot integrity', snapshot.verification ? `digest verified at pin (${snapshot.verification.verified_at}); metadata checked per query` : 'none pinned', snapshot.verification?.sha256 ? `sha256 ${snapshot.verification.sha256.slice(0, 16)}…` : undefined)}
      {item('Snapshot', label.headline, label.metadata === 'change_detected' ? 'source metadata change detected' : label.metadata === 'no_change_detected' ? 'no metadata change detected (not a freshness guarantee)' : undefined)}
      {item('Workspace', workspacePath ?? 'none selected', runActive)}
      {item('Kriya', identity, capabilities.current ? undefined : capabilities.error ? `unverified: ${capabilities.error.code}` : 'unverified')}
      {item('Model / qualification', `${facts.model} / ${facts.qualification}`, detail ? 'recorded in the snapshot for the selected run (historical, not live status)' : undefined)}
      {item('Observed', list.observedAt ?? 'never', list.error ? `last read failed: ${list.error.code}` : list.current ? 'read of the displayed snapshot succeeded' : undefined)}
      {item('Host', `${hostKind}${fixtureMode ? ' (fixtures - matrix protection D-9)' : ''}`)}
    </header>
  );
}
