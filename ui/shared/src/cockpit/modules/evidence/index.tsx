import { useMemo, useState } from 'react';
import { AvailabilityView, Badge, Panel, describeRecord, recordContent, shortDigest, useDispatch, useModuleSelection, useRunEvidence, type EvidenceRecord, type ModuleDefinition, type RunEvidence } from '../../core';

type Group = 'none' | 'unit' | 'attempt' | 'kind';
const PAGE = 150;

function matches(r: EvidenceRecord, f: { kind: string; provenance: string; unit: string }, attempt: number | null): boolean {
  return (f.kind === '' || r.kind === f.kind) && (f.provenance === '' || r.provenance === f.provenance) && (f.unit === '' || r.unit_id === f.unit) && (attempt === null || r.attempt_number === attempt);
}

function Row({ r, selected, onSelect }: { r: EvidenceRecord; selected: boolean; onSelect(): void }) {
  const s = describeRecord(r);
  const content = recordContent(r);
  return (
    <>
      <tr aria-selected={selected} onClick={onSelect} style={{ cursor: 'pointer', background: selected ? 'var(--ck-accent-bg)' : undefined }}>
        <td className="mono">{r.seq}</td>
        <td><div>{s.title}</div><div className="muted small">{r.kind}</div></td>
        <td>{r.attempt_number ?? '-'}{r.unit_id ? <div className="muted small">{r.unit_id}</div> : null}</td>
        <td><Badge tone={r.provenance === 'MODEL_CLAIMED' ? 'warn' : 'neutral'}>{r.provenance}</Badge>{s.evidenceClass ? <> <Badge tone={s.evidenceClass === 'MODEL_CLAIMED' ? 'warn' : s.evidenceClass === 'MEASURED' ? 'ok' : 'info'}>{s.evidenceClass}</Badge></> : null}</td>
        <td className="muted small">{content.kept.length || content.digestOnly.length ? `${content.kept.length} kept, ${content.digestOnly.length} digest only` : '-'}</td>
      </tr>
      {selected ? (
        <tr><td /><td colSpan={4}>
          <div aria-label="Record detail" style={{ display: 'grid', gap: 4 }}>
            {s.facts.length ? <ul style={{ margin: 0, paddingLeft: 16 }}>{s.facts.map((f) => <li key={f}>{f}</li>)}</ul> : <span className="muted">No further recorded facts for this kind.</span>}
            <div className="muted small mono">record {r.seq} · prev {shortDigest(r.prev)} · phase {r.phase ?? 'not recorded'}{r.phase_provenance ? <> (<Badge tone="info">DERIVED</Badge> {r.phase_provenance})</> : null}{r.role ? ` · role ${r.role}` : ''}{r.call_seq != null ? ` · call ${r.call_seq}` : ''}</div>
            {content.kept.length || content.digestOnly.length ? <div className="muted small mono">{[...content.kept.map((k) => `${k}: kept ${shortDigest(r.blobs[k])}`), ...content.digestOnly.map((k) => `${k}: digest only ${shortDigest((r.content_digests as Record<string, { digest?: string }>)[k]?.digest)}`)].join(' · ')}</div> : null}
            <details><summary className="muted small">Raw payload</summary><pre className="mono" style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{JSON.stringify(r.payload, null, 1)}</pre></details>
          </div>
        </td></tr>
      ) : null}
    </>
  );
}

