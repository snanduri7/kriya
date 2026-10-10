import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { overviewModule } from '.';

describe('Overview', () => {
  it('greenfield: no baseline is assumed', async () => {
    renderModule(overviewModule, { runId: 'fx-greenfield-success' });
    expect(await screen.findByText('not recorded (no baseline is assumed)')).toBeInTheDocument();
    expect(await screen.findByText('Run closed')).toBeInTheDocument();
  });
  it('brownfield: the recorded baseline revision, verbatim', async () => {
    renderModule(overviewModule, { runId: 'fx-brownfield-modify' });
    expect(await screen.findByText('a1b2c3d4e5f60718293a4b5c6d7e8f9012345678')).toBeInTheDocument();
  });
  it('an unfinished run is not claimed to be active', async () => {
    renderModule(overviewModule, { runId: 'fx-unsealed' });
    expect(await screen.findByText(/Unfinished \(the trace row says in_progress; this is not a claim that a run is active\)/)).toBeInTheDocument();
  });
  it('a pruned run still shows the run row and says the evidence is pruned', async () => {
    renderModule(overviewModule, { runId: 'fx-pruned' });
    expect(await screen.findByText('Pruned by retention')).toBeInTheDocument();
    expect(screen.getByText('An older run whose store retention removed')).toBeInTheDocument();
  });
});
