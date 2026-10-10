import { fireEvent, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { EvidenceRecord } from '../../core';
import { renderModule } from '../../testing/harness';
import { buildTree, timelineModule } from '.';

const rec = (seq: number, kind: string, over: Record<string, unknown> = {}): EvidenceRecord => ({ schema: 's', seq, prev: 'p', kind, run_id: 'r', unit_id: null, attempt_number: null, role: null, provenance: 'OBSERVED', payload: {}, content_digests: {}, blobs: {}, phase_provenance: null, ...over }) as unknown as EvidenceRecord;

describe('Timeline: the only hierarchical navigator', () => {
  it('structure comes only from recorded identity: run-level nodes, units, attempts, nodes; nothing is invented', () => {
    const tree = buildTree([
      rec(1, 'model.request', { role: 'planner' }),
      rec(2, 'gate.result', { unit_id: 'u', attempt_number: 1, payload: { gate: 'compile', success: false } }),
      rec(3, 'attempt.concluded', { unit_id: 'u', attempt_number: 1, payload: { succeeded: false } }),
      rec(4, 'gate.result', { unit_id: 'u', attempt_number: 2, payload: { gate: 'compile', success: true } }),
      rec(5, 'run.closed', { payload: { terminal_status: 'FAILED' } }),
      rec(6, 'unit.opened', { unit_id: 'u' }),
    ]);
    expect(tree.run.map((n) => n.seq)).toEqual([1, 5]);
    expect(tree.units).toHaveLength(1);
    expect(tree.units[0]!.attempts.map((a) => [a.number, a.nodes.map((n) => n.seq)])).toEqual([[1, [2, 3]], [2, [4]]]);
    expect(tree.units[0]!.attempts[0]!.nodes[0]).toMatchObject({ gateId: '1:compile@2', attempt: '1', tone: 'bad' });
  });
  it('real recorder vocabulary (shape MEASURED on a real store): model.result calls, mirrored gate outcomes and events, fallback decisions', () => {
    const tree = buildTree([
      rec(1, 'run.opened'),
      rec(2, 'unit.opened', { unit_id: 'direct' }),
      rec(3, 'mirror.event', { unit_id: 'direct', payload: { kind: 'attempt.started', attempt: 0 } }),
      rec(4, 'attempt.opened', { unit_id: 'direct', attempt_number: 1 }),
      rec(5, 'fallback.decision', { unit_id: 'direct', attempt_number: 1, payload: { phase: 'call' } }),
      rec(6, 'model.request', { unit_id: 'direct', attempt_number: 1, role: 'developer', call_seq: 1 }),
      rec(7, 'model.result', { unit_id: 'direct', attempt_number: 1, role: 'developer', call_seq: 1, payload: { finish_reason: 'stop' } }),
      rec(8, 'mirror.gate_outcome', { unit_id: 'direct', attempt_number: 1, payload: { attempt: 1, index: 0, success: true, type: 'compile' } }),
      rec(9, 'attempt.closed', { unit_id: 'direct', attempt_number: 1, payload: { outcome: 'success' } }),
      rec(10, 'model.result', { unit_id: 'direct', role: 'reviewer', call_seq: 2, phase: 'review' }),
    ]);
    const unit = tree.units[0]!;
    expect(unit.loose.map((n) => n.label)).toEqual(['event: attempt.started', 'model call #2 (reviewer)']);
    const nodes = unit.attempts[0]!.nodes;
    expect(nodes.map((n) => n.seq)).toEqual([5, 7, 8, 9]); // the request of an answered call is the same call, not a second node
    expect(nodes.map((n) => n.label)).toEqual(['fallback decision (call)', 'model call #1 (developer) · stop', 'compile passed (mirrored outcome)', 'attempt closed: success']);
    expect(nodes[2]).toMatchObject({ gateId: '1:compile@8', tone: 'ok' });
  });
  it('a model.request with no answer is still shown (a call that never returned is evidence too)', () => {
    expect(buildTree([rec(1, 'model.request', { unit_id: 'u', attempt_number: 1, role: 'developer', call_seq: 4 })]).units[0]!.attempts[0]!.nodes.map((n) => n.label)).toEqual(['model call #4 (developer)']);
  });
  it('a gate result without a recorded outcome says so instead of passing', () => {
    expect(buildTree([rec(1, 'gate.result', { unit_id: 'u', attempt_number: 1, payload: { gate: 'tests' } })]).units[0]!.attempts[0]!.nodes[0]!.label).toMatch(/result not recorded/);
  });
  it('clicking a node selects unit, attempt, event and gate in one dispatch and shows exactly what was recorded', async () => {
    const { store } = renderModule(timelineModule, { runId: 'fx-greenfield-success' });
    const tree = await screen.findByRole('tree', { name: 'Execution timeline' });
    fireEvent.click(within(tree).getByRole('button', { name: /compile passed/ }));
    expect(store.getState()).toMatchObject({ unitId: 'unit-1', attempt: '1', gateId: expect.stringMatching(/^1:compile@\d+$/) });
    expect(await screen.findByLabelText('Selected record')).toHaveTextContent('"gate": "compile"');
  });
});
