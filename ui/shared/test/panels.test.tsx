import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { App } from '../src/App';
import { FakeHost, env } from './fakeHost';
import { AVAILABILITY_STATES, type Availability, type RunDetail, type RunSummary } from '../src/model/kup';

const run = (id: string, over: Partial<RunSummary> = {}): RunSummary => ({ run_id: id, timestamp: '2026-09-01 10:00:00', goal: `goal of ${id}`, duration_sec: 3.5, attempts: 2, status: 'FAILED', failure_category: 'quality_gate_failed', files_modified: 'a.py,b.py', ...over });

function detailFor(id: string, avail: Availability, extra: Partial<RunDetail> = {}): RunDetail {
  const sec = <T,>(data: T) => ({ availability: avail, data: avail === 'recorded' ? data : null, reason: avail === 'recorded' ? null : `fixture ${avail}`, provenance: 'fixture' });
  return {
    run: run(id), fields: {},
    run_events: sec([{ event: 'context.known_target_package', attempt: 1, at: 't1', payload: { x: 1 }, novel_field: 'kept' }, { event: 'gate.compile', attempt: 2, at: 't2', payload: 'p' }]),
    evidence_records: sec([{ evidence_id: 'ev1' }]), gate_outcomes: sec([{ attempt: 1, gate: 'compile', passed: false }]), model_hops: sec([]),
    generation_metrics: sec({ a: 1 }), failure_report: sec([{ failure_type: 'compile', category: 'quality_gate_failed', attribution_tier: 'locator' }]),
    context: sec({ items: [{ path: 'a.py', tier: 'full', member_ids: ['A.f'] }, { path: 'b.py', tier: 'signatures', omitted: true, omission_reason: 'budget_exhausted' }], tokens: { estimated: { total: 100 }, provider_reported: { prompt: 98 } } }),
    attribution: sec({ first_incorrect_state: 'CONTEXT', cause: 'omitted target', category: 'CONTEXT', evidence_ids: ['ev1'] }),
    diagnostics: sec({ note: 'd' }), comparisons: sec([{ path: 'a.py', before: { text: 'a\nb\n', provenance: 'commit_evidence', revision: 'r1' }, after: { text: 'a\nc\n', provenance: 'commit_evidence', revision: 'r2' } }]),
    output: sec('model said hi'), ...extra,
  } as RunDetail;
}

function hostWith(detail: RunDetail) {
  return new FakeHost((req) => {
    switch (req.operation) {
      case 'capabilities': return env('capabilities', { kup_versions: [1], operations: ['history.list'], identity: { kriya_version: '0.1.0', commit: 'abcdef1234567890' }, limits: {}, features: {} });
      case 'history.list': return env('history.list', { runs: [run('r1'), run('r2', { status: 'SUCCESS', goal: 'second' })], next_cursor: null });
      case 'history.detail': return env('history.detail', detail);
      case 'history.prompt': return env('history.prompt', { prompt_rendered: 'PLAN \u0007 prompt', role: 'planner', scope: 'plan_prompt' });
      case 'workspace.status': return env('workspace.status', { workspace: '/fixture/workspace', run_active: false, status: 'IDLE', exit_code: 0, assessment: {} });
    }
  });
}

async function renderAndSelect(detail: RunDetail) {
  const host = hostWith(detail);
  render(<App host={host} />);
  await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
  fireEvent.click(screen.getByText('goal of r1'));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'goal of r1' })).toBeInTheDocument());
  return host;
}

