import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { settingsModule } from '.';

describe('Settings', () => {
  it('edits host settings through the provider and offers no content-visibility control (Kriya owns it; a host may only narrow)', async () => {
    const { provider } = renderModule(settingsModule);
    const input = await screen.findByLabelText('Workspace');
    await waitFor(() => expect(input).toHaveValue('/fixture/workspace'));
    fireEvent.change(input, { target: { value: '/other' } });
    fireEvent.click(input.parentElement!.querySelector('button')!);
    expect(await screen.findByText('Workspace saved.')).toBeInTheDocument();
    expect(await provider.getSetting('workspacePath')).toBe('/other');
    expect(screen.queryByText(/content visibility/i)).toBeNull();
  });
});
