import { AvailabilityView, Badge, Panel, useDispatch, useModuleSelection, useRunEvidence, type ModuleDefinition } from '../../core';

/** The recorded records as they are: kind, identity and provenance only. Payloads open in the timeline inspector (Raw). */
function EvidenceTable() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const seq = useModuleSelection('eventSeq');
  return (
    <AvailabilityView value={evidence}>
      {(data) => (
        <Panel title={`Evidence records (${data.records.length})`}>
          {data.verify && !data.verify.sealed ? <p className="muted small" style={{ margin: '0 0 4px' }}>Read from an unsealed store.</p> : null}
          <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Evidence records">
            <thead><tr className="muted"><th align="left">seq</th><th align="left">kind</th><th align="left">attempt</th><th align="left">role</th><th align="left">provenance</th><th align="left">blobs</th></tr></thead>
            <tbody>
              {data.records.map((r) => (
                <tr key={r.seq} aria-selected={r.seq === seq} onClick={() => dispatch({ type: 'selectEventSeq', eventSeq: r.seq })} style={{ cursor: 'pointer', background: r.seq === seq ? 'var(--ck-accent-bg)' : undefined }}>
                  <td className="mono">{r.seq}</td><td>{r.kind}</td><td>{r.attempt_number ?? '-'}</td><td>{r.role ?? '-'}</td>
                  <td><Badge tone={r.provenance === 'MODEL_CLAIMED' ? 'warn' : 'neutral'}>{r.provenance}</Badge></td><td>{Object.keys(r.blobs ?? {}).length || Object.keys(r.content_digests ?? {}).length ? `${Object.keys(r.blobs ?? {}).length} stored / ${Object.keys(r.content_digests ?? {}).length} digested` : '-'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}
    </AvailabilityView>
  );
}

export const evidenceModule: ModuleDefinition = {
  id: 'evidence',
  placement: { page: 'runs', surface: 'center-tab', label: 'Evidence', order: 70 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'eventSeq'],
  whenUnavailable: 'explain',
  render: EvidenceTable,
};
