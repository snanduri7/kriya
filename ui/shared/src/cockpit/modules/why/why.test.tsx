import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFixtureProvider } from '../../providers/fixture';
import type { EvidenceRecord, UIProvider } from '../../core';
import { renderModule } from '../../testing/harness';
import { whyModule } from '.';

const withRecords = (patch: (records: EvidenceRecord[]) => EvidenceRecord[], id = 'ev-fx-greenfield-compile-retry'): UIProvider => {
  const base = createFixtureProvider();
  return Object.assign(Object.create(base), { evidenceRecords: async (rid: string, q: never, s: never) => { const r = await base.evidenceRecords(rid, q, s); return r.ok && rid === id ? { ...r, data: { ...r.data, records: patch(r.data.records as EvidenceRecord[]) } } : r; } }) as UIProvider;
};
const why = async () => within(await screen.findByLabelText('Why'));

describe('WHY: only what Kriya recorded, in the frozen structure', () => {
  it('a failed greenfield attempt: what happened, first failed gate, recorded diagnosis with its class, recovery and fallback', async () => {
    renderModule(whyModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' } });
    const w = await why();
    expect(await w.findByText(/What happened · attempt 1/)).toBeInTheDocument();
    expect(w.getByText(/Attempt 1 failed/)).toBeInTheDocument();
    expect(w.getByText('First failed gate, in recorded order')).toBeInTheDocument();
    expect(w.getAllByText('compile', { selector: '.mono' }).length).toBeGreaterThan(0);
    expect(w.getByText('Recorded diagnosis')).toBeInTheDocument();
    expect(w.getAllByText('DERIVED_DETERMINISTIC', { selector: '.ck-badge' }).length).toBeGreaterThanOrEqual(2);
    for (const recorded of ['COMPILE_FAILED', 'SOURCE_DEFECT', 'locator', 'high', 'src/Lib.java']) expect(w.getAllByText(recorded, { exact: false }).length).toBeGreaterThan(0);
    expect(w.getByText(/Recovery decision/)).toBeInTheDocument();
    expect(w.getByText(/Recorded reason: The compile failure names a symbol/)).toBeInTheDocument();
    expect(w.getByText('Unknowns and evidence gaps')).toBeInTheDocument();
    expect(w.getByText('The failure message text is kept as content and is not shown here.')).toBeInTheDocument();
  });

  it('a failed brownfield regression uses the same component: recorded MODEL_CLAIMED stays MODEL_CLAIMED', async () => {
    renderModule(whyModule, { runId: 'fx-brownfield-regression', selection: { attempt: '1' } });
    const w = await why();
    expect(await w.findByText('Recorded diagnosis')).toBeInTheDocument();
    expect(w.getAllByText('MODEL_CLAIMED', { selector: '.ck-badge' }).length).toBeGreaterThanOrEqual(2);
    expect(w.queryByText('DERIVED_DETERMINISTIC')).toBeNull();
    expect(w.getByText(/action stop_environment|stop_environment/)).toBeInTheDocument();
    expect(w.getByText(/REGRESSION_UNATTRIBUTED: the regression cannot be attributed/)).toBeInTheDocument();
  });

  it('no diagnosis recorded: says so in the frozen words and fabricates nothing', async () => {
    renderModule(whyModule, { runId: 'fx-brownfield-regression', selection: { attempt: '1' }, provider: withRecords((records) => records.filter((r) => r.kind !== 'diagnosis'), 'ev-fx-brownfield-regression') });
    const w = await why();
    expect(await w.findByText('Kriya recorded the failure evidence below, but no causal diagnosis was recorded.')).toBeInTheDocument();
    expect(w.queryByText('Recorded diagnosis')).toBeNull();
    expect(w.getByText('No causal diagnosis was recorded for this attempt.')).toBeInTheDocument();
    expect(w.getByText('First failed gate, in recorded order')).toBeInTheDocument();
  });

  it('a diagnosis without an evidence class says no class was recorded (the record\'s provenance is not promoted to a class)', async () => {
    const provider = withRecords((records) => records.map((r) => (r.kind === 'diagnosis' ? { ...r, payload: { ...(r.payload as Record<string, unknown>), evidence_class: undefined } } : r) as EvidenceRecord));
    renderModule(whyModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' }, provider });
    const w = await why();
    expect((await w.findAllByText('no evidence class recorded')).length).toBeGreaterThanOrEqual(2);
    expect(w.queryByText('MEASURED')).toBeNull();
  });

  it('a successful attempt has no failure to explain', async () => {
    renderModule(whyModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '2' } });
    const w = await why();
    expect(await w.findByText(/Attempt 2 succeeded/)).toBeInTheDocument();
    expect(w.getByText('No failure is recorded for this attempt.')).toBeInTheDocument();
    expect(w.queryByText('Recorded diagnosis')).toBeNull();
  });

  it('with no attempt selected it explains the last failed attempt', async () => {
    renderModule(whyModule, { runId: 'fx-greenfield-compile-retry' });
    expect(await (await why()).findByText(/What happened · attempt 1/)).toBeInTheDocument();
  });

  it('evidence gaps follow the evidence: unsealed and digest-only runs say what is missing', async () => {
    renderModule(whyModule, { runId: 'fx-unsealed' });
    expect((await (await why()).findAllByText(/What happened/)).length).toBeGreaterThan(0);
    const gap = withRecords((records) => [...records, { ...records[0], seq: 99, kind: 'diagnosis', attempt_number: 1, payload: { type: 'x', reason_code: 'Y' }, blobs: {} } as EvidenceRecord], 'ev-fx-unsealed');
    const { unmount } = renderModule(whyModule, { runId: 'fx-unsealed', selection: { attempt: '1' }, provider: gap });
    expect(await screen.findByText('The store is unsealed, so records after the last written one may be missing.')).toBeInTheDocument();
    unmount();
    renderModule(whyModule, { runId: 'fx-digest-only', provider: withRecords((records) => [...records.filter((r) => r.kind !== 'attempt.concluded'), { ...records[0], seq: 98, kind: 'attempt.concluded', attempt_number: 1, payload: { succeeded: false } } as EvidenceRecord], 'ev-fx-digest-only'), selection: { attempt: '1' } });
    expect(await screen.findByText('This run kept digests only: message and output text were not retained.')).toBeInTheDocument();
  });

  it('the explicit actions point at the evidence without importing another module', async () => {
    const { store } = renderModule(whyModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' } });
    const w = await why();
    fireEvent.click(await w.findByRole('button', { name: 'Open in Validation' }));
    expect(store.getState()).toMatchObject({ centralTab: 'validation', gateId: expect.stringMatching(/^1:compile@\d+$/) });
    fireEvent.click(w.getByRole('button', { name: 'Open in Changes' }));
    expect(store.getState().centralTab).toBe('changes');
    fireEvent.click(w.getByRole('button', { name: 'Open evidence' }));
    expect(store.getState().centralTab).toBe('evidence');
  });

  it('never uses cohort vocabulary in a normal run WHY', async () => {
    for (const id of ['fx-greenfield-compile-retry', 'fx-brownfield-regression']) {
      const { unmount } = renderModule(whyModule, { runId: id, selection: { attempt: '1' } });
      const w = await why();
      await w.findByText(/What happened/);
      expect(w.queryByText(/GENUINE|SAFE_FAILURE|FALSE_NEGATIVE|FALSE_SUCCESS/)).toBeNull();
      unmount();
    }
  });

  it('unavailable evidence is its own state', async () => {
    renderModule(whyModule, { runId: 'fx-pre-recorder' });
    expect(await screen.findByText('Recorded before the recorder')).toBeInTheDocument();
  });
});
