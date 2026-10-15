import type { ComponentType } from 'react';
import { AvailabilityView, Badge, KeyValue, Panel, TrustList, VerificationLines, overallRequestLine, attemptFor, changeFacts, firstFailedGate, gateRows, hasData, useDispatch, useModuleSelection, useRunContext, useRunDetail, useRunEvidence, userEvidence, validationFacts, verificationGroups, type GateRow, type ModuleDefinition } from '../../core';

/** Explicit DISPLAY mapping only: the recorded type is always shown beside the friendly label, and an unmapped type is shown as recorded. */
const LABELS: Readonly<Record<string, string>> = {
  compile: 'Compile', test: 'Tests', tests: 'Tests', targeted_test: 'Targeted tests', regression_test: 'Regression tests',
  run_verification: 'Runtime verification', runtime_verification: 'Runtime verification', static_analysis: 'Static analysis',
};

const outcome = (g: GateRow) => (g.success === true ? 'passed' : g.success === false ? 'failed' : 'result not recorded');
const origin = (g: GateRow) => (g.origin === 'mirrored' ? 'mirrored from the run record' : g.origin === 'terminal' ? 'terminal gate' : 'validator gate');

function baseFacts(g: GateRow): Array<readonly [string, string]> {
  const facts: Array<readonly [string, string]> = [['Recorded type', g.type], ['Outcome', outcome(g)], ['Recorded as', origin(g)]];
  if (g.exitCode !== null) facts.push(['Exit code', String(g.exitCode)]);
  if (g.durationSeconds !== null) facts.push(['Duration', `${g.durationSeconds}s`]);
  if (g.timedOut === true) facts.push(['Timed out', 'yes']);
  return facts;
}

/** Structured test facts are shown only when the recorder persisted a summary; nothing is parsed out of console text. */
function TestFacts({ gate }: { gate: GateRow }) {
  const summary = gate.testSummary;
  const entries = summary ? Object.entries(summary).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)] as const) : [];
  return <KeyValue items={[...baseFacts(gate), ['Structured test results', summary ? 'recorded' : 'not recorded for this gate'], ...entries]} />;
}

export const GenericGateView = ({ gate }: { gate: GateRow }) => <KeyValue items={baseFacts(gate)} />;
const CompileView = ({ gate }: { gate: GateRow }) => <KeyValue items={baseFacts(gate)} />;

/** Small renderers keyed by what a gate IS (recorder vocabulary), never by language or build system. Anything else gets the generic view. */
export const GATE_RENDERERS: Readonly<Record<string, ComponentType<{ gate: GateRow }>>> = {
  compile: CompileView, test: TestFacts, tests: TestFacts, targeted_test: TestFacts, regression_test: TestFacts,
  run_verification: GenericGateView, runtime_verification: GenericGateView, static_analysis: GenericGateView,
};

function GateList({ gates, selected, onSelect }: { gates: readonly GateRow[]; selected: string | null; onSelect(g: GateRow): void }) {
  return (
    <ul className="ck-list" aria-label="Gates">
      {gates.map((g) => (
        <li key={g.seq}>
          <button type="button" aria-selected={g.id === selected} onClick={() => onSelect(g)}>
            <Badge tone={g.success === true ? 'ok' : g.success === false ? 'bad' : 'neutral'}>{outcome(g)}</Badge> {LABELS[g.type] ?? g.type} <span className="muted mono">{g.type}</span>
            <div className="muted small">{origin(g)}{g.index !== null ? ` · #${g.index}` : ''}</div>
          </button>
        </li>
      ))}
    </ul>
  );
}

