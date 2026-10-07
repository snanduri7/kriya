import { useMemo, useState } from 'react';
import type { RunSummary } from '../model/kup';
import { sanitizeText } from '../render/sanitize';
import { VirtualList } from './VirtualList';

export interface RunsColumnProps {
  runs: RunSummary[];
  selectedRunId: string | null;
  onSelect: (runId: string) => void;
  onLoadMore: (() => void) | null;
  pending: boolean;
  height: number;
  emptyText?: string;
}

/** Colour hints for the RECORDED run status values (runs.status is the literal string Kriya's workflow writes; the real
 * vocabulary is lowercase: success, failure, needs_review, approval_required, knowledge_gap, baseline_indeterminate,
 * planner_output_incomplete, planner_output_schema_invalid, ... - MEASURED on the owner's store 2026-10-05 and TRACED to
 * kriya/workflow/workflow.py). A hint is applied only to the four values whose meaning is explicit; every other value,
 * including any future or differently-cased one, stays neutral and is never classified as success or failure. */
export const STATUS_HINTS: Readonly<Record<string, 'st-success' | 'st-failed' | 'st-review'>> = Object.freeze({
  success: 'st-success', // the run completed and its changes were applied
  failure: 'st-failed', // a terminal failure (failure_category names the class)
  needs_review: 'st-review', // Kriya stopped for a human decision
  approval_required: 'st-review', // human approval was required and not available (changes not applied)
});
export function statusClass(status: string | null): string {
  return (status !== null && Object.prototype.hasOwnProperty.call(STATUS_HINTS, status) ? STATUS_HINTS[status] : null) ?? 'st-unknown';
}

export function RunsColumn({ runs, selectedRunId, onSelect, onLoadMore, pending, height, emptyText }: RunsColumnProps) {
  const [filter, setFilter] = useState('');
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter((r) => (r.goal ?? '').toLowerCase().includes(q) || (r.status ?? '').toLowerCase().includes(q) || r.run_id.toLowerCase().includes(q) || (r.failure_category ?? '').toLowerCase().includes(q));
  }, [runs, filter]);
  const selectedIndex = filtered.findIndex((r) => r.run_id === selectedRunId);
  return (
    <section className="runs" aria-label="Runs">
      <label className="filter">
        <span className="sr-only">Filter runs</span>
        <input type="search" placeholder="Filter by goal, status, id, category" value={filter} onChange={(e) => setFilter(e.target.value)} />
      </label>
      <VirtualList
        items={filtered}
        rowHeight={56}
        height={height}
        selectedIndex={selectedIndex >= 0 ? selectedIndex : null}
        onSelect={(i) => { const r = filtered[i]; if (r) onSelect(r.run_id); }}
        getKey={(r) => r.run_id}
        ariaLabel="Recorded runs"
        emptyText={pending ? 'loading…' : emptyText ?? 'no runs recorded in this snapshot'}
        renderRow={(r) => (
          <div className="runrow" title={r.goal ?? ''}>
            <div className="runrow-top">
              <span className={`status ${statusClass(r.status)}`}>{sanitizeText(r.status ?? 'unknown status')}</span>
              <span className="muted">{sanitizeText(r.timestamp ?? 'no timestamp')}</span>
            </div>
            <div className="runrow-goal">{sanitizeText(r.goal ?? '(no goal recorded)')}</div>
          </div>
        )}
      />
      <div className="runs-footer">
        <span className="muted">{filtered.length} of {runs.length} shown</span>
        {onLoadMore ? <button type="button" className="small" onClick={onLoadMore} disabled={pending}>Load more</button> : null}
      </div>
    </section>
  );
}
