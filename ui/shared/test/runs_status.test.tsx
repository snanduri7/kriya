/** Run status rendering (production-data fidelity batch, 2026-10-05): runs.status is shown literally; a colour hint is applied
 * only to the four values whose meaning is explicit; every other recorded value is neutral and never read as success or
 * failure. The vocabulary is the one MEASURED on the owner's store: lowercase. */
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { RunsColumn, STATUS_HINTS, statusClass } from '../src/panels/RunsColumn';
import type { RunSummary } from '../src/model/kup';

const run = (id: string, status: string | null, failure_category: string | null = null): RunSummary => ({ run_id: id, timestamp: '2026-10-02 18:04:49', goal: `goal ${id}`, duration_sec: 1, attempts: 1, status, failure_category, files_modified: null });
const REAL = ['success', 'failure', 'approval_required', 'needs_review', 'planner_output_incomplete', 'planner_output_schema_invalid'];

describe('statusClass: explicit hints only', () => {
  it('hints exactly success, failure, needs_review and approval_required; everything else is neutral', () => {
    expect(Object.keys(STATUS_HINTS).sort()).toEqual(['approval_required', 'failure', 'needs_review', 'success']);
    expect(statusClass('success')).toBe('st-success');
    expect(statusClass('failure')).toBe('st-failed');
    expect(statusClass('needs_review')).toBe('st-review');
    expect(statusClass('approval_required')).toBe('st-review');
    for (const s of ['planner_output_incomplete', 'planner_output_schema_invalid', 'knowledge_gap', 'baseline_indeterminate', 'in_progress', 'error', 'SUCCESS', 'FAILED', 'Success', '', 'constructor', '__proto__', null]) expect(statusClass(s), String(s)).toBe('st-unknown');
  });
});

describe('RunsColumn shows the recorded status text literally', () => {
  it('renders every real vocabulary value as text, hints only the supported ones, and shows the raw string for an unknown status', () => {
    const runs = [...REAL.map((s, i) => run(`r${i}`, s, s === 'failure' ? 'quality_gate_failed' : null)), run('r9', 'PARTIALLY_SETTLED_v9'), run('r10', null)];
    render(<RunsColumn runs={runs} selectedRunId={null} onSelect={() => undefined} onLoadMore={null} pending={false} height={600} />);
    const list = screen.getByRole('listbox', { name: 'Recorded runs' });
    for (const s of [...REAL, 'PARTIALLY_SETTLED_v9']) expect(within(list).getByText(s)).toBeInTheDocument(); // literal, exactly as recorded
    expect(within(list).getByText('unknown status')).toBeInTheDocument(); // null is said in words
    const hinted = within(list).getAllByText(/./, { selector: '.status' }).map((el) => [el.textContent, el.className]);
    expect(hinted.filter(([, c]) => c!.includes('st-success')).map(([t]) => t)).toEqual(['success']);
    expect(hinted.filter(([, c]) => c!.includes('st-failed')).map(([t]) => t)).toEqual(['failure']);
    expect(hinted.filter(([, c]) => c!.includes('st-review')).map(([t]) => t).sort()).toEqual(['approval_required', 'needs_review']);
    expect(hinted.filter(([, c]) => c!.includes('st-unknown')).map(([t]) => t).sort()).toEqual(['PARTIALLY_SETTLED_v9', 'planner_output_incomplete', 'planner_output_schema_invalid', 'unknown status']);
    expect(screen.getByText('8 of 8 shown')).toBeInTheDocument();
  });
  it('the filter matches the literal status text', () => {
    const runs = REAL.map((s, i) => run(`r${i}`, s));
    render(<RunsColumn runs={runs} selectedRunId={null} onSelect={() => undefined} onLoadMore={null} pending={false} height={600} />);
    const box = screen.getByRole('searchbox');
    fireEvent.change(box, { target: { value: 'planner_output' } });
    expect(within(screen.getByRole('listbox', { name: 'Recorded runs' })).getAllByRole('option')).toHaveLength(2);
    expect(screen.getByText('2 of 6 shown')).toBeInTheDocument();
  });
});
