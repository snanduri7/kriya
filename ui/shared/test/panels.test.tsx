import { describe, expect, it } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { App } from '../src/App';
import { FakeHost, env, withSnapshots, SNAP_ID_OLD, snapshotSummary } from './fakeHost';
import { AVAILABILITY_STATES, type Availability, type RunDetail, type RunSummary } from '../src/model/kup';

const run = (id: string, over: Partial<RunSummary> = {}): RunSummary => ({ run_id: id, timestamp: '2026-09-01 10:00:00', goal: `goal of ${id}`, duration_sec: 3.5, attempts: 2, status: 'FAILED', failure_category: 'quality_gate_failed', files_modified: 'a.py,b.py', ...over });

function detailFor(id: string, avail: Availability, extra: Partial<RunDetail> = {}): RunDetail {
  const sec = <T,>(data: T) => ({ availability: avail, data: avail === 'recorded' ? data : null, reason: avail === 'recorded' ? null : `fixture ${avail}`, provenance: 'fixture' });
  return {
    run: run(id), fields: {},
    run_events: sec([{ kind: 'context.known_target_package', attempt: 1, source: 'attempt.run_attempt', authority: 'advisory', message: 'package', failure_type: null, operation: null, details: { x: 1 }, created_at: 1759561200.25, novel_field: 'kept' },
      { kind: 'candidate_gates.passed', attempt: 2, source: 'workflow', authority: 'authoritative', message: 'gates', failure_type: null, operation: null, details: 'p', created_at: 1759561260 }]),
    evidence_records: sec([{ kind: 'failure', source: 'quality_gate', attempt: 1, payload: { type: 'compile' }, sensitivity: 'local_only', created_at: 1759561201.5 }]), gate_outcomes: sec([{ attempt: 1, type: 'compile', success: false, output: 'a.py:1: error: boom' }, { attempt: 2, type: 'test', success: true, output: 'ok', passed: false }, { attempt: 2, gate: 'legacy', passed: true }]), model_hops: sec([]),
    generation_metrics: sec({ a: 1 }), failure_report: sec([{ failure_type: 'compile', category: 'quality_gate_failed', attribution_tier: 'locator' }]),
    context: sec({ items: [{ path: 'a.py', tier: 'full', member_ids: ['A.f'] }, { path: 'b.py', tier: 'signatures', omitted: true, omission_reason: 'budget_exhausted' }], tokens: { estimated: { total: 100 }, provider_reported: { prompt: 98 } } }),
    attribution: sec({ first_incorrect_state: 'CONTEXT', cause: 'omitted target', category: 'CONTEXT', evidence_ids: ['ev1', 'ev1', 'ev2'] }), // no namespace exists for these: they must render as unresolved
    diagnostics: sec({ note: 'd' }), comparisons: sec([{ path: 'a.py', before: { text: 'a\nb\n', provenance: 'commit_evidence', revision: 'r1' }, after: { text: 'a\nc\n', provenance: 'commit_evidence', revision: 'r2' } }]),
    output: sec('model said hi'), ...extra,
  } as RunDetail;
}

function hostWith(detail: RunDetail) {
  return new FakeHost(withSnapshots((req) => {
    switch (req.operation) {
      case 'capabilities': return env('capabilities', { kup_versions: [1], operations: ['history.list'], identity: { kriya_version: '0.1.0', commit: 'abcdef1234567890' }, limits: {}, features: {} });
      case 'history.list': return env('history.list', { runs: [run('r1'), run('r2', { status: 'SUCCESS', goal: 'second' })], next_cursor: null });
      case 'history.detail': return env('history.detail', detail);
      case 'history.prompt': return env('history.prompt', { prompt_rendered: 'PLAN \u0007 prompt', role: 'planner', scope: 'plan_prompt' });
      case 'workspace.status': return env('workspace.status', { workspace: '/fixture/workspace', run_active: false, status: 'IDLE', exit_code: 0, assessment: {} });
      default: return env(req.operation, null);
    }
  }));
}

