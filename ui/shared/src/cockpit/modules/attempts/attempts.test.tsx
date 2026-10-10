import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createFixtureProvider } from '../../providers/fixture';
import type { EvidenceRecord, UIProvider } from '../../core';
import { renderModule } from '../../testing/harness';
import { attemptsModule } from '.';

describe('Attempts', () => {
  it('lists each attempt from its own records; selecting writes the shared attempt selection', async () => {
    const { store } = renderModule(attemptsModule, { runId: 'fx-greenfield-compile-retry' });
    const table = await screen.findByRole('table', { name: 'Attempts' });
    expect(within(table).getByRole('button', { name: 'Select attempt 1' })).toBeInTheDocument();
    expect(within(table).getByText('failed')).toBeInTheDocument();
    expect(within(table).getByText('succeeded')).toBeInTheDocument();
    fireEvent.click(within(table).getByRole('button', { name: 'Select attempt 2' }));
    expect(store.getState().attempt).toBe('2');
    expect(store.getState().compareAttempt).toBeNull();
  });

  it('compares A and B using the recorded retry.delta', async () => {
    const { store } = renderModule(attemptsModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' } });
    const table = await screen.findByRole('table', { name: 'Attempts' });
    fireEvent.click(within(table).getAllByRole('button', { name: 'Compare as B' })[1]!);
    expect(store.getState().compareAttempt).toBe('2');
    expect(await screen.findByRole('table', { name: 'Attempt comparison' })).toBeInTheDocument();
    const delta = screen.getByLabelText('Recorded retry delta');
    expect(within(delta).getByText(/PRESENT/)).toBeInTheDocument();
    expect(within(delta).getByText(/failure_signature, retry_evidence/)).toBeInTheDocument();
  });

  it('a dimension one side did not record is Not recorded and cannot compare, never "same"', async () => {
    const base = createFixtureProvider();
    const provider = Object.assign(Object.create(base), { evidenceRecords: async (rid: string, q: never, sig: never) => { const r = await base.evidenceRecords(rid, q, sig); return r.ok ? { ...r, data: { ...r.data, records: (r.data.records as EvidenceRecord[]).filter((x) => !(x.kind === 'prompt.sections' && x.attempt_number === 2)) } } : r; } }) as UIProvider;
    renderModule(attemptsModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1', compareAttempt: '2' }, provider });
    const cmp = await screen.findByRole('table', { name: 'Attempt comparison' });
    const row = cmp.querySelector('tr[data-dimension="prompt_sections"]') as HTMLElement;
    expect(within(row).getByText('Not recorded')).toBeInTheDocument();
    expect(within(row).getByText('cannot compare')).toBeInTheDocument();
    expect(within(row).queryByText('same')).toBeNull();
  });

  it('comparison works with only metadata (no content authorization is involved)', async () => {
    renderModule(attemptsModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1', compareAttempt: '2' }, fixture: { evidenceContent: false } });
    expect(await screen.findByRole('table', { name: 'Attempt comparison' })).toBeInTheDocument();
  });

  it('a one-attempt run offers no comparison and says how to start one', async () => {
    renderModule(attemptsModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' } });
    await screen.findByRole('table', { name: 'Attempts' });
    expect(screen.queryByRole('table', { name: 'Attempt comparison' })).toBeNull();
    expect(screen.getByText(/Select an attempt \(A\)/)).toBeInTheDocument();
  });
});
