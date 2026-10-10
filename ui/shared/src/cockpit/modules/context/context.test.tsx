import { fireEvent, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { contextModule, contextPercent } from '.';

describe('Context', () => {
  it('shows recorded invocation facts, prompt sections and budget for the selected attempt', async () => {
    renderModule(contextModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' } });
    expect(await screen.findByText('Invocation')).toBeInTheDocument();
    expect(screen.getByText('Prompt sections')).toBeInTheDocument();
    expect(screen.getByText('Omissions and reasons')).toBeInTheDocument();
  });

  it('prompt text is fetched only on demand and rendered through the authorized call_content', async () => {
    renderModule(contextModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' }, fixture: { evidenceContent: true } });
    await screen.findByText('Prompt text');
    expect(screen.queryByText(/You are the developer/)).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /Show prompt messages/ }));
    expect(await screen.findByText(/You are the developer/)).toBeInTheDocument();
  });

  it('without content authorization the prompt text is denied with the reason, and no request for it is made', async () => {
    renderModule(contextModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' } });
    await screen.findByText('Prompt text');
    expect(screen.queryByRole('button', { name: /Show prompt messages/ })).toBeNull();
    expect(await screen.findByText(/content visibility policy does not authorize/)).toBeInTheDocument();
  });

  it('a digest-only run never shows prompt text and says why', async () => {
    renderModule(contextModule, { runId: 'fx-digest-only', selection: { attempt: '1' }, fixture: { evidenceContent: true } });
    await screen.findByText('Prompt text');
    expect(screen.queryByRole('button', { name: /Show prompt messages/ })).toBeNull();
    expect(screen.queryByText(/You are the developer/)).toBeNull();
    expect(screen.getByText(/kept a digest of the prompt, not the prompt text/)).toBeInTheDocument();
  });

  it('context percent is derived only when both the reported tokens and the served window are recorded', () => {
    expect(contextPercent({ promptTokensReported: 50, servedContextWindow: 200 } as never)?.percent).toBe(25);
    expect(contextPercent({ promptTokensReported: 50, servedContextWindow: null } as never)).toBeNull();
    expect(contextPercent({ promptTokensReported: null, servedContextWindow: 200 } as never)).toBeNull();
    expect(contextPercent({ promptTokensReported: 50, servedContextWindow: 0 } as never)).toBeNull();
  });
});
