import type { ComponentType } from 'react';
import { AvailabilityView, Badge, KeyValue, Panel, effectiveAttempt, ofKind, payloadOf, useDispatch, useModuleSelection, useRunEvidence, type EvidenceRecord, type ModuleDefinition } from '../../core';

interface GateProps { gate: string; payload: Record<string, unknown> }

const outcome = (p: Record<string, unknown>) => (p.success === true ? 'passed' : p.success === false ? 'failed' : 'result not recorded');
const facts = (p: Record<string, unknown>, extra: ReadonlyArray<readonly [string, string]> = []) => (
  <KeyValue items={[['Outcome', outcome(p)], ['Exit code', p.exit_code == null ? 'not recorded' : String(p.exit_code)], ['Duration', typeof p.duration_seconds === 'number' ? `${p.duration_seconds}s` : 'not recorded'], ...(p.timed_out === true ? [['Timed out', 'yes'] as const] : []), ...extra]} />
);

/** Small gate renderers keyed by what the gate IS, not by language or build system. Anything unlisted gets the generic renderer. */
const Compile = ({ payload }: GateProps) => facts(payload, [['Kind', 'compile']]);
const Tests = ({ payload }: GateProps) => facts(payload, [['Kind', 'test'], ['Structured per-test results', payload.test_execution ? 'recorded' : 'not recorded for this gate']]);
const Runtime = ({ payload }: GateProps) => facts(payload, [['Kind', 'runtime verification']]);
const StaticAnalysis = ({ payload }: GateProps) => facts(payload, [['Kind', 'static analysis']]);
const Generic = ({ payload }: GateProps) => facts(payload);

export const GATE_RENDERERS: Readonly<Record<string, ComponentType<GateProps>>> = { compile: Compile, tests: Tests, runtime_verification: Runtime, static_analysis: StaticAnalysis };

function Validation() {
  const evidence = useRunEvidence();
  const dispatch = useDispatch();
  const attempt = useModuleSelection('attempt');
  const gateId = useModuleSelection('gateId');
  return (
    <AvailabilityView value={evidence}>
      {(data) => {
        const which = effectiveAttempt(data.records, attempt, ['gate.result']);
        const gates = ofKind(data.records, 'gate.result').filter((r: EvidenceRecord) => (r.attempt_number ?? null) === which || (which === null && r.attempt_number == null));
        if (!gates.length) return <div className="ck-state"><strong>No gate result recorded{which === null ? '' : ` for attempt ${which}`}</strong>Only persisted structured validation evidence is shown here.</div>;
        return (
          <div style={{ display: 'grid', gap: 8 }}>
            {gates.map((r) => {
              const p = payloadOf(r);
              const name = String(p.gate ?? 'gate');
              const id = `${r.attempt_number ?? '-'}:${name}`;
              const Render = GATE_RENDERERS[name] ?? Generic;
              return (
                <Panel key={r.seq} title={`${name}${which !== null ? ` · attempt ${which}` : ''}`}>
                  <button type="button" aria-pressed={gateId === id} onClick={() => dispatch({ type: 'selectGate', gateId: id })}>
                    <Badge tone={p.success === true ? 'ok' : p.success === false ? 'bad' : 'neutral'}>{outcome(p)}</Badge> select gate
                  </button>
                  <Render gate={name} payload={p} />
                </Panel>
              );
            })}
          </div>
        );
      }}
    </AvailabilityView>
  );
}

export const validationModule: ModuleDefinition = {
  id: 'validation',
  placement: { page: 'runs', surface: 'center-tab', label: 'Validation', order: 50 },
  requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: ['runId', 'attempt', 'gateId'],
  whenUnavailable: 'explain',
  render: Validation,
};
