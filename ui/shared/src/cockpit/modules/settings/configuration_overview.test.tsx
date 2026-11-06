/** The customer-comprehensible configuration view: read-only, every value a field of Kriya's execution.preflight answer. */
import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ExecutionPreflight } from '../../../model/kup';
import { ConfigurationOverview } from './ConfigurationOverview';

const row = (id: string, state: string, reason: string) => ({ id, label: id, state, reason_code: reason, detail: `${id} detail`, evidence: ['field.x'] });
const pre = (over: Partial<ExecutionPreflight> = {}): ExecutionPreflight => ({
  ready: true, blockers: [], workspace: { path: '/w', identity: 'i', name: 'w' },
  configuration: { status: 'loaded', source: 'packaged_default', file: null, directory: '/w', discovery: 'd', fingerprint: 'f', authority: { status: 'no_findings', fields: [] } },
  state: { directory: '/state', source: 'default', cockpit_directory: '/state', observable_by_cockpit: true },
  model: { provider: 'openai', model: 'qwen3-coder:30b', endpoint: 'http://localhost:11434/v1', context_window: 32768, fallback_chain: ['qwen3.6:35b'], role_overrides: { planner: 'qwen3.6:35b' } },
  recovery: { status: 'CLEAN', run_active: false }, interaction: { approvals: 'change_review', knowledge_policy: 'strict', note: 'n' }, toolchains: { tools: [], note: 'n' },
  health: [row('lookup', 'NOT_REQUIRED', 'LOOKUP_DISABLED'), row('security', 'AVAILABLE', 'CONTAINMENT_REQUIRED'), row('lsp', 'UNAVAILABLE', 'JDTLS_NOT_CONTAINABLE'), row('code_intelligence', 'AVAILABLE', 'STRUCTURAL_INDEX')] as ExecutionPreflight['health'],
  ...over } as ExecutionPreflight);
const line = (label: string) => screen.getByText(label).closest('div,tr,li,dl') as HTMLElement | null;

describe('ConfigurationOverview', () => {
  it('shows the models, the planner override, the fallback chain and the context window as Kriya resolved them', () => {
    render(<ConfigurationOverview pre={pre()} />);
    const text = screen.getByTestId('configuration-overview').textContent ?? '';
    expect(text).toContain('openai at http://localhost:11434/v1');
    expect(text).toContain('Plannerqwen3.6:35b');
    expect(text).toContain('Developerqwen3-coder:30b (the default model)');
    expect(text).toContain('qwen3.6:35b');
    expect(text).toContain('32,768 tokens');
  });
  it('states the privacy, execution and code-intelligence settings from the health rows, with the reason code and evidence in the title', () => {
    render(<ConfigurationOverview pre={pre()} />);
    expect(within(screen.getByText('Off').closest('span') as HTMLElement).getByText('Off')).toBeTruthy();
    expect(screen.getAllByText('In force').map((e) => (e.closest('span[title]') as HTMLElement).title)).toContain('CONTAINMENT_REQUIRED; evidence: field.x');
    expect(screen.getByText('Unavailable')).toBeTruthy();
    expect(line('Java language server')).not.toBeNull();
    expect(screen.getByText('Every change is held for your review before it is applied')).toBeTruthy();
  });
  it('says what it does not know instead of inventing it: no model, no health rows, no memory', () => {
    render(<ConfigurationOverview pre={pre({ model: null, health: undefined, memory: undefined })} />);
    expect(screen.getByText(/did not load, so no model is resolved/)).toBeTruthy();
    expect(screen.getAllByText('not reported by this Kriya').length).toBeGreaterThanOrEqual(6);
  });
  it('is read-only: it offers no control and says where operator settings are changed', () => {
    render(<ConfigurationOverview pre={pre()} />);
    expect(screen.queryAllByRole('button')).toHaveLength(0);
    expect(screen.queryAllByRole('textbox')).toHaveLength(0);
    expect(screen.getByRole('note').textContent).toContain('nothing here writes them');
    expect(screen.getByRole('note').textContent).toContain('packaged defaults');
  });
});
