import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SettingsPanel } from '../src/panels/SettingsPanel';
import { App } from '../src/App';
import { FakeHost, env, withSnapshots } from './fakeHost';

describe('settings via the host boundary', () => {
  it('saves configuration independently of workspace and never acquires', async () => {
    const host = new FakeHost(() => null);
    const saved = vi.fn();
    render(<SettingsPanel host={host} onSaved={saved} onClose={() => {}} />);
    await screen.findByLabelText('Configuration directory');
    fireEvent.change(screen.getByLabelText('Configuration directory'), { target: { value: '/fixture/config' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    await screen.findByText('Saved. No snapshot was acquired.');
    expect(host.settings.configDirectory).toBe('/fixture/config');
    expect(host.settings.workspacePath).toBe('/fixture/workspace');
    expect(saved).toHaveBeenCalledWith('configDirectory');
    expect(host.calls).toEqual([]);
  });

  it('refuses a relative path; blank explicitly restores the home default', async () => {
    const host = new FakeHost(() => null);
    host.settings.configDirectory = '/fixture/config';
    render(<SettingsPanel host={host} onSaved={() => {}} onClose={() => {}} />);
    await screen.findByLabelText('Configuration directory');
    fireEvent.change(screen.getByLabelText('Configuration directory'), { target: { value: 'relative' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    expect(screen.getByRole('alert')).toHaveTextContent('absolute path');
    expect(host.settings.configDirectory).toBe('/fixture/config');
    fireEvent.change(screen.getByLabelText('Configuration directory'), { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    await screen.findByText('Saved. No snapshot was acquired.');
    expect(host.settings.configDirectory).toBeNull();
  });

  it('reports persistence failure without declaring the session changed', async () => {
    const host = new FakeHost(() => null);
    host.setSetting = async () => { throw new Error('settings write refused'); };
    const saved = vi.fn();
    render(<SettingsPanel host={host} onSaved={saved} onClose={() => {}} />);
    await screen.findByLabelText('Preferred editor');
    fireEvent.click(screen.getByRole('button', { name: 'Save preferred editor' }));
    await screen.findByText('settings write refused');
    expect(saved).not.toHaveBeenCalled();
  });

  it('clears the pin after a configuration change without acquiring a replacement', async () => {
    const host = new FakeHost(withSnapshots((r) => {
      if (r.operation === 'capabilities') return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
      if (r.operation === 'history.list') return env('history.list', { runs: [], next_cursor: null });
      return env(r.operation, {});
    }));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh displayed snapshot' })).toBeEnabled());
    await waitFor(() => expect(screen.getByRole('button', { name: 'Settings' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    await screen.findByLabelText('Configuration directory');
    fireEvent.change(screen.getByLabelText('Configuration directory'), { target: { value: '/fixture/other' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save configuration directory' }));
    await screen.findByText('Saved. No snapshot was acquired.');
    fireEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh displayed snapshot' })).toBeDisabled());
    expect(host.calls.filter((c) => c.operation === 'snapshot.acquire')).toHaveLength(1);
  });
});
