import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFixtureProvider } from '../../providers/fixture';
import type { EvidenceRecord, UIProvider } from '../../core';
import { gateIdOf, renderModule } from '../../testing/harness';
import { GATE_RENDERERS, GenericGateView, validationModule } from '.';

const withRecords = (patch: (records: EvidenceRecord[]) => EvidenceRecord[]): UIProvider => {
  const base = createFixtureProvider();
  return Object.assign(Object.create(base), { evidenceRecords: async (id: string, q: never, s: never) => { const r = await base.evidenceRecords(id, q, s); return r.ok ? { ...r, data: { ...r.data, records: patch(r.data.records as EvidenceRecord[]) } } : r; } }) as UIProvider;
};
const gate = (seq: number, payload: Record<string, unknown>, attempt: number | null, kind = 'gate.result'): EvidenceRecord => ({ schema: 's', seq, prev: 'p', kind, run_id: 'r', unit_id: 'unit-1', attempt_number: attempt, provenance: 'OBSERVED', payload, content_digests: {}, blobs: {}, phase_provenance: null }) as unknown as EvidenceRecord;

describe('Validation renderers are keyed by gate type, in recorder vocabulary', () => {
  it('covers compile, test(s), targeted_test, regression_test, runtime verification and static analysis; everything else is generic', () => {
    expect(Object.keys(GATE_RENDERERS).sort()).toEqual(['compile', 'regression_test', 'run_verification', 'runtime_verification', 'static_analysis', 'targeted_test', 'test', 'tests']);
    expect(GATE_RENDERERS.operation_contract).toBeUndefined(); // a failure-type outcome: the generic view
    expect(GenericGateView).toBeTypeOf('function');
  });
});