async function renderAndSelect(detail: RunDetail) {
  const host = hostWith(detail);
  render(<App host={host} />);
  // Nothing is read until a snapshot is pinned: acquire explicitly (gate C-1/C-2).
  await waitFor(() => expect(screen.getByRole('button', { name: 'Acquire new snapshot' })).toBeEnabled());
  expect(host.calls.some((c) => c.operation === 'history.list')).toBe(false);
  fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
  await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
  fireEvent.click(screen.getByText('goal of r1'));
  await waitFor(() => expect(screen.getByRole('heading', { name: 'goal of r1' })).toBeInTheDocument());
  return host;
}

/** run_events produced by Kriya's own serializer through the KUP adapter (ui/fixtures/serializer_events.py). */
import serializerFixture from '../../fixtures/serializer/run_events.json';
const SERIALIZER = serializerFixture as unknown as { run_events: Record<string, unknown>[]; serializer_keys: string[] };

describe('events exactly as Kriya serializes them reach every consumer (08 review F-3)', () => {
  it('timeline names, context list, model strip and the selected-event payload all show the serializer-shaped events', async () => {
    const events = SERIALIZER.run_events;
    expect(events.map((e) => e.kind)).toEqual(['context.known_target_package', 'developer.prompt_composition', 'model.transition', 'model.role_metrics']);
    for (const e of events) expect(Object.keys(e).sort()).toEqual([...SERIALIZER.serializer_keys].sort());
    const detail = detailFor('r1', 'recorded', { run_events: { availability: 'recorded', provenance: 'runs.run_events', reason: null, data: events } } as unknown as Partial<RunDetail>);
    await renderAndSelect(detail);
    // timeline: every event named, none "(unnamed event)", times rendered as labelled UTC from created_at
    const list = screen.getByRole('listbox', { name: /recorded events/i });
    for (const e of events) expect(within(list).getAllByText(String(e.kind)).length).toBeGreaterThan(0);
    expect(within(list).queryByText('(unnamed event)')).not.toBeInTheDocument();
    expect(within(list).getAllByText(/2026-10-04 07:0[01]:\d\d\.\d{3} UTC/).length).toBe(4);
    expect(screen.getByText(/all events \(4\)/)).toBeInTheDocument();
    // context tab lists the context package and the prompt composition
    const contextList = screen.getByText('Context events (recorded)').nextElementSibling as HTMLElement;
    expect(contextList).toHaveTextContent('context.known_target_package');
    expect(contextList).toHaveTextContent('developer.prompt_composition');
    expect(contextList).not.toHaveTextContent('model.role_metrics');
    // trust strip: the Developer request profile (model.transition details.to) supplies model and qualification
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('qwen2.5-coder:14b / QUALIFIED');
    // selecting the metrics event shows its real details (rows) in the evidence tab
    fireEvent.click(within(list).getAllByText('model.role_metrics')[0]!);
    fireEvent.click(screen.getByRole('tab', { name: /^Evidence/ }));
    await waitFor(() => expect(screen.getByText(/"runtime_digest"/)).toBeInTheDocument());
    expect(screen.queryByText(/unknown fields preserved/)).not.toBeInTheDocument();
  });
  it('role metrics alone still name the model; without any model event both facts stay "unknown"', async () => {
    const metricsOnly = SERIALIZER.run_events.filter((e) => e.kind === 'model.role_metrics');
    await renderAndSelect(detailFor('r1', 'recorded', { run_events: { availability: 'recorded', provenance: 'runs.run_events', reason: null, data: metricsOnly } } as unknown as Partial<RunDetail>));
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('qwen2.5-coder:7b / unknown');
  });
});

