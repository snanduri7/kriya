import { screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { planModule } from '.';

describe('Plan: recorded structure only', () => {
  it('shows the recorded unit, plan identity, planned files, write scope, milestone, targets, requirements and plan events', async () => {
    renderModule(planModule, { runId: 'fx-greenfield-compile-retry', selection: { attempt: '1' } });
    const units = await screen.findByLabelText('Units');
    expect(within(units).getAllByText(/Unit /).length).toBeGreaterThan(0);
    expect(within(units).getByText(/plan-1/)).toBeInTheDocument();
    expect(within(units).getAllByText(/src\/Lib.java, src\/LibTest.java/).length).toBeGreaterThanOrEqual(1);
    expect(within(units).getByText(/restricted: src\/Lib.java/)).toBeInTheDocument();
    expect(within(units).getByText(/fx-milestone-1, position 1/)).toBeInTheDocument();
    const targets = await screen.findByRole('table', { name: 'Declared targets' });
    expect(within(targets).getByText('src/Lib.java')).toBeInTheDocument();
  });

  it('never invents what is not recorded: dependencies and immutable targets say so; a direct unit has no plan identity', async () => {
    renderModule(planModule, { runId: 'fx-greenfield-success', selection: { attempt: '1' } });
    await screen.findByLabelText('Units');
    expect(screen.getAllByText('Not recorded by this Kriya version')).toHaveLength(2);
    expect(screen.getByText(/no plan identity recorded/)).toBeInTheDocument();
    expect(screen.getByText(/nothing was planned ahead/)).toBeInTheDocument();
    expect(screen.queryByText(/confidence/i)).toBeNull();
  });

  it('a run with no evidence store is an honest unavailable state, not an empty plan', async () => {
    renderModule(planModule, { runId: 'fx-pre-recorder' });
    expect(await screen.findByText(/no evidence|not recorded|unavailable|predates/i)).toBeInTheDocument();
    expect(screen.queryByLabelText('Units')).toBeNull();
  });
});