describe('Validation over recorded gate evidence', () => {
  it('compile pass and test fail: summary counts, the first failed gate in recorded order, each gate with its exact recorded type', async () => {
    renderModule(validationModule, { runId: 'fx-brownfield-regression', selection: { attempt: '1' } });
    const gates = await screen.findByRole('list', { name: 'Gates' });
    expect(within(gates).getAllByRole('button')).toHaveLength(2);
    expect(screen.getByText('2 (1 passed, 1 failed)')).toBeInTheDocument();
    expect(screen.getByText('Tests (tests)')).toBeInTheDocument(); // the first failed gate: friendly label plus the recorded type
    expect(within(gates).getByText('tests')).toBeInTheDocument();
  });
  it('shows structured test results only when persisted; never manufactures a per-test list', async () => {
    const provider = withRecords((records) => [...records, gate(900, { gate: 'tests', success: false, exit_code: 1, test_execution: { status_counts: { passed: 3, failed: 1 }, completeness: 'complete' } }, 1)]);
    renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1', gateId: await gateIdOf('fx-greenfield-success', 'tests', provider, 1) }, provider });
    expect(await screen.findByText('Structured test results')).toBeInTheDocument();
    expect(screen.getAllByText('recorded').length).toBeGreaterThan(0);
    expect(screen.getByText('{"passed":3,"failed":1}')).toBeInTheDocument();
    expect(screen.queryByText(/per-test|pass list/i)).toBeNull();
  });
  it('a test gate without a persisted summary says so', async () => {
    renderModule(validationModule, { runId: 'fx-brownfield-regression', selection: { attempt: '1', gateId: await gateIdOf('fx-brownfield-regression', 'tests') } });
    expect(await screen.findByText('not recorded for this gate')).toBeInTheDocument();
  });
  it('regression and targeted gates are rendered when recorded; an unknown gate type gets the generic view, shown as recorded', async () => {
    const provider = withRecords(() => [gate(1, { attempt: 1, index: 0, success: true, type: 'targeted_test' }, 1, 'mirror.gate_outcome'), gate(2, { attempt: 1, index: 1, success: false, type: 'regression_test' }, 1, 'mirror.gate_outcome'), gate(3, { gate: 'future_validator_v9', success: true, novel: 'x' }, 1)]);
    renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1', gateId: await gateIdOf('fx-greenfield-success', 'future_validator_v9', provider) }, provider });
    const gates = await screen.findByRole('list', { name: 'Gates' });
    expect(within(gates).getByText('Targeted tests')).toBeInTheDocument();
    expect(within(gates).getByText('Regression tests')).toBeInTheDocument();
    expect(within(gates).getAllByText('future_validator_v9').length).toBeGreaterThan(0);
    expect(screen.getByText('Recorded type')).toBeInTheDocument();
    expect(within(gates).getAllByText(/mirrored from the run record/)).toHaveLength(2);
  });
  it('incomplete optional fields are not invented: no exit code, no duration', async () => {
    const provider = withRecords(() => [gate(1, { gate: 'compile', success: true }, 1)]);
    renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1', gateId: await gateIdOf('fx-greenfield-success', 'compile', provider) }, provider });
    await screen.findByText('Recorded type');
    expect(screen.queryByText('Exit code')).toBeNull();
    expect(screen.queryByText('Duration')).toBeNull();
  });
  it('a gate with no recorded result is shown as such, never as passed', async () => {
    const provider = withRecords(() => [gate(1, { gate: 'compile' }, 1)]);
    renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' }, provider });
    expect((await screen.findAllByText('result not recorded')).length).toBeGreaterThan(0);
    expect(screen.getByText('1 (0 passed, 0 failed, 1 without a recorded result)')).toBeInTheDocument();
  });
  it('follows the selected attempt: attempt 1 failed, attempt 2 passed', async () => {
    const { store } = renderModule(validationModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' } });
    expect(await screen.findByText(/Validation summary · attempt 1/)).toBeInTheDocument();
    expect(screen.getByText('Compile (compile)')).toBeInTheDocument();
    store.dispatch({ type: 'selectTimelineNode', node: { unitId: 'unit-1', attempt: '2', eventSeq: null, gateId: null } });
    await waitFor(() => expect(screen.getByText(/Validation summary · attempt 2/)).toBeInTheDocument());
    expect(screen.getByText('none recorded')).toBeInTheDocument();
  });
  it('selecting a gate writes selectedGate only: no tab change, no drawer', async () => {
    const { store } = renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1', centralTab: 'validation' } });
    const gates = await screen.findByRole('list', { name: 'Gates' });
    fireEvent.click(within(gates).getAllByRole('button')[1]!);
    expect(store.getState()).toMatchObject({ gateId: expect.stringMatching(/^1:tests@\d+$/), centralTab: 'validation', drawerOpen: false });
    expect(await screen.findByText('Recorded type')).toBeInTheDocument();
  });
  it('a gate selected elsewhere (the timeline) is the one shown in detail', async () => {
    renderModule(validationModule, { runId: 'fx-greenfield-success', selection: { attempt: '1', gateId: await gateIdOf('fx-greenfield-success', 'compile') } });
    expect(await screen.findByText(/Compile · detail/)).toBeInTheDocument();
  });
  it('a run with no gate evidence says so (the generic "empty" is never used)', async () => {
    renderModule(validationModule, { runId: 'fx-greenfield-success', provider: withRecords((records) => records.filter((r) => r.kind !== 'gate.result')) });
    expect(await screen.findByText('No gate evidence recorded')).toBeInTheDocument();
  });
  it('unavailable evidence is its own state', async () => {
    renderModule(validationModule, { runId: 'fx-pruned' });
    expect(await screen.findByText('Evidence pruned by retention')).toBeInTheDocument();
  });
  it('mirrored gate outcomes carry their origin: they are the run record\'s outcomes, not validator output', async () => {
    renderModule(validationModule, { runId: 'fx-brownfield-modify', selection: { attempt: '1' } });
    const gates = await screen.findByRole('list', { name: 'Gates' });
    expect(within(gates).getByText(/mirrored from the run record/)).toBeInTheDocument();
  });
});
