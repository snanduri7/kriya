import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { GATE_RENDERERS, validationModule } from '.';

describe('Validation: generic typed gate evidence with small renderers', () => {
  it('renderers are keyed by what a gate is, with no language or build-system screens', () => {
    expect(Object.keys(GATE_RENDERERS).sort()).toEqual(['compile', 'runtime_verification', 'static_analysis', 'tests']);
  });
  it('shows only recorded structured facts: outcome, exit code, duration; no per-test list is manufactured', async () => {
    renderModule(validationModule, { runId: 'fx-brownfield-regression' });
    expect(await screen.findByText(/tests · attempt 1/)).toBeInTheDocument();
    expect(screen.getAllByText('failed').length).toBeGreaterThan(0);
    expect(screen.getAllByText('not recorded for this gate').length).toBeGreaterThan(0);
    expect(screen.queryByText(/passed tests|per-test list/i)).toBeNull();
  });
  it('selecting a gate writes gateId as attempt:gate', async () => {
    const { store } = renderModule(validationModule, { runId: 'fx-greenfield-success' });
    (await screen.findAllByRole('button', { name: /select gate/ }))[0]!.click();
    await waitFor(() => expect(store.getState().gateId).toMatch(/^1:(compile|tests)$/));
  });
  it('an unavailable store is its own state', async () => {
    renderModule(validationModule, { runId: 'fx-pruned' });
    expect(await screen.findByText('Pruned by retention')).toBeInTheDocument();
  });
});