describe('digest verified at pin; metadata checked per query (08 review F-4)', () => {
  it('after an acquisition the exact new id is verified BEFORE any history read; the strip states the guarantee in those words', async () => {
    const host = await renderAndSelect(detailFor('r1', 'recorded'));
    const ops = host.calls.map((c) => c.operation);
    const verifyCall = host.calls.find((c) => c.operation === 'snapshot.verify') as { snapshot_id: string } | undefined;
    expect(verifyCall).toBeDefined();
    const acquiredId = verifyCall?.snapshot_id ?? '';
    expect(acquiredId).toMatch(/^\d{8}T\d{12}Z-[0-9a-f]{8}$/);
    expect(ops.indexOf('snapshot.verify')).toBeGreaterThan(ops.indexOf('snapshot.acquire'));
    expect(ops.indexOf('snapshot.verify')).toBeLessThan(ops.indexOf('history.list'));
    for (const c of host.calls.filter((c) => c.operation.startsWith('history.'))) expect((c as { snapshot_id: string }).snapshot_id).toBe(acquiredId);
    const strip = screen.getByRole('region', { name: /trust strip/i });
    expect(strip).toHaveTextContent('digest verified at pin (2026-10-04T09:31:00.000000Z); metadata checked per query');
    expect(strip).toHaveTextContent(/sha256 [0-9a-f]{16}…/);
    expect(strip.textContent).not.toMatch(/protected against|tamper-proof|guaranteed unchanged/i);
    // a refresh re-reads under the pin without re-verifying: the guarantee is per pin, not per query
    fireEvent.click(screen.getByRole('button', { name: 'Refresh displayed snapshot' }));
    await waitFor(() => expect(host.calls.filter((c) => c.operation === 'history.list').length).toBeGreaterThanOrEqual(2));
    expect(host.calls.filter((c) => c.operation === 'snapshot.verify').length).toBe(1);
  });
  it('a chosen snapshot that fails verification is never pinned or read; the failure is shown typed', async () => {
    const host = new FakeHost(withSnapshots((req) => {
      switch (req.operation) {
        case 'capabilities': return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
        case 'history.list': return env('history.list', { runs: [run('r1')], next_cursor: null });
        default: return env(req.operation, null);
      }
    }, [snapshotSummary(SNAP_ID_OLD, true), snapshotSummary()], [SNAP_ID_OLD]));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByRole('combobox', { name: 'Displayed snapshot' }), { target: { value: SNAP_ID_OLD } });
    await waitFor(() => expect(screen.getByRole('status', { name: 'Snapshot state' })).toHaveTextContent(/snapshot not displayed: digest verification failed: SNAPSHOT_CORRUPT/));
    expect(host.calls.filter((c) => c.operation === 'snapshot.verify').map((c) => (c as { snapshot_id: string }).snapshot_id)).toEqual([SNAP_ID_OLD]);
    expect(host.calls.some((c) => c.operation.startsWith('history.'))).toBe(false);
    expect(screen.queryByText('goal of r1')).not.toBeInTheDocument();
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('none pinned');
    // the other snapshot verifies and is displayed
    fireEvent.change(screen.getByRole('combobox', { name: 'Displayed snapshot' }), { target: { value: '20261004T093000000000Z-f1c70001' } });
    await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
    expect((host.calls.filter((c) => c.operation === 'history.list').at(-1) as { snapshot_id: string }).snapshot_id).toBe('20261004T093000000000Z-f1c70001');
  });
  it('a verification answer that names another snapshot does not pin: the binding is to the exact selected id', async () => {
    const host = new FakeHost(withSnapshots((req) => {
      switch (req.operation) {
        case 'capabilities': return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
        case 'history.list': return env('history.list', { runs: [run('r1')], next_cursor: null });
        default: return env(req.operation, null);
      }
    }, [snapshotSummary(SNAP_ID_OLD, true), snapshotSummary()], [], [SNAP_ID_OLD]));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByRole('combobox', { name: 'Displayed snapshot' }), { target: { value: SNAP_ID_OLD } });
    await waitFor(() => expect(screen.getByRole('status', { name: 'Snapshot state' })).toHaveTextContent(/not displayed: the verification answer did not confirm this exact snapshot/));
    expect(host.calls.some((c) => c.operation.startsWith('history.'))).toBe(false);
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('none pinned');
  });
  it('an acquired snapshot that fails verification is not pinned either: nothing of it is read', async () => {
    const host = new FakeHost(withSnapshots((req) => (req.operation === 'capabilities' ? env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} }) : env(req.operation, null)), [], ['20261004T100000000001Z-acc00001']));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Acquire new snapshot' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getByRole('status', { name: 'Snapshot state' })).toHaveTextContent(/digest verification failed: SNAPSHOT_CORRUPT/));
    expect(host.calls.some((c) => c.operation.startsWith('history.'))).toBe(false);
    expect(screen.getByRole('region', { name: /trust strip/i })).toHaveTextContent('none pinned');
  });
});

