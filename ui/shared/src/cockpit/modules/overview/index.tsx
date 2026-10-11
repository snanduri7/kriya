import { AvailabilityView, CopyText, KeyValue, Panel, payloadOf, ofKind, useRunContext, useRunEvidence, type ModuleDefinition } from '../../core';

function Overview() {
  const run = useRunContext();
  const evidence = useRunEvidence();
  return (
    <AvailabilityView value={run}>
      {(ctx) => (
        <div style={{ display: 'grid', gap: 8 }}>
          <Panel title="Run">
            <KeyValue items={[
              ['Run ID', <span key="rid"><span className="mono">{ctx.runId}</span> <CopyText text={ctx.runId} what="run ID" /></span>],
              ['Goal', ctx.goal ?? 'not recorded'], ['Stored status', ctx.status === 'in_progress' ? 'Unfinished (the trace row says in_progress; this is not a claim that a run is active)' : ctx.status ?? 'not recorded'],
              ...(ctx.failureCategory ? [['Failure category', <span key="fc">{ctx.failureCategory} <CopyText text={ctx.failureCategory} what="failure category" /></span>] as const] : []),
              ['Workspace', ctx.workspace.identity ?? 'not recorded'],
              ['Baseline revision', ctx.baseline.revision ?? 'not recorded (no baseline is assumed)'], ['Baseline tree', ctx.baseline.treeHash ?? 'not recorded'],
              ['Evidence store', ctx.evidence.store === 'present' ? `present, capture ${ctx.evidence.captureMode ?? 'not recorded'}` : 'absent'],
              ['Trace row "attempts" (as stored)', ctx.storedAttempts === null ? 'not recorded' : `${ctx.storedAttempts} - Kriya's retry counter (retries + best-of-N candidates), not the number of attempts made; the attempt count below is derived from the run's evidence`],
            ]} />
          </Panel>
          <AvailabilityView value={evidence}>
            {(data) => {
              const attempts = ofKind(data.records, 'attempt.opened').length;
              const concluded = ofKind(data.records, 'attempt.concluded').map((r) => payloadOf(r).succeeded === true);
              const closed = ofKind(data.records, 'run.closed')[0];
              return (
                <Panel title="Recorded outcome">
                  <KeyValue items={[
                    ['Attempts opened', String(attempts)],
                    ['Attempts concluded', concluded.length ? concluded.map((ok, i) => `#${i + 1} ${ok ? 'succeeded' : 'failed'}`).join(', ') : 'none recorded'],
                    ['Run closed', closed ? String(payloadOf(closed).terminal_status ?? 'terminal status not recorded') : 'no run.closed record'],
                  ]} />
                </Panel>
              );
            }}
          </AvailabilityView>
        </div>
      )}
    </AvailabilityView>
  );
}

export const overviewModule: ModuleDefinition = {
  id: 'overview',
  placement: { page: 'runs', surface: 'center-tab', label: 'Overview', order: 10 },
  requiredCapabilities: ['snapshot'], optionalCapabilities: ['evidence'], selectionDependencies: ['runId'],
  render: Overview,
};
