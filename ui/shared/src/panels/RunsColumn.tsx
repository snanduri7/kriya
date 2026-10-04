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
}

export function statusClass(status: string | null): string {
  switch (status) {
    case 'SUCCESS': return 'st-success';
    case 'FAILED': return 'st-failed';
    case 'NEEDS_REVIEW': return 'st-review';
    default: return 'st-unknown';
  }
}

export function RunsColumn({ runs, selectedRunId, onSelect, onLoadMore, pending, height }: RunsColumnProps) {
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
        emptyText={pending ? 'loading…' : 'no runs recorded in this store'}
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
