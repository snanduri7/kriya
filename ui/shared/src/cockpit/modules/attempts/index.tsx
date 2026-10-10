import {
  AvailabilityView, Badge, KeyValue, Panel, attemptRows, compareAttempts, useDispatch, useModuleSelection, useRunEvidence, type AttemptRow, type ComparisonDimension, type ModuleDefinition,
} from '../../core';

const disposition = (a: AttemptRow): string => {
  const parts: string[] = [];
  for (const r of a.recovery) parts.push(`recovery: ${r.action ?? 'action not recorded'}${r.retry === null ? '' : r.retry ? ', retry' : ', no retry'}`);
  for (const f of a.fallback) if (f.fell_back === true) parts.push(`fell back to ${f.selected ?? '?'}`);
  if (a.reservedFallback === true) parts.push('reserved fallback attempt');
  return parts.length ? parts.join('; ') : 'none recorded';
};

const outcome = (a: AttemptRow) => (a.concluded === null ? 'no conclusion recorded' : a.concluded ? 'succeeded' : 'failed');

function Row({ a, selected, compared, onSelect, onCompare }: { a: AttemptRow; selected: boolean; compared: boolean; onSelect(): void; onCompare(): void }) {
  return (
    <tr aria-selected={selected} style={{ background: selected ? 'var(--ck-accent-bg)' : compared ? 'var(--ck-raised)' : undefined }}>
      <td><button type="button" aria-label={`Select attempt ${a.attempt}`} aria-pressed={selected} onClick={onSelect}>{a.attempt}</button></td>
      <td>{a.unitId ?? 'not recorded'}</td>
      <td>{a.calls.length ? a.calls.map((c) => `${c.role ?? '?'}${c.model ? ` · ${c.model}` : ''}`).join('; ') : 'no model call recorded'}</td>
      <td>{a.mode ? `${a.mode}${a.operation ? ` / ${a.operation}` : ''}` : 'not recorded'}{a.retryDelta ? <div className="muted small">after attempt {a.retryDelta.previousAttempt ?? '?'}: information gain {a.retryDelta.informationGain ?? 'not recorded'}</div> : null}</td>
      <td>{a.candidateFiles.length}</td>
      <td>{a.gates.passed} passed, {a.gates.failed} failed{a.gates.unknown ? `, ${a.gates.unknown} no result` : ''}</td>
      <td>{disposition(a)}</td>
      <td><Badge tone={a.concluded === true ? 'ok' : a.concluded === false ? 'bad' : 'neutral'}>{outcome(a)}</Badge></td>
      <td><button type="button" aria-pressed={compared} onClick={onCompare}>{compared ? 'Comparing (B)' : 'Compare as B'}</button></td>
    </tr>
  );
}

const cell = (v: string | null) => (v === null ? <span className="muted">Not recorded</span> : v);

function Comparison({ dims, a, b }: { dims: ComparisonDimension[]; a: number; b: number }) {
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Attempt comparison">
      <thead><tr className="muted"><th align="left">dimension</th><th align="left">A: attempt {a}</th><th align="left">B: attempt {b}</th><th align="left">same?</th><th align="left">recorded in</th></tr></thead>
      <tbody>
        {dims.map((d) => (
          <tr key={d.id} data-dimension={d.id}>
            <td>{d.label}</td><td className="mono" style={{ overflowWrap: 'anywhere' }}>{cell(d.a)}</td><td className="mono" style={{ overflowWrap: 'anywhere' }}>{cell(d.b)}</td>
            <td>{d.same === null ? <Badge>cannot compare</Badge> : d.same ? <Badge>same</Badge> : <Badge tone="warn">different</Badge>}</td><td className="muted small">{d.source}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function Attempts() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const selected = useModuleSelection('attempt');
  const compareWith = useModuleSelection('compareAttempt');
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const rows = attemptRows(data.records);
        if (!rows.length) return <div className="ck-state"><strong>No attempt recorded</strong>The store holds no record carrying an attempt number.</div>;
        const a = selected !== null ? Number(selected) : null;
        const b = compareWith !== null ? Number(compareWith) : null;
        const comparison = a !== null && b !== null && rows.some((r) => r.attempt === a) && rows.some((r) => r.attempt === b) ? compareAttempts(data.records, a, b) : null;
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <Panel title={`Attempts (${rows.length})`}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }} aria-label="Attempts">
                <thead><tr className="muted"><th align="left">#</th><th align="left">unit</th><th align="left">role · model</th><th align="left">mode / operation</th><th align="left">files</th><th align="left">gates</th><th align="left">retry / recovery / fallback</th><th align="left">outcome</th><th /></tr></thead>
                <tbody>
                  {rows.map((r) => (
                    <Row key={r.attempt} a={r} selected={a === r.attempt} compared={b === r.attempt}
                      onSelect={() => dispatch({ type: 'selectTimelineNode', node: { unitId: r.unitId, attempt: String(r.attempt), eventSeq: null, gateId: null } })}
                      onCompare={() => dispatch({ type: 'compareAttempt', attempt: b === r.attempt ? null : String(r.attempt) })} />
                  ))}
                </tbody>
              </table>
            </Panel>
            {comparison && a !== null && b !== null ? (
              <Panel title={`Compare attempt ${a} (A) with attempt ${b} (B)`}>
                <Comparison dims={comparison.dimensions} a={a} b={b} />
                {comparison.delta ? (
                  <div style={{ marginTop: 8 }} aria-label="Recorded retry delta">
                    <KeyValue items={[['Retry delta (record ' + comparison.delta.seq + ')', `attempt ${comparison.delta.previousAttempt ?? '?'} to ${comparison.delta.attempt ?? '?'}`], ['Information gain', comparison.delta.informationGain ?? 'not recorded'], ['Changed inputs', comparison.delta.changed.length ? comparison.delta.changed.join(', ') : 'none recorded'], ...(comparison.delta.reason ? [['Reason', comparison.delta.reason] as const] : [])]} />
                  </div>
                ) : <p className="muted small" style={{ marginBottom: 0 }}>No retry.delta record links these two attempts; the dimensions above come from each attempt's own records.</p>}
              </Panel>
            ) : <p className="muted">Select an attempt (A), then choose another with "Compare as B".</p>}
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const attemptsModule: ModuleDefinition = {
  id: 'attempts',
  placement: { page: 'runs', surface: 'center-tab', label: 'Attempts', order: 60 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'attempt', 'compareAttempt'],
  whenUnavailable: 'explain',
  render: Attempts,
};