/** The user layer: how Kriya knows this works (build, tests, requested behavior, safety), and why the result can be trusted. Every statement is a recorded fact; the exact check names are one disclosure down. */
function UserLayer() {
  const evidence = useRunEvidence();
  const run = useRunContext();
  const detail = useRunDetail();
  const dispatch = useDispatch();
  const ctx = hasData(run) ? run.data : null;
  if (!hasData(evidence)) return <p className="muted" role="status">The verification results come from this run&apos;s evidence, which is not readable here.</p>;
  const gates = validationFacts(evidence.data.records);
  const { groups } = verificationGroups(gates);
  const events = hasData(detail) ? detail.data.run_events.data ?? [] : [];
  const decision = events.find((e) => e.kind === 'approval.decision');
  const outcome = decision && decision.details && typeof decision.details === 'object' ? (decision.details as Record<string, unknown>).outcome : null;
  const trust = userEvidence({ displayGoal: ctx?.displayGoal ?? null, filesChanged: changeFacts(evidence.data.records).length, gates, approval: outcome === 'approved' ? 'approved' : outcome === 'rejected' ? 'rejected' : null,
    evidence: { status: evidence.data.verify?.status ?? null, sealed: evidence.data.verify?.sealed ?? null } });
  return (
    <div className="ck-verification" style={{ display: 'grid', gap: 8 }}>
      <section aria-label="How Kriya knows this works">
        <h3>How Kriya knows this works</h3>
        {groups.length === 0 ? <p className="muted">No verification result is recorded for this run.</p> : groups.map((g) => (
          <div key={g.id}><div className="muted small ck-group-title">{g.title}</div><VerificationLines lines={g.lines} label={g.title} /></div>
        ))}
        {groups.length > 0 && overallRequestLine(gates) ? <VerificationLines lines={[overallRequestLine(gates)!]} label="Overall request" /> : null}
      </section>
      <section aria-label="Why you can trust this result">
        <h3>Why you can trust this result</h3>
        <TrustList items={trust} />
        <button type="button" className="ck-link" onClick={() => dispatch({ type: 'centralTab', tab: 'evidence' })}>View supporting evidence</button>
      </section>
    </div>
  );
}

function Validation() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const attempt = useModuleSelection('attempt');
  const gateId = useModuleSelection('gateId');
  return (
    <div style={{ display: 'grid', gap: 8 }}>
      <UserLayer />
      <details className="ck-tech" data-testid="technical-verification">
        <summary>Technical verification</summary>
        <TechnicalChecks evidence={evidence} dispatch={dispatch} attempt={attempt} gateId={gateId} />
      </details>
    </div>
  );
}

function TechnicalChecks({ evidence, dispatch, attempt, gateId }: { evidence: ReturnType<typeof useRunEvidence>; dispatch: ReturnType<typeof useDispatch>; attempt: string | null; gateId: string | null }) {
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const all = gateRows(data.records);
        if (!all.length) return <div className="ck-state"><strong>No gate evidence recorded</strong>The store holds no gate.result or mirrored gate outcome for this run. Only persisted structured gate evidence is shown here.</div>;
        const which = attemptFor(data.records, attempt, ['gate.result', 'mirror.gate_outcome']);
        const scoped = all.filter((g) => g.attempt === which);
        const runLevel = all.filter((g) => g.attempt === null);
        const visible = [...scoped, ...runLevel];
        const failed = firstFailedGate(visible);
        const selected = visible.find((g) => g.id === gateId) ?? null;
        const attemptsWithGates = [...new Set(all.map((g) => g.attempt).filter((n): n is number => n !== null))];
        const counts = { passed: visible.filter((g) => g.success === true).length, failed: visible.filter((g) => g.success === false).length, unknown: visible.filter((g) => g.success === null).length };
        const select = (g: GateRow) => dispatch({ type: 'selectGate', gateId: g.id });
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            <Panel title={which === null ? 'Validation summary' : `Validation summary · attempt ${which}`}>
              <KeyValue items={[
                ['Gates recorded', `${visible.length} (${counts.passed} passed, ${counts.failed} failed${counts.unknown ? `, ${counts.unknown} without a recorded result` : ''})`],
                ['First failed gate, in recorded order', failed ? `${LABELS[failed.type] ?? failed.type} (${failed.type})` : 'none recorded'],
                ['Attempts with gate evidence', attemptsWithGates.length ? attemptsWithGates.join(', ') : 'none (run-level gates only)'],
              ]} />
            </Panel>
            {scoped.length ? <Panel title="Gates"><GateList gates={scoped} selected={gateId} onSelect={select} /></Panel> : <div className="ck-state"><strong>No gate recorded for attempt {which ?? '-'}</strong>Other attempts or run-level gates are listed below if recorded.</div>}
            {runLevel.length ? <Panel title="Run-level gates"><GateList gates={runLevel} selected={gateId} onSelect={select} /></Panel> : null}
            {selected ? (() => { const Render = GATE_RENDERERS[selected.type] ?? GenericGateView; return <Panel title={`${LABELS[selected.type] ?? selected.type} · detail`}><Render gate={selected} />{selected.outputKept ? <p className="muted small">The recorder kept this gate's output as content; content is shown only through Kriya's authorized content API, which covers candidate changes in this build.</p> : null}</Panel>; })() : <p className="muted">Select a gate for its recorded detail.</p>}
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const validationModule: ModuleDefinition = {
  id: 'validation',
  placement: { page: 'runs', surface: 'center-tab', label: 'Verification', order: 50 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'attempt', 'gateId'],
  whenUnavailable: 'explain',
  render: Validation,
};