describe('every panel renders with a fake host (P-R3)', () => {
  it('trust strip, runs, timeline, inspector tabs and drawer all render recorded data', async () => {
    const host = await renderAndSelect(detailFor('r1', 'recorded'));
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('/fixture/state/traces.db');
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('0.1.0 @ abcdef1234');
    expect(screen.getByRole('listbox', { name: /recorded events/i })).toBeInTheDocument();
    expect(screen.getByText('1 unknown field')).toBeInTheDocument(); // novel_field preserved and surfaced
    // Context tab
    expect(screen.getByRole('table', { name: 'Context items' })).toHaveTextContent('budget_exhausted');
    expect(screen.getByText(/Provider-reported/)).toBeInTheDocument();
    // Prompt tab: explicit request only
    fireEvent.click(screen.getByRole('tab', { name: /^Prompt/ }));
    expect(host.calls.some((c) => c.operation === 'history.prompt')).toBe(false);
    fireEvent.click(screen.getByText('Load prompt (sensitive)'));
    await waitFor(() => expect(screen.getByText(/role: planner/)).toBeInTheDocument());
    expect(screen.getByText(/PLAN \\u0007 prompt/)).toBeInTheDocument(); // control char sanitized
    fireEvent.click(screen.getByRole('tab', { name: /^Output/ }));
    expect(screen.getByText(/model said hi/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /^Gates/ }));
    expect(screen.getByText(/compile · failed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /^Evidence/ }));
    expect(screen.getByText(/select an event in the timeline/)).toBeInTheDocument();
    // selecting an event routes to Evidence and shows unknown fields
    const events = screen.getByRole('listbox', { name: /recorded events/i });
    fireEvent.click(within(events).getAllByRole('option')[0]!);
    expect(screen.getByText(/unknown fields preserved: novel_field/)).toBeInTheDocument();
    // Drawer
    fireEvent.click(screen.getByRole('tab', { name: /^Why/ }));
    expect(screen.getByText('omitted target')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /^Diff/ }));
    fireEvent.click(screen.getByRole('button', { name: 'a.py' }));
    expect(screen.getByRole('listbox', { name: 'Diff of a.py' })).toHaveTextContent('c');
    // open in IDE goes through the host only
    fireEvent.click(screen.getAllByRole('button', { name: 'Open in IDE' })[0]!);
    await waitFor(() => expect(host.opened.length).toBe(1));
    expect(screen.getByRole('status', { name: '' })).toHaveTextContent('opened in vscode');
  });

  for (const state of AVAILABILITY_STATES.filter((s) => s !== 'recorded')) {
    it(`renders honest "${state}" panels, reconstructing nothing (D-5)`, async () => {
      await renderAndSelect(detailFor('r1', state));
      const label = state.replace('_', ' ');
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      expect(screen.queryByText('omitted target')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('tab', { name: /^Output/ }));
      expect(screen.queryByText(/model said hi/)).not.toBeInTheDocument();
      expect(screen.getAllByRole('status').some((el) => el.textContent?.includes(`Model output: ${label}`))).toBe(true);
    });
  }

  it('an unknown availability value is shown literally, never as success', async () => {
    await renderAndSelect(detailFor('r1', 'recorded', { output: { availability: 'totally_new_state', data: 'x' } }));
    fireEvent.click(screen.getByRole('tab', { name: /^Output/ }));
    expect(screen.getByText(/unknown availability: totally_new_state/)).toBeInTheDocument();
    expect(screen.queryByText('x')).not.toBeInTheDocument();
  });

  it('a KUP error envelope marks the list not current and shows the code', async () => {
    const host = new FakeHost((req) => req.operation === 'history.list'
      ? env('history.list', null, { error: { code: 'READ_ONLY_UNAVAILABLE', message: 'WAL store needs -shm' }, source: null, consistency: null })
      : env(req.operation, null));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getByText(/list not current: READ_ONLY_UNAVAILABLE/)).toBeInTheDocument());
  });

  it('an unsupported schema version is refused, never rendered', async () => {
    const host = new FakeHost((req) => ({ ...env(req.operation, { runs: [run('ghost')], next_cursor: null }), schema_version: 2 }));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getAllByText(/UNSUPPORTED_SCHEMA_VERSION/).length).toBeGreaterThan(0));
    expect(screen.queryByText('goal of ghost')).not.toBeInTheDocument();
  });
});