function Audit({ data }: { data: RunEvidence }) {
  const dispatch = useDispatch();
  const seq = useModuleSelection('eventSeq');
  const attemptSel = useModuleSelection('attempt');
  const [kind, setKind] = useState('');
  const [provenance, setProvenance] = useState('');
  const [unit, setUnit] = useState('');
  const [group, setGroup] = useState<Group>('none');
  const [scoped, setScoped] = useState(false);
  const [limit, setLimit] = useState(PAGE);
  const attempt = scoped && attemptSel !== null && Number.isInteger(Number(attemptSel)) ? Number(attemptSel) : null;
  const kinds = useMemo(() => { const m = new Map<string, number>(); for (const r of data.records) m.set(r.kind, (m.get(r.kind) ?? 0) + 1); return [...m.entries()].sort(); }, [data.records]);
  const provenances = useMemo(() => [...new Set(data.records.map((r) => r.provenance))].sort(), [data.records]);
  const units = useMemo(() => [...new Set(data.records.map((r) => r.unit_id).filter((u): u is string => !!u))].sort(), [data.records]);
  const rows = useMemo(() => data.records.filter((r) => matches(r, { kind, provenance, unit }, attempt)), [data.records, kind, provenance, unit, attempt]);
  const shown = rows.slice(0, limit);
  const groups = useMemo(() => {
    if (group === 'none') return [['', shown] as const];
    const key = (r: EvidenceRecord) => (group === 'unit' ? `Unit ${r.unit_id ?? '(none)'}` : group === 'attempt' ? (r.attempt_number == null ? 'No attempt' : `Attempt ${r.attempt_number}`) : r.kind);
    const m = new Map<string, EvidenceRecord[]>();
    for (const r of shown) m.set(key(r), [...(m.get(key(r)) ?? []), r]);
    return [...m.entries()];
  }, [group, shown]);
  const select = <T extends string>(label: string, value: T, set: (v: T) => void, options: ReadonlyArray<readonly [string, string]>) => (
    <label className="small">{label} <select aria-label={label} value={value} onChange={(e) => { set(e.target.value as T); setLimit(PAGE); }}>{options.map(([v, t]) => <option key={v} value={v}>{t}</option>)}</select></label>
  );
  return (
    <Panel title={`Evidence audit (${rows.length} of ${data.records.length} records)`}>
      {data.verify && !data.verify.sealed ? <p className="muted small" style={{ margin: '0 0 4px' }}>Read from an unsealed store.</p> : null}
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 10, marginBottom: 6 }}>
        {select('Kind', kind, setKind, [['', 'All kinds'], ...kinds.map(([k, c]) => [k, `${k} (${c})`] as const)])}
        {select('Provenance', provenance, setProvenance, [['', 'All'], ...provenances.map((p) => [p, p] as const)])}
        {select('Unit', unit, setUnit, [['', 'All units'], ...units.map((u) => [u, u] as const)])}
        {select('Group by', group, setGroup, [['none', 'No grouping'], ['unit', 'Unit'], ['attempt', 'Attempt'], ['kind', 'Kind']])}
        <label className="small"><input type="checkbox" checked={scoped} disabled={attemptSel === null} onChange={(e) => { setScoped(e.target.checked); setLimit(PAGE); }} /> Only the selected attempt{attemptSel !== null ? ` (${attemptSel})` : ''}</label>
      </div>
      {rows.length === 0 ? <div className="ck-state"><strong>No record matches these filters</strong>{data.records.length} record(s) exist in the store.</div> : (
        <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Evidence records">
          <thead><tr className="muted"><th align="left">seq</th><th align="left">record</th><th align="left">attempt</th><th align="left">provenance / class</th><th align="left">content</th></tr></thead>
          <tbody>
            {groups.map(([name, list]) => (
              <GroupRows key={name} name={name} list={list} seq={seq} onSelect={(r) => dispatch({ type: 'selectEventSeq', eventSeq: seq === r.seq ? null : r.seq })} />
            ))}
          </tbody>
        </table>
      )}
      {rows.length > shown.length ? <button type="button" onClick={() => setLimit(limit + PAGE)}>Show more ({rows.length - shown.length} further)</button> : null}
    </Panel>
  );
}

function GroupRows({ name, list, seq, onSelect }: { name: string; list: readonly EvidenceRecord[]; seq: number | null; onSelect(r: EvidenceRecord): void }) {
  return (
    <>
      {name ? <tr><td colSpan={5} className="ck-group">{name} ({list.length})</td></tr> : null}
      {list.map((r) => <Row key={r.seq} r={r} selected={r.seq === seq} onSelect={() => onSelect(r)} />)}
    </>
  );
}

function EvidenceTable() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{(data) => <Audit data={data} />}</AvailabilityView>;
}

export const evidenceModule: ModuleDefinition = {
  id: 'evidence',
  placement: { page: 'runs', surface: 'center-tab', label: 'Evidence', order: 70 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'eventSeq', 'attempt'],
  whenUnavailable: 'explain',
  render: EvidenceTable,
};
