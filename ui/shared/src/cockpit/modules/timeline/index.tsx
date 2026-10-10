import { useMemo } from 'react';
import { AvailabilityView, Badge, OPEN_TARGETS, Panel, gateKey, payloadOf, useDispatch, useModuleSelection, useRunEvidence, type EvidenceRecord, type ModuleDefinition, type Tone } from '../../core';

interface Node { key: string; seq: number; label: string; tone: Tone; unitId: string | null; attempt: string | null; gateId: string | null }
interface Attempt { number: number; nodes: Node[] }
interface Unit { id: string; attempts: Attempt[]; loose: Node[] }

function describe(r: EvidenceRecord): Pick<Node, 'label' | 'tone'> | null {
  const p = payloadOf(r);
  switch (r.kind) {
    case 'gate.result': return { label: `${String(p.gate ?? 'gate')} ${p.success === true ? 'passed' : p.success === false ? 'failed' : '(result not recorded)'}`, tone: p.success === true ? 'ok' : p.success === false ? 'bad' : 'neutral' };
    case 'candidate.change': return { label: `${String(p.decision ?? 'change')}: ${String(p.path ?? 'path not recorded')}`, tone: p.decision === 'REFUSED' ? 'warn' : 'neutral' };
    case 'model.result': return { label: `model call${r.call_seq != null ? ` #${r.call_seq}` : ''} (${r.role ?? 'role not recorded'})${typeof p.finish_reason === 'string' ? ` · ${p.finish_reason}` : ''}`, tone: 'neutral' };
    case 'model.request': return { label: `model call${r.call_seq != null ? ` #${r.call_seq}` : ''} (${r.role ?? 'role not recorded'})`, tone: 'neutral' };
    // The recorder mirrors the trace row's gate outcomes and run events into the store; they are recorded facts, shown as such.
    case 'mirror.gate_outcome': return { label: `${String(p.type ?? 'gate')} ${p.success === true ? 'passed' : p.success === false ? 'failed' : '(result not recorded)'} (mirrored outcome)`, tone: p.success === true ? 'ok' : p.success === false ? 'bad' : 'neutral' };
    case 'mirror.event': return { label: `event: ${String(p.kind ?? 'kind not recorded')}`, tone: 'neutral' };
    case 'fallback.decision': return { label: `fallback decision (${String(p.phase ?? 'phase not recorded')})`, tone: 'warn' };
    case 'recovery.decision': return { label: 'recovery decision', tone: 'warn' };
    case 'attempt.closed': return { label: `attempt closed: ${String(p.outcome ?? 'outcome not recorded')}`, tone: 'neutral' };
    case 'diagnosis': return { label: `diagnosis ${String(p.reason_code ?? '')}`.trim(), tone: 'warn' };
    case 'regression.decision': return { label: `regression ${String(p.classification ?? 'decision')}`, tone: 'bad' };
    case 'retry.delta': return { label: 'retry delta', tone: 'neutral' };
    case 'attempt.concluded': return { label: p.succeeded === true ? 'attempt succeeded' : 'attempt failed', tone: p.succeeded === true ? 'ok' : 'bad' };
    case 'run.closed': return { label: `run closed: ${String(p.terminal_status ?? 'not recorded')}`, tone: 'neutral' };
    default: return null;
  }
}

/** The one hierarchical navigator: unit > attempt > recorded nodes. Structure comes only from recorded identity fields. */
export function buildTree(records: readonly EvidenceRecord[]): { units: Unit[]; run: Node[] } {
  const units = new Map<string, Unit>();
  const run: Node[] = [];
  // A call recorded as model.result is one node; its model.request (when the wire was captured too) is the same call.
  const answered = new Set(records.filter((r) => r.kind === 'model.result').map((r) => r.call_seq));
  for (const r of records) {
    if (r.kind === 'model.request' && answered.has(r.call_seq)) continue;
    const d = describe(r);
    if (!d) continue;
    const p = payloadOf(r);
    const attempt = r.attempt_number ?? null;
    const node: Node = { key: `${r.seq}`, seq: r.seq, ...d, unitId: r.unit_id ?? null, attempt: attempt === null ? null : String(attempt), gateId: attempt !== null && (r.kind === 'gate.result' || r.kind === 'mirror.gate_outcome') ? gateKey(attempt, String(r.kind === 'gate.result' ? p.gate : p.type), r.seq) : null };
    if (!r.unit_id) { run.push(node); continue; }
    const unit = units.get(r.unit_id) ?? { id: r.unit_id, attempts: [], loose: [] };
    units.set(unit.id, unit);
    if (attempt === null) { unit.loose.push(node); continue; }
    let a = unit.attempts.find((x) => x.number === attempt);
    if (!a) { a = { number: attempt, nodes: [] }; unit.attempts.push(a); }
    a.nodes.push(node);
  }
  return { units: [...units.values()], run };
}

function Timeline() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const seq = useModuleSelection('eventSeq');
  const attempt = useModuleSelection('attempt');
  const tree = useMemo(() => (evidence.state === 'ready' || evidence.state === 'degraded' ? buildTree(evidence.data.records) : null), [evidence]);
  const pick = (n: Node) => dispatch({ type: 'selectTimelineNode', node: { unitId: n.unitId, attempt: n.attempt, eventSeq: n.seq, gateId: n.gateId } });
  const row = (n: Node) => (
    <li key={n.key}>
      <button type="button" aria-selected={n.seq === seq} onClick={() => pick(n)}><Badge tone={n.tone}>{n.seq}</Badge> {n.label}</button>
    </li>
  );
  return (
    <Panel title="Timeline">
      {evidence.state === 'degraded' ? <p style={{ margin: '0 0 6px' }}><Badge tone="warn" title={evidence.reasons.join(' ')}>unsealed store</Badge> <span className="muted small">details in the trust strip</span></p> : null}
      <AvailabilityView value={evidence}>
        {(data) => (
          <>
            <ul className="ck-tree root" role="tree" aria-label="Execution timeline">
              {tree?.run.map(row)}
              {tree?.units.map((u) => (
                <li key={u.id}>
                  <div className="ck-group">Unit {u.id}</div>
                  <ul className="ck-tree">
                    {u.loose.map(row)}
                    {u.attempts.sort((a, b) => a.number - b.number).map((a) => (
                      <li key={a.number}>
                        <button type="button" aria-selected={attempt === String(a.number)} onClick={() => dispatch({ type: 'selectTimelineNode', node: { unitId: u.id, attempt: String(a.number), eventSeq: null, gateId: null } })}>Attempt {a.number}</button>
                        <ul className="ck-tree">{a.nodes.map(row)}</ul>
                      </li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
            <Detail records={data.records} seq={seq} />
          </>
        )}
      </AvailabilityView>
    </Panel>
  );
}

function Detail({ records, seq }: { records: readonly EvidenceRecord[]; seq: number | null }) {
  const dispatch = useDispatch();
  const r = records.find((x) => x.seq === seq);
  if (!r) return <p className="muted">Select a node to see exactly what was recorded.</p>;
  return (
    <div aria-label="Selected record">
      <div className="ck-group">Record {r.seq} · {r.kind} · provenance {r.provenance}{r.phase_provenance ? <> · phase <Badge tone="info">DERIVED</Badge></> : null}</div>
      <div style={{ display: 'flex', gap: 6, margin: '4px 0' }}>
        {r.kind === 'gate.result' || r.kind === 'mirror.gate_outcome' ? <button type="button" className="small" onClick={() => { const p = payloadOf(r); dispatch({ type: 'selectGate', gateId: gateKey(r.attempt_number ?? null, String(r.kind === 'gate.result' ? p.gate : p.type), r.seq) }); dispatch({ type: 'centralTab', tab: OPEN_TARGETS.validation }); }}>Open in Validation</button> : null}
        {r.kind === 'candidate.change' ? <button type="button" className="small" onClick={() => { const p = payloadOf(r); if (typeof p.path === 'string') dispatch({ type: 'selectFile', filePath: p.path }); dispatch({ type: 'centralTab', tab: OPEN_TARGETS.changes }); }}>Open in Changes</button> : null}
        <button type="button" className="small" onClick={() => dispatch({ type: 'centralTab', tab: OPEN_TARGETS.evidence })}>Open evidence</button>
      </div>
      <pre className="mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(payloadOf(r), null, 1)}</pre>
    </div>
  );
}

export const timelineModule: ModuleDefinition = {
  id: 'timeline',
  placement: { page: 'runs', surface: 'inspector', label: 'Timeline', order: 10 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'eventSeq', 'attempt'],
  whenUnavailable: 'explain',
  render: Timeline,
};
