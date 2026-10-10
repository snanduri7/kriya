import { screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { workspaceModule } from '.';

describe('Workspace', () => {
  it('reports the workspace recovery status; RUN_ACTIVE would be a workspace property, not a run row', async () => {
    renderModule(workspaceModule);
    expect(await screen.findByText('CLEAN')).toBeInTheDocument();
    expect(screen.getByText('/fixture/workspace')).toBeInTheDocument();
  });
});
