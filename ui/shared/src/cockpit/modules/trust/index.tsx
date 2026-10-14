import { AvailabilityView, Badge, useRunContext, useRunEvidence, type ModuleDefinition } from '../../core';

function Trust() {
  const evidence = useRunEvidence();
  const run = useRunContext();
  return (
    <div className="ck-panel" aria-label="Trust strip">
      <AvailabilityView value={evidence} notice>
        {(data, notes) => {
          const sealed = data.verify?.sealed;
          const ctx = run.state === 'ready' ? run.data : null;
          const verified = data.verify?.status === 'VERIFIED' && sealed === true;
          return (
            <div>
              <div data-testid="trust-line"><span aria-hidden="true">{verified ? '✓' : '•'}</span> {verified ? 'Evidence verified' : sealed === false ? 'Evidence not sealed (the run did not finish)' : `Evidence: ${data.verify?.status ?? 'not verified'}`}</div>
              <details className="ck-tech">
                <summary>Technical details</summary>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
              <Badge tone="info">capture: {data.manifest.manifest?.capture ?? 'not recorded'}</Badge>
              <Badge tone={sealed ? 'ok' : 'warn'}>{sealed ? 'sealed' : 'unsealed'}</Badge>
              <Badge tone={data.verify?.status === 'VERIFIED' ? 'ok' : 'warn'}>verification: {data.verify?.status ?? 'not run'}</Badge>
              <Badge>consistency: {data.provenance.records?.consistency?.kind ?? 'not recorded'}</Badge>
              <Badge>{ctx?.baseline.source === 'recorded' ? `baseline ${ctx.baseline.revision?.slice(0, 10) ?? ctx.baseline.treeHash?.slice(0, 10)}` : 'baseline not recorded'}</Badge>
              {ctx ? <Badge>provider: {ctx.provider}</Badge> : null}
              {notes.length ? <Badge tone="warn">limits apply</Badge> : null}
            </div>
              </details>
            </div>
          );
        }}
      </AvailabilityView>
    </div>
  );
}

export const trustModule: ModuleDefinition = {
  id: 'trust',
  placement: { page: 'runs', surface: 'header', label: 'Trust', order: 10 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId'],
  whenUnavailable: 'explain',
  render: Trust,
};
