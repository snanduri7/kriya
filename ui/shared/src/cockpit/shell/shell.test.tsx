import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { createCockpitRegistry } from '../composition';
import type { ModuleDefinition } from '../core';
import { renderShell } from '../testing/harness';
import { INSPECTOR_COLLAPSE_AT } from './AppShell';

const GF = 'fx-greenfield-compile-retry';
const body = () => document.querySelector('.ck-body') as HTMLElement;
const ready = async () => { await screen.findByRole('navigation', { name: 'Destinations' }); await screen.findByText(/Greenfield: |Create a utility library/, undefined, { timeout: 3000 }).catch(() => undefined); };

describe('AppShell layout and navigation', () => {
  it('shows the Runs / Workspace / Settings destinations once capabilities load, and switches with the rail and Ctrl+number', async () => {
    renderShell();
    const rail = await screen.findByRole('navigation', { name: 'Destinations' });
    await waitFor(() => expect(within(rail).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Runs', 'Workspace', 'Settings']));
    expect(within(rail).getByRole('button', { name: 'Runs' })).toHaveAttribute('aria-current', 'page');
    fireEvent.click(within(rail).getByRole('button', { name: 'Workspace' }));
    expect(await screen.findByRole('heading', { name: 'Workspace' })).toBeInTheDocument();
    fireEvent.keyDown(rail, { key: '3', ctrlKey: true });
    expect(await screen.findByRole('heading', { name: 'Settings' })).toBeInTheDocument();
    fireEvent.keyDown(rail, { key: '1', metaKey: true });
    expect(await screen.findByRole('heading', { name: 'Runs' })).toBeInTheDocument();
  });

  it('hides destinations whose capabilities are absent: no rail slot, no "coming soon"', async () => {
    renderShell({ registry: createCockpitRegistry({ disabled: ['workspace'] }) });
    const rail = await screen.findByRole('navigation', { name: 'Destinations' });
    await waitFor(() => expect(within(rail).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Runs', 'Settings']));
  });

  it('the inspector is collapsed by definition at <=1180px and open above it; the user can override both ways', async () => {
    const small = renderShell({ width: INSPECTOR_COLLAPSE_AT });
    await screen.findByRole('navigation', { name: 'Destinations' });
    await waitFor(() => expect(body().className).toContain('no-inspector'));
    expect(screen.queryByRole('complementary', { name: 'Execution inspector' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Inspector' }));
    expect(await screen.findByRole('complementary', { name: 'Execution inspector' })).toBeInTheDocument();
    expect(small.store.getState().runId).toBeNull();
  });

  it('wide: inspector open; the toggles collapse and restore the secondary pane and the inspector', async () => {
    renderShell({ width: 1440 });
    await screen.findByRole('complementary', { name: 'Execution inspector' });
    await screen.findByRole('complementary', { name: 'Secondary pane' });
    fireEvent.click(screen.getByRole('button', { name: 'Inspector' }));
    expect(screen.queryByRole('complementary', { name: 'Execution inspector' })).toBeNull();
    expect(body().className).toContain('no-inspector');
    fireEvent.click(screen.getByRole('button', { name: 'Runs pane' }));
    expect(screen.queryByRole('complementary', { name: 'Secondary pane' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Runs pane' }));
    expect(await screen.findByRole('complementary', { name: 'Secondary pane' })).toBeInTheDocument();
  });

  it('panes resize by keyboard within their designed range', async () => {
    renderShell();
    const handle = await screen.findByRole('separator', { name: 'Resize runs pane' });
    expect(handle).toHaveAttribute('aria-valuenow', '280');
    fireEvent.keyDown(handle, { key: 'ArrowRight', shiftKey: true });
    expect(handle).toHaveAttribute('aria-valuenow', '320');
    fireEvent.keyDown(handle, { key: 'End' });
    expect(handle).toHaveAttribute('aria-valuenow', '360');
    fireEvent.keyDown(handle, { key: 'ArrowRight' });
    expect(handle).toHaveAttribute('aria-valuenow', '360');
    fireEvent.keyDown(handle, { key: 'Home' });
    expect(handle).toHaveAttribute('aria-valuenow', '240');
    expect(body().style.getPropertyValue('--ck-secondary')).toBe('240px');
    const inspector = await screen.findByRole('separator', { name: 'Resize inspector' });
    fireEvent.keyDown(inspector, { key: 'ArrowLeft', shiftKey: true });
    expect(inspector).toHaveAttribute('aria-valuenow', '380');
  });

  it('a fixture provider is announced so fixture data can never pass for evidence', async () => {
    renderShell();
    expect(await screen.findByText(/FIXTURE DATA - not Kriya evidence/)).toBeInTheDocument();
  });
});

describe('AppShell mounts modules through the registry and the shared selection', () => {
  it('selecting a run, a timeline node, a tab and the drawer all go through the SelectionStore', async () => {
    const { store } = renderShell();
    await ready();
    fireEvent.click(await screen.findByRole('button', { name: /Create a utility library/ }));
    await waitFor(() => expect(store.getState().runId).toBe(GF));
    const tabs = await screen.findByRole('tablist', { name: 'Run documents' });
    expect(within(tabs).getAllByRole('tab').map((t) => t.textContent)).toEqual(['Overview', 'Context', 'Plan', 'Changes', 'Validation', 'Attempts', 'Evidence']);
    fireEvent.click(within(tabs).getByRole('tab', { name: 'Validation' }));
    expect(store.getState().centralTab).toBe('validation');
    fireEvent.click(screen.getByRole('button', { name: 'Expand' }));
    expect(store.getState().drawerOpen).toBe(true);
    fireEvent.click(screen.getByRole('tab', { name: 'Raw Events' }));
    expect(store.getState()).toMatchObject({ drawerOpen: true, bottomTab: 'drawer-raw-events' });
  });

  it('timeline selection drives the same attempt / gate / file context that Changes, Validation and the drawer consume', async () => {
    const { store } = renderShell({ runId: GF });
    const timeline = await screen.findByRole('tree', { name: 'Execution timeline' });
    // attempt 1 (compile failed)
    fireEvent.click(await within(timeline).findByRole('button', { name: 'Attempt 1' }));
    expect(store.getState()).toMatchObject({ unitId: 'unit-1', attempt: '1', gateId: null });
    fireEvent.click(screen.getByRole('tab', { name: 'Validation' }));
    expect(await screen.findByText(/compile · attempt 1/)).toBeInTheDocument();
    expect(screen.getByText('failed', { selector: '.ck-badge' })).toBeInTheDocument();
    // attempt 2 (compile and tests passed): Validation follows without any module-to-module call
    fireEvent.click(within(timeline).getByRole('button', { name: 'Attempt 2' }));
    expect(await screen.findByText(/compile · attempt 2/)).toBeInTheDocument();
    expect(screen.queryByText(/compile · attempt 1/)).toBeNull();
    // a gate node selects the gate and the event; the drawer's raw events narrow to that record
    fireEvent.click(within(timeline).getByRole('button', { name: /tests passed/ }));
    expect(store.getState()).toMatchObject({ attempt: '2', gateId: '2:tests' });
    expect(store.getState().eventSeq).not.toBeNull();
    fireEvent.click(screen.getByRole('tab', { name: 'Changes' }));
    expect(await screen.findByText(/Changes · attempt 2/)).toBeInTheDocument();
    fireEvent.click(within(await screen.findByRole('list', { name: 'Changed files' })).getByRole('button', { name: /src\/Lib\.java/ }));
    expect(store.getState().filePath).toBe('src/Lib.java');
    expect(store.getState().drawerOpen).toBe(false); // a file selection never opens the drawer
  });

  it('a module can be removed or added without touching AppShell: only the registry input changes', async () => {
    renderShell({ runId: GF, registry: createCockpitRegistry({ disabled: ['validation', 'timeline'] }) });
    const tabs = await screen.findByRole('tablist', { name: 'Run documents' });
    expect(within(tabs).queryByRole('tab', { name: 'Validation' })).toBeNull();
    expect(within(tabs).getByRole('tab', { name: 'Changes' })).toBeInTheDocument();
    expect(screen.queryByRole('tree', { name: 'Execution timeline' })).toBeNull();
  });

  it('an extra module appears in its slot with no shell change, and a failing module cannot take the shell down', async () => {
    const Extra = () => <p>extra module body</p>;
    const Boom = () => { throw new Error('boom'); };
    const extra: ModuleDefinition = { id: 'extra', placement: { page: 'runs', surface: 'center-tab', label: 'Extra', order: 5 }, requiredCapabilities: ['evidence'], optionalCapabilities: [], selectionDependencies: [], render: Extra };
    const boom: ModuleDefinition = { id: 'boom', placement: { page: 'runs', surface: 'center-tab', label: 'Boom', order: 6 }, requiredCapabilities: [], optionalCapabilities: [], selectionDependencies: [], render: Boom };
    const { store } = renderShell({ runId: GF, registry: createCockpitRegistry({ extra: [extra, boom] }) });
    const spy = console.error; console.error = () => undefined;
    try {
      const tabs = await screen.findByRole('tablist', { name: 'Run documents' });
      fireEvent.click(within(tabs).getByRole('tab', { name: 'Extra' }));
      expect(await screen.findByText('extra module body')).toBeInTheDocument();
      fireEvent.click(within(tabs).getByRole('tab', { name: 'Boom' }));
      expect(await screen.findByText('Module boom failed')).toBeInTheDocument();
      fireEvent.click(within(tabs).getByRole('tab', { name: 'Overview' }));
      expect(store.getState().centralTab).toBe('overview');
      await act(async () => undefined);
    } finally { console.error = spy; }
  });
});
