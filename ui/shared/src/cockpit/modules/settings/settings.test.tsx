import { screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { renderModule } from '../../testing/harness';
import { settingsModule } from '.';

describe('Settings', () => {
  it('shows the host-owned values read-only, offers no way to edit the executable or configuration directory, and no content-visibility control (Kriya owns it)', async () => {
    const { provider } = renderModule(settingsModule);
    await screen.findByText('Settings');
    for (const label of ['Configuration directory', 'Kriya executable']) {
      expect(screen.queryByRole('textbox', { name: label })).toBeNull();
      expect(screen.getByLabelText(label)).toHaveTextContent(/Managed by the host/);
    }
    expect(screen.queryByRole('button', { name: 'Save' })).toBeNull();
    expect(screen.queryAllByRole('textbox')).toHaveLength(0); // no free-text field of any name in production Settings
    expect(screen.queryByLabelText('Workspace')).toBeNull(); // the workspace is chosen with Open Folder, never typed
    expect(screen.queryByText(/content visibility/i)).toBeNull();
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Editor' })).toBeInTheDocument()); // the harmless preference stays
    expect(await provider.getSetting('configDirectory')).toBeNull();
  });
  it('the module never calls setSetting for a host-owned key (source guard)', async () => {
    const calls: string[] = [];
    const { provider } = renderModule(settingsModule);
    const original = provider.setSetting.bind(provider);
    provider.setSetting = (async (key: never, value: never) => { calls.push(String(key)); return original(key, value); }) as typeof provider.setSetting;
    await screen.findByText('Settings');
    expect(calls).toEqual([]);
  });
});
