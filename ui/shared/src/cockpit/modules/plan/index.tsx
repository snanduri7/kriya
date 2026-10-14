import {
  AvailabilityView, Badge, KeyValue, Panel, attemptFor, authorityTargets, obligations, planUnits, requirementsFacts, runEventsOf, shortDigest, useModuleSelection, useRunDetail, useRunEvidence, type ModuleDefinition, type RunEvidence,
} from '../../core';

const NOT_RECORDED = 'Not recorded by this Kriya version';

function PlanBody({ data }: { data: RunEvidence }) {
  const detail = useRunDetail();
  const attemptSel = useModuleSelection('attempt');
  const events = runEventsOf(detail.state === 'ready' ? detail.data : null);
  const units = planUnits(data.records);
  const which = attemptFor(data.records, attemptSel, ['authority.snapshot']);
  const authority = authorityTargets(data.records, which);
  const ledger = obligations(data.records, which);
  const req = requirementsFacts(events);
  const hasPlanFields = units.some((u) => u.planId !== null || u.plannedFiles.length > 0 || u.currentSubtaskId !== null);
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <Panel title="Units">
        {units.length === 0 ? <p className="muted" style={{ margin: 0 }}>No unit record in the store.</p> : (
          <ul className="ck-tree root" aria-label="Units">
            {units.map((u) => (
              <li key={u.seq} style={{ marginBottom: 8 }}>
                <div><b>Unit {u.unitId}</b> <Badge>{u.unitKind ?? 'kind not recorded'}</Badge> {u.invocationSeq !== null ? <span className="muted small">invocation {u.invocationSeq}</span> : null}{u.resume ? <Badge tone="warn">resumed</Badge> : null}</div>
                <KeyValue items={[
                  ['Plan', u.planId ? `${u.planId} (fingerprint ${shortDigest(u.planFingerprint)})` : 'no plan identity recorded (a direct unit)'],
                  ['Subtask', u.currentSubtaskId ?? 'none recorded'],
                  ['Planned source files', u.plannedFiles.length ? u.plannedFiles.join(', ') : 'none recorded'],
                  ['Write scope', u.authorizedWriteScope === null ? `not recorded${u.writeScopeMode ? ` (mode ${u.writeScopeMode})` : ''}` : `${u.writeScopeMode ?? 'mode not recorded'}: ${u.authorizedWriteScope.join(', ') || 'no file'}`],
                  ['Milestone', u.milestoneGroupId ? `${u.milestoneGroupId}, position ${u.milestoneIndex === null ? '?' : u.milestoneIndex + 1}` : 'none recorded'],
                  ['Closed', u.closed ? `${u.closed.outcome ?? 'outcome not recorded'}${u.closed.failureCategory ? `, failure category ${u.closed.failureCategory}` : ''}` : 'no unit.closed record'],
                ]} />
              </li>
            ))}
          </ul>
        )}
        {units.length > 0 && !hasPlanFields ? <p className="muted small" style={{ margin: 0 }}>These units carry no plan identity or planned files: nothing was planned ahead for this run, and none is shown.</p> : null}
      </Panel>
      <Panel title={which === null ? 'Declared targets' : `Declared targets · attempt ${which}`}>
        {authority ? (
          <>
            <p className="muted small" style={{ margin: '0 0 4px' }}>From the authority snapshot (record {authority.seq}); write scope mode {authority.writeScopeMode ?? 'not recorded'}{authority.writeScope.length ? `, scope ${authority.writeScope.join(', ')}` : ''}.</p>
            <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Declared targets">
              <thead><tr className="muted"><th align="left">file</th><th align="left">operations</th><th align="left">in write scope</th><th align="left">shown as</th></tr></thead>
              <tbody>{authority.targets.map((t) => <tr key={t.path}><td className="mono">{t.path}</td><td>{t.operations.join(', ') || 'none'}{t.requestedOperation ? ` (requested ${t.requestedOperation})` : ''}</td><td>{t.inWriteScope === null ? 'not recorded' : t.inWriteScope ? 'yes' : 'no'}</td><td>{t.shownTier ?? 'not recorded'}{t.shownIsExact === false ? ', not exact' : ''}{t.wholeFileAuthority ? ', whole-file authority' : ''}</td></tr>)}</tbody>
            </table>
          </>
        ) : <p className="muted" style={{ margin: 0 }}>No authority snapshot was recorded.</p>}
      </Panel>
      <Panel title="Requirements and plan lineage">
        {req.derived ? (
          <ul style={{ margin: 0, paddingLeft: 16 }} aria-label="Requirements">{req.derived.map((r) => <li key={r.id}><b>{r.id}</b> <Badge>{r.kind ?? 'kind not recorded'}</Badge> {r.text ?? '(text not recorded)'}</li>)}</ul>
        ) : <p className="muted" style={{ margin: 0 }}>No derived-requirements event was recorded.</p>}
        {req.lineage.map((l, i) => <p key={i} className="muted small" style={{ margin: '4px 0 0' }}>At the {l.stage ?? 'unnamed'} stage: cited {l.cited.join(', ') || 'none'}; omitted but still active {l.omitted.join(', ') || 'none'}.</p>)}
      </Panel>
      <Panel title="Plan validation and replanning">
        {req.planEvents.length ? (
          <ul style={{ margin: 0, paddingLeft: 16 }} aria-label="Plan validation events">{req.planEvents.map((e, i) => <li key={i}><span className="mono">{e.kind}</span>{e.valid !== null ? (e.valid ? ' valid' : ' invalid') : ''}{e.classification ? `, ${e.classification}` : ''}{e.accepted !== null ? (e.accepted ? ', accepted' : ', not accepted') : ''}{e.reasonCodes.length ? `; reasons: ${e.reasonCodes.join(', ')}` : ''}</li>)}</ul>
        ) : <p className="muted" style={{ margin: 0 }}>No plan validation or repair event was recorded.</p>}
      </Panel>
      <Panel title="Obligations">
        {ledger ? <KeyValue items={[['Ledger revision', ledger.ledgerRevision === null ? 'not recorded' : String(ledger.ledgerRevision)], ['Ledger digest', shortDigest(ledger.ledgerDigest)], ['Unresolved terminal obligations', String(ledger.unresolvedTerminal)]]} /> : <p className="muted" style={{ margin: 0 }}>No obligations snapshot was recorded.</p>}
      </Panel>
      <Panel title="Not recorded">
        <KeyValue items={[['Dependencies / order between units', NOT_RECORDED], ['Immutable / preserved targets', NOT_RECORDED]]} />
      </Panel>
    </div>
  );
}

function Plan() {
  const evidence = useRunEvidence();
  return <AvailabilityView value={evidence}>{(data) => <PlanBody data={data} />}</AvailabilityView>;
}

export const planModule: ModuleDefinition = {
  id: 'plan',
  placement: { page: 'runs', surface: 'center-tab', label: 'Plan', order: 30, group: 'details' },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'attempt'],
  whenUnavailable: 'explain',
  render: Plan,
};