describe('the configuration directory is explicit and visible, distinct from the recovery workspace (08 review F-5)', () => {
  it('the strip shows the configuration directory with its source and the workspace as the recovery-assessment input', async () => {
    await renderAndSelect(detailFor('r1', 'recorded'));
    const strip = screen.getByRole('region', { name: /trust strip/i });
    expect(strip).toHaveTextContent('Configuration directory/fixture/home');
    expect(strip).toHaveTextContent('default: operator HOME; kriya.yaml is discovered here (child working directory); independent of the workspace');
    expect(strip).toHaveTextContent('Workspace (recovery assessment)/fixture/workspace');
  });
  it('an invalid configuration directory is shown as such, with the host\'s reason', async () => {
    const host = hostWith(detailFor('r1', 'recorded'));
    host.info = { ...host.info, configDirectory: null, configDirectorySource: 'invalid', configDirectoryProblem: 'the configDirectory setting is not an existing directory: /gone' };
    render(<App host={host} />);
    const strip = await screen.findByRole('region', { name: /trust strip/i });
    expect(strip).toHaveTextContent('Configuration directoryinvalid');
    expect(strip).toHaveTextContent('/gone - every Kriya call is refused until the configDirectory setting is fixed');
  });
});

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
    expect(screen.getByText(/compile · failure/)).toBeInTheDocument(); // the writers' fields: type and the success boolean
    expect(screen.getByText('a.py:1: error: boom')).toBeInTheDocument(); // recorded output, verbatim
    expect(screen.getByText(/test · result ambiguous \(conflicting fields\)/)).toBeInTheDocument();
    expect(screen.getByText(/conflicting result fields, not resolved: success=true, passed=false/)).toBeInTheDocument();
    expect(screen.getByText(/type not recorded · result not recorded/)).toBeInTheDocument(); // legacy record: nothing inferred
    expect(screen.getByText(/unknown fields preserved: gate, passed/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /^Evidence/ }));
    expect(screen.getByText(/select an event in the timeline/)).toBeInTheDocument();
    // selecting an event routes to Evidence and shows unknown fields
    const events = screen.getByRole('listbox', { name: /recorded events/i });
    fireEvent.click(within(events).getAllByRole('option')[0]!);
    expect(screen.getByText(/unknown fields preserved: novel_field/)).toBeInTheDocument();
    // Drawer
    expect(screen.getByText(/#1 failure · quality_gate · attempt 1/)).toBeInTheDocument(); // the serializer's fields, recorded order
    expect(screen.getByText(/Evidence records carry no identifier/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: /^Why/ }));
    expect(screen.getByText('omitted target')).toBeInTheDocument();
    // attribution evidence references: every id stays visible as an unresolved reference, never linked, never verified
    const refs = within(screen.getByRole('list', { name: 'Evidence references' })).getAllByRole('listitem');
    expect(refs.map((li) => li.textContent)).toEqual(['ev1 — unresolved reference', 'ev1 — unresolved reference', 'ev2 — unresolved reference']);
    expect(screen.getByText(/3 references: no identifier namespace is defined/)).toBeInTheDocument();
    expect(screen.getByText(/Repeated within the list: ev1\./)).toBeInTheDocument();
    expect(screen.getByText(/none resolves to a record; nothing here is verified attribution/)).toBeInTheDocument();
    expect(screen.queryByText(/\bresolved\b/)).not.toBeInTheDocument(); // the word "resolved" never appears on its own
    expect(screen.getByText(/attribution record: \d+ chars/)).toBeInTheDocument(); // the raw record stays inspectable
    fireEvent.click(screen.getByRole('tab', { name: /^Diff/ }));
    fireEvent.click(screen.getByRole('button', { name: 'a.py' }));
    expect(screen.getByRole('listbox', { name: 'Diff of a.py' })).toHaveTextContent('c');
    // open in IDE goes through the host only
    fireEvent.click(screen.getAllByRole('button', { name: 'Open in IDE' })[0]!);
    await waitFor(() => expect(host.opened.length).toBe(1));
    expect(screen.getByRole('status', { name: 'Host action result' })).toHaveTextContent('opened in vscode');
  });

  for (const state of AVAILABILITY_STATES.filter((s) => s !== 'recorded')) {
    it(`renders honest "${state}" panels, reconstructing nothing (D-5)`, async () => {
      await renderAndSelect(detailFor('r1', state));
      const label = state.replace('_', ' ');
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
      expect(screen.queryByText('omitted target')).not.toBeInTheDocument();
      fireEvent.click(screen.getByRole('tab', { name: /^Output/ }));
      expect(screen.queryByText(/model said hi/)).not.toBeInTheDocument();
      expect(screen.getAllByRole('note').some((el) => el.textContent?.includes(`Model output: ${label}`))).toBe(true); // a static statement, not a live region
    });
  }

  for (const [name, attribution, expected] of [
    ['absent', { first_incorrect_state: 'CONTEXT', cause: 'omitted target', category: 'CONTEXT' }, 'not recorded (field absent)'],
    ['empty', { first_incorrect_state: 'CONTEXT', cause: 'omitted target', category: 'CONTEXT', evidence_ids: [] }, 'none recorded (empty list)'],
    ['null', { first_incorrect_state: 'CONTEXT', cause: 'omitted target', category: 'CONTEXT', evidence_ids: null }, 'recorded as null'],
  ] as const) {
    it(`attribution evidence references ${name} are stated as such, never shown as references or as resolved`, async () => {
      await renderAndSelect(detailFor('r1', 'recorded', { attribution: { availability: 'recorded', data: attribution, reason: null, provenance: 'fixture' } as never }));
      fireEvent.click(screen.getByRole('tab', { name: /^Why/ }));
      expect(screen.getByText(expected)).toBeInTheDocument();
      expect(screen.queryByRole('list', { name: 'Evidence references' })).not.toBeInTheDocument();
      expect(screen.queryByText(/unresolved reference/)).not.toBeInTheDocument();
    });
  }

  it('an unknown availability value is shown literally, never as success', async () => {
    await renderAndSelect(detailFor('r1', 'recorded', { output: { availability: 'totally_new_state', data: 'x' } }));
    fireEvent.click(screen.getByRole('tab', { name: /^Output/ }));
    expect(screen.getByText(/unknown availability: totally_new_state/)).toBeInTheDocument();
    expect(screen.queryByText('x')).not.toBeInTheDocument();
  });

  it('a KUP error envelope on the pinned read is shown with its code', async () => {
    const host = new FakeHost(withSnapshots((req) => req.operation === 'history.list'
      ? env('history.list', null, { error: { code: 'SNAPSHOT_CORRUPT', message: 'digest differs' }, source: null, consistency: null } as never)
      : env(req.operation, null)));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Acquire new snapshot' })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: 'Acquire new snapshot' }));
    await waitFor(() => expect(screen.getByText(/snapshot read failed: SNAPSHOT_CORRUPT/)).toBeInTheDocument());
  });

  it('an unsupported schema version is refused, never rendered', async () => {
    const host = new FakeHost((req) => ({ ...env(req.operation, { runs: [run('ghost')], next_cursor: null }), schema_version: 2 }));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getAllByText(/UNSUPPORTED_SCHEMA_VERSION/).length).toBeGreaterThan(0));
    expect(screen.queryByText('goal of ghost')).not.toBeInTheDocument();
  });

  it('Refresh never acquires; Acquire is the only acquisition request (gate C-1)', async () => {
    const host = await renderAndSelect(detailFor('r1', 'recorded'));
    const acquisitions = () => host.calls.filter((c) => c.operation === 'snapshot.acquire').length;
    expect(acquisitions()).toBe(1);
    fireEvent.click(screen.getByRole('button', { name: 'Refresh displayed snapshot' }));
    await waitFor(() => expect(host.calls.filter((c) => c.operation === 'history.list').length).toBeGreaterThanOrEqual(2));
    expect(acquisitions()).toBe(1);
    // every history request carried the pinned id
    const pinned = host.calls.find((c) => c.operation === 'snapshot.acquire');
    expect(pinned).toBeTruthy();
    for (const c of host.calls.filter((c) => c.operation.startsWith('history.'))) expect((c as { snapshot_id: string }).snapshot_id).toMatch(/^\d{8}T\d{12}Z-[0-9a-f]{8}$/);
  });

  it('switching the displayed snapshot invalidates the previous selection and reads under the new id (gate C-2)', async () => {
    const host = new FakeHost(withSnapshots((req) => {
      switch (req.operation) {
        case 'capabilities': return env('capabilities', { kup_versions: [1], operations: [], identity: {}, limits: {}, features: {} });
        case 'history.list': return env('history.list', { runs: [run('r1')], next_cursor: null });
        case 'history.detail': return env('history.detail', detailFor('r1', 'recorded'));
        default: return env(req.operation, null);
      }
    }, [snapshotSummary(SNAP_ID_OLD, true), snapshotSummary()]));
    render(<App host={host} />);
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Displayed snapshot' })).toBeInTheDocument());
    // choose a published snapshot explicitly (no implicit newest)
    await waitFor(() => expect(screen.getAllByRole('option').length).toBeGreaterThan(1));
    fireEvent.change(screen.getByRole('combobox', { name: 'Displayed snapshot' }), { target: { value: SNAP_ID_OLD } });
    await waitFor(() => expect(screen.getByText('goal of r1')).toBeInTheDocument());
    fireEvent.click(screen.getByText('goal of r1'));
    await waitFor(() => expect(screen.getByRole('heading', { name: 'goal of r1' })).toBeInTheDocument());
    const before = host.calls.filter((c) => c.operation === 'history.detail').length;
    fireEvent.change(screen.getByRole('combobox', { name: 'Displayed snapshot' }), { target: { value: '20261004T093000000000Z-f1c70001' } });
    await waitFor(() => expect(screen.queryByRole('heading', { name: 'goal of r1' })).not.toBeInTheDocument());
    expect(host.calls.filter((c) => c.operation === 'history.detail').length).toBe(before);
    const last = host.calls.filter((c) => c.operation === 'history.list').at(-1) as { snapshot_id: string };
    expect(last.snapshot_id).toBe('20261004T093000000000Z-f1c70001');
  });

  it('labels: "snapshot acquired at", metadata change detected, never current/latest (gate C-3)', async () => {
    await renderAndSelect(detailFor('r1', 'recorded'));
    const strip = screen.getByRole('region', { name: /trust strip/i });
    expect(strip).toHaveTextContent(/snapshot acquired at 2026-10-04T09:30:00/);
    expect(strip.textContent).not.toMatch(/\b(current|latest|verified unchanged)\b/i);
    expect(screen.getByRole('status', { name: 'Snapshot state' })).toHaveTextContent(/not a freshness guarantee/);
  });
});
