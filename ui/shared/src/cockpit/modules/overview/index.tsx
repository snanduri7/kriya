import { AvailabilityView, Badge, CopyText, KeyValue, Panel, changeFacts, describeGate, hasData, payloadOf, ofKind, useRunContext, useRunEvidence, validationFacts, type ModuleDefinition } from '../../core';

const OUTCOME_WORDS: Record<string, string> = { success: 'Succeeded', failure: 'Failed', in_progress: 'Run not finished' };

function Overview() {
  const run = useRunContext();
  const evidence = useRunEvidence();
  return (
    <AvailabilityView value={run}>
      {(ctx) => (
        <div style={{ display: 'grid', gap: 8 }}>
          <Panel title="What happened">
            {(() => {
              const records = hasData(evidence) ? evidence.data.records : null;
              const changes = records ? changeFacts(records) : null;
              const gates = records ? validationFacts(records) : null;
              return (
                <>
                  <p style={{ margin: '0 0 6px' }}><strong>{ctx.status ? OUTCOME_WORDS[ctx.status] ?? ctx.status : 'Outcome not recorded'}</strong>{ctx.goal ? <span className="muted"> · {ctx.goal}</span> : null}</p>
                  {changes === null || gates === null ? <p className="muted" style={{ margin: 0 }}>The changed files and the validation come from this run&apos;s evidence, which is not readable here (see below).</p> : (
                    <>
                      <div className="muted small">Changed</div>
                      {changes.length === 0 ? <p className="muted" style={{ margin: '0 0 6px' }}>No staged change is recorded for this run.</p>
                        : <ul aria-label="Changed files" style={{ margin: '2px 0 6px', paddingLeft: 18 }}>{changes.map((c) => <li key={c.path}><span className="mono">{c.path}</span> <span className="muted small">{c.linesAdded !== null && c.linesRemoved !== null ? `+${c.linesAdded} −${c.linesRemoved}` : 'line counts not recorded'}</span></li>)}</ul>}
                      <div className="muted small">Validation</div>
                      {gates.length === 0 ? <p className="muted" style={{ margin: 0 }}>No gate result is recorded for this run.</p>
                        : <div className="ck-badges">{gates.map((g) => <Badge key={g.type} tone={g.passed === true ? 'ok' : g.passed === false ? 'bad' : 'neutral'}>{g.passed === true ? '✓ ' : g.passed === false ? '✕ ' : ''}{describeGate(g)}</Badge>)}</div>}
                    </>
                  )}
                  <p className="muted small" style={{ margin: '6px 0 0' }}>The identifiers below are the recorded detail behind this summary.</p>
                </>
              );
            })()}
          </Panel>
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
