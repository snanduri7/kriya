import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { trustModule } from '.';

describe('Trust strip', () => {
  it('shows only recorded facts, each as recorded', async () => {
    renderModule(trustModule, { runId: 'fx-brownfield-modify' });
    const strip = await screen.findByLabelText('Trust strip');
    expect(await screen.findByText('capture: full')).toBeInTheDocument();
    for (const text of ['sealed', 'verification: VERIFIED', 'consistency: sealed_store', 'provider: fixture']) expect(strip).toHaveTextContent(text);
    expect(strip.textContent).not.toMatch(/score|%|safe|trusted/i); // no synthetic trust score
  });
  it('an unsealed store is shown unsealed with live consistency, and carries the limits notice', async () => {
    renderModule(trustModule, { runId: 'fx-unsealed' });
    expect(await screen.findByText('unsealed')).toBeInTheDocument();
    expect(screen.getByText('consistency: live_observation')).toBeInTheDocument();
    expect(screen.getByText('limits apply')).toBeInTheDocument();
  });
  it('a pruned store says why, not "no data"', async () => {
    renderModule(trustModule, { runId: 'fx-pruned' });
    expect(await screen.findByText('Pruned by retention')).toBeInTheDocument();
  });
});
