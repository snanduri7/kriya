import { fireEvent, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { settingsModule } from '.';

describe('Settings', () => {
  it('edits host settings through the provider and offers no content-visibility control (Kriya owns it; a host may only narrow)', async () => {
    const { provider } = renderModule(settingsModule);
    const input = await screen.findByLabelText('Configuration directory');
    fireEvent.change(input, { target: { value: '/other' } });
    fireEvent.click(input.parentElement!.querySelector('button')!);
    expect(await screen.findByText('Configuration directory saved.')).toBeInTheDocument();
    expect(await provider.getSetting('configDirectory')).toBe('/other');
    await waitFor(() => expect(screen.getByLabelText('Configuration directory')).toHaveValue('/other'));
    expect(screen.queryByLabelText('Workspace')).toBeNull(); // the workspace is chosen with Open Folder, never typed
    expect(screen.queryByText(/content visibility/i)).toBeNull();
  });
});
