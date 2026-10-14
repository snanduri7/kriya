import type { RunSummary } from '../../../model/kup';

/** Client-side filtering of the rows already loaded for the list. It reads nothing more (no evidence store is opened to decide a filter) and never hides what it cannot place:
 * a row with no recorded time is reported by the caller, not silently dropped or kept. */
export type StatusFilter = 'all' | 'success' | 'failure' | 'unfinished' | 'not_recorded';
export type RangeFilter = 'any' | 'today' | '7d' | '30d';
export interface RunFilter { query: string; status: StatusFilter; range: RangeFilter }
export const NO_FILTER: RunFilter = { query: '', status: 'all', range: 'any' };

export const isFiltering = (f: RunFilter): boolean => f.query.trim() !== '' || f.status !== 'all' || f.range !== 'any';

/** The row's status as a filter value. Kriya's own words: success / failure / in_progress (shown "Unfinished"); anything else is "not recorded". */
export function statusOf(run: Pick<RunSummary, 'status'>): Exclude<StatusFilter, 'all'> {
  switch (run.status) {
    case 'success': return 'success';
    case 'failure': return 'failure';
    case 'in_progress': return 'unfinished';
    default: return 'not_recorded';
  }
}

/** The time a stored timestamp ("YYYY-MM-DD HH:MM:SS" or ISO) denotes, as epoch ms, or null when it is absent or unparseable. A bare timestamp is local time, as the trace store writes it. */
export function timeOf(run: Pick<RunSummary, 'timestamp'>): number | null {
  const raw = run.timestamp;
  if (typeof raw !== 'string' || raw.trim() === '') return null;
  const ms = Date.parse(raw.includes('T') || raw.includes('Z') ? raw : raw.replace(' ', 'T'));
  return Number.isFinite(ms) ? ms : null;
}

const DAY = 24 * 60 * 60 * 1000;
function since(range: RangeFilter, now: number): number | null {
  if (range === 'any') return null;
  if (range === 'today') { const d = new Date(now); d.setHours(0, 0, 0, 0); return d.getTime(); }
  return now - (range === '7d' ? 7 : 30) * DAY;
}

export interface Filtered { runs: RunSummary[]; /** rows a date range could not place (no recorded time); never counted as matches */ untimed: number }

export function filterRuns(runs: readonly RunSummary[], f: RunFilter, now: number): Filtered {
  const needle = f.query.trim().toLowerCase();
  const from = since(f.range, now);
  let untimed = 0;
  const out: RunSummary[] = [];
  for (const run of runs) {
    if (needle && !`${run.display_goal ?? ''} ${run.status ?? ''} ${run.run_id}`.toLowerCase().includes(needle)) continue;
    if (f.status !== 'all' && statusOf(run) !== f.status) continue;
    if (from !== null) {
      const t = timeOf(run);
      if (t === null) { untimed += 1; continue; }
      if (t < from || t > now + DAY) continue; // (a timestamp far in the future is not "recent")
    }
    out.push(run);
  }
  return { runs: out, untimed };
}
