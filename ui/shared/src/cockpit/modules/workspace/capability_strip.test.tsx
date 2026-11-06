/**
 * The capability strip. The renderer states nothing of its own: a health row's state, reason code, detail and evidence are Kriya's `execution.preflight.health`; the model,
 * Skills and Tools cells are the page's own backend answers; Learning is the memory block's counts. A capability with no answer is absent, never shown as available.
 * The backend rows are pinned in tests/test_project_health_rows.py.
 */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExecutionPreflight, ProjectMemory } from '../../../model/kup';
import { renderWorkspaceShell } from '../../testing/harness';
import { CapabilityStrip } from './CapabilityStrip';

type Row = NonNullable<ExecutionPreflight['health']>[number];
const row = (id: string, state: Row['state'], over: Partial<Row> = {}): Row =>
  ({ id, label: id, state, reason_code: `${id.toUpperCase()}_CODE`, detail: `${id} detail`, evidence: ['autonomy.x'], ...over });
const memory = (current: number, stale = 0, store_state: ProjectMemory['store_state'] = 'OK'): ProjectMemory => ({
  schema_version: 1, repository: 'r', store_state, store_detail: null, facts: [], rejected: [], runs: [],
  counts: { current, stale, rejected_total: 0, learned_last_run: 0, reused_last_run: current, rejected_last_run: 0 } });
const cell = (id: string) => document.querySelector(`[data-capability="${id}"]`) as HTMLElement | null;

describe('the capability strip', () => {
  it('shows each backend row with its state word and carries the reason code, detail and evidence in its title', () => {
    render(<CapabilityStrip health={[row('security', 'AVAILABLE'), row('lsp', 'UNAVAILABLE'), row('lookup', 'DISABLED'), row('private_state', 'DEGRADED')]} composed={{}} memory={undefined} />);
    expect(within(cell('security')!).getByText('Available')).toBeTruthy();
    expect(within(cell('lsp')!).getByText('Unavailable')).toBeTruthy();
    expect(within(cell('lookup')!).getByText('Disabled')).toBeTruthy();   // a setting that is off is DISABLED, never "not required"
    expect(within(cell('private_state')!).getByText('Degraded')).toBeTruthy();
    expect(cell('security')!.title).toBe('security: security detail (SECURITY_CODE; evidence: autonomy.x)');
  });

  it('orders the strip by purpose and uses the short names, whatever order Kriya listed the rows in', () => {
    render(<CapabilityStrip health={[row('recovery', 'AVAILABLE'), row('verification', 'AVAILABLE'), row('security', 'AVAILABLE'), row('code_intelligence', 'AVAILABLE')]}
      composed={{ model: { label: 'Model server', tone: 'ok', status: 'Answering' } }} memory={memory(2)} />);
    expect([...document.querySelectorAll('[data-capability]')].map((e) => e.getAttribute('data-capability'))).toEqual(['model', 'code_intelligence', 'security', 'verification', 'recovery', 'memory']);
    expect(screen.getByText('Local model')).toBeTruthy();
    expect(screen.getByText('Learning')).toBeTruthy();
  });

  it('passes a composed cell through in the answer\'s own words and omits one with no answer', () => {
    render(<CapabilityStrip health={[row('security', 'AVAILABLE')]} composed={{ model: { label: 'Model server', tone: 'bad', status: 'Not reachable', detail: 'start the model server' }, skills: null, tools: { label: 'Tools', tone: 'info', status: '0 MCP servers' } }} memory={undefined} />);
    expect(within(cell('model')!).getByText('Not reachable')).toBeTruthy();
    expect(cell('model')!.title).toBe('Model server: Not reachable - start the model server');
    expect(cell('skills')).toBeNull();
    expect(within(cell('tools')!).getByText('0 MCP servers')).toBeTruthy();
  });

  it('reports Learning from the memory counts and never as available by default', () => {
    const { unmount } = render(<CapabilityStrip health={[]} composed={{}} memory={memory(0)} />);
    expect(within(cell('memory')!).getByText('Nothing yet')).toBeTruthy();
    unmount();
    const second = render(<CapabilityStrip health={[]} composed={{}} memory={memory(3, 1)} />);
    expect(within(cell('memory')!).getByText('3 current · 1 stale')).toBeTruthy();
    second.unmount();
    render(<CapabilityStrip health={[]} composed={{}} memory={memory(0, 0, 'STORE_UNREADABLE')} />);
    expect(within(cell('memory')!).getByText('Unusable')).toBeTruthy();
    expect(cell('memory')!.title).toContain('STORE_UNREADABLE');
  });

  it('renders nothing when Kriya reports no capability at all', () => {
    const { container } = render(<CapabilityStrip health={[]} composed={{}} memory={undefined} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows a state word it does not know literally rather than guessing a meaning', () => {
    render(<CapabilityStrip health={[row('security', 'WEIRD' as Row['state'])]} composed={{}} memory={undefined} />);
    expect(within(cell('security')!).getByText('WEIRD')).toBeTruthy();
  });
});

describe('the Project page', () => {
  it('shows the strip from the health rows in the preflight, with the memory counts', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/empty', execution: { outcome: 'success', health: [row('security', 'AVAILABLE'), row('lsp', 'UNAVAILABLE')], memory: memory(2) } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    const strip = await screen.findByTestId('capability-strip');
    expect(within(strip).getByText('Security')).toBeTruthy();
    expect(within(strip).getByText('2 current')).toBeTruthy();
  });

  it('shows no strip when the preflight carries no health (an older Kriya)', async () => {
    renderWorkspaceShell({ fixture: { workspace: '/fixture/empty', execution: { outcome: 'success' } } });
    await screen.findByRole('navigation', { name: 'Destinations' });
    await screen.findByText('What do you want Kriya to do?');
    expect(screen.queryByTestId('capability-strip')).toBeNull();
  });
});
