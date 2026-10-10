import {
  AvailabilityView, Badge, OPEN_TARGETS, Panel, attemptFor, attemptOutcomes, diagnoses, fallbacks, firstFailedGate, gateRows, recoveries, runClosure, useDispatch, useModuleSelection, useRunContext,
  useRunEvidence, type DiagnosisFacts, type EvidenceClass, type ModuleDefinition, type RunEvidence,
} from '../../core';

const CLASS_TONE: Record<EvidenceClass, 'ok' | 'info' | 'warn' | 'neutral'> = { MEASURED: 'ok', DERIVED_DETERMINISTIC: 'info', MODEL_CLAIMED: 'warn', UNKNOWN: 'neutral' };

function ClassBadge({ d }: { d: DiagnosisFacts }) {
  // The recorded evidence class, exactly; absent means the record carried none (provenance is shown separately, never promoted to a class).
  return d.evidenceClass ? <Badge tone={CLASS_TONE[d.evidenceClass]}>{d.evidenceClass}</Badge> : <Badge>no evidence class recorded</Badge>;
}

function Why({ data, failureCategory }: { data: RunEvidence; failureCategory: string | null }) {
  const dispatch = useDispatch();
  const attemptSel = useModuleSelection('attempt');
  const gateSel = useModuleSelection('gateId');
  const records = data.records;
  const failedAttempts = attemptOutcomes(records).filter((o) => o.concluded === false).map((o) => o.attempt);
  const attempt = attemptFor(records, attemptSel, ['diagnosis', 'recovery.decision', 'attempt.concluded']) ?? null;
  const target = attemptSel === null && failedAttempts.length ? failedAttempts[failedAttempts.length - 1]! : attempt;
  const outcome = attemptOutcomes(records).find((o) => o.attempt === target) ?? null;
  const closure = runClosure(records);
  const gates = gateRows(records).filter((g) => g.attempt === target || g.attempt === null);
  const failedGate = firstFailedGate(gates);
  const dx = diagnoses(records).filter((d) => d.attempt === target || target === null);
  const rec = recoveries(records).filter((r) => r.attempt === target || target === null);
  const fb = fallbacks(records).filter((f) => f.attempt === target || target === null);
  const failed = outcome?.concluded === false || failedGate !== null || dx.length > 0;
  const gaps: string[] = [];
  if (failed && dx.length === 0) gaps.push('No causal diagnosis was recorded for this attempt.');
  if (failed && gates.length === 0) gaps.push('No gate result was recorded for this attempt.');
  if (dx.some((d) => d.messageKept)) gaps.push('The failure message text is kept as content and is not shown here.');
  if (data.manifest.manifest?.capture === 'digest_only') gaps.push('This run kept digests only: message and output text were not retained.');
  if (data.verify && !data.verify.sealed) gaps.push('The store is unsealed, so records after the last written one may be missing.');

  return (
    <div style={{ display: 'grid', gap: 8 }} aria-label="Why">
      <Panel title={target === null ? 'What happened' : `What happened · attempt ${target}`}>
        <p style={{ margin: 0 }}>
          {outcome ? `Attempt ${outcome.attempt} ${outcome.concluded === true ? 'succeeded' : outcome.concluded === false ? 'failed' : 'has no recorded conclusion'}` : 'No attempt conclusion was recorded'}
          {outcome?.closedOutcome ? ` (closed: ${outcome.closedOutcome})` : ''}{closure.present ? `; the run closed ${closure.terminalStatus ?? 'with no terminal status recorded'}` : '; the run recorded no closure'}.
        </p>
        {failureCategory ? <p className="muted" style={{ margin: '4px 0 0' }}>Stored failure category: <span className="mono">{failureCategory}</span></p> : null}
      </Panel>
      {!failed ? <p className="muted">No failure is recorded for this attempt.</p> : (
        <>
          <Panel title="First failed gate, in recorded order">
            {failedGate ? <p style={{ margin: 0 }}><span className="mono">{failedGate.type}</span> failed{failedGate.exitCode !== null ? ` (exit code ${failedGate.exitCode})` : ''}, record {failedGate.seq}. <button type="button" onClick={() => { dispatch({ type: 'selectGate', gateId: failedGate.id }); dispatch({ type: 'centralTab', tab: OPEN_TARGETS.validation }); }}>Open in Validation</button></p> : <p className="muted" style={{ margin: 0 }}>No failed gate was recorded.</p>}
            {gateSel ? <p className="muted small" style={{ margin: '4px 0 0' }}>Selected gate: <span className="mono">{gateSel}</span></p> : null}
          </Panel>
          {dx.length === 0 ? <div className="ck-state" role="note"><strong>No causal diagnosis recorded</strong>Kriya recorded the failure evidence below, but no causal diagnosis was recorded.</div> : dx.map((d) => (
            <Panel key={d.seq} title="Recorded diagnosis">
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                <li>Failure type <span className="mono">{d.type ?? 'not recorded'}</span>, reason code <span className="mono">{d.reasonCode ?? 'not recorded'}</span> <ClassBadge d={d} /></li>
                <li>Attribution: kind <span className="mono">{d.attributionKind ?? 'not recorded'}</span>, tier <span className="mono">{d.attributionTier ?? 'not recorded'}</span>, confidence <span className="mono">{d.attributionConfidence ?? 'not recorded'}</span> <ClassBadge d={d} /></li>
                {d.likelyFiles.length ? <li>Files the diagnosis names: <span className="mono">{d.likelyFiles.join(', ')}</span> <ClassBadge d={d} /></li> : null}
              </ul>
              <p className="muted small" style={{ margin: '4px 0 0' }}>Record {d.seq}, provenance {d.provenance}.</p>
            </Panel>
          ))}
          <Panel title="What Kriya did next">
            {rec.length === 0 && fb.length === 0 ? <p className="muted" style={{ margin: 0 }}>No recovery or fallback decision was recorded.</p> : (
              <ul style={{ margin: 0, paddingLeft: 16 }}>
                {rec.map((r) => <li key={`r${r.seq}`}>Recovery decision (record {r.seq}): action <span className="mono">{r.action ?? 'not recorded'}</span>{r.retry !== null ? `, retry ${r.retry ? 'yes' : 'no'}` : ''}{r.progress ? `, progress ${r.progress}` : ''}{r.reason ? `. Recorded reason: ${r.reason}` : ''}</li>)}
                {fb.map((f) => <li key={`f${f.seq}`}>Model fallback decision (record {f.seq}, {f.phase ?? 'phase not recorded'}): requested <span className="mono">{f.requested ?? '-'}</span>, selected <span className="mono">{f.selected ?? '-'}</span>{f.fell_back === true ? ' (fell back)' : f.fell_back === false ? ' (no fallback)' : ''}</li>)}
              </ul>
            )}
          </Panel>
          <Panel title="Unknowns and evidence gaps">
            {gaps.length ? <ul style={{ margin: 0, paddingLeft: 16 }}>{gaps.map((g) => <li key={g}>{g}</li>)}</ul> : <p className="muted" style={{ margin: 0 }}>None identified from the recorded evidence.</p>}
          </Panel>
          <div style={{ display: 'flex', gap: 6 }}>
            <button type="button" onClick={() => dispatch({ type: 'centralTab', tab: OPEN_TARGETS.changes })}>Open in Changes</button>
            <button type="button" onClick={() => dispatch({ type: 'centralTab', tab: OPEN_TARGETS.evidence })}>Open evidence</button>
          </div>
        </>
      )}
    </div>
  );
}

function WhyModule() {
  const evidence = useRunEvidence();
  const run = useRunContext();
  const failureCategory = run.state === 'ready' ? run.data.failureCategory : null;
  return <Panel title="WHY"><AvailabilityView value={evidence}>{(data) => <Why data={data} failureCategory={failureCategory} />}</AvailabilityView></Panel>;
}

export const whyModule: ModuleDefinition = {
  id: 'why',
  placement: { page: 'runs', surface: 'inspector', label: 'WHY', order: 20 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'attempt', 'gateId'],
  whenUnavailable: 'explain',
  render: WhyModule,
};
