import { activityEntries, type ActivityEntry } from './activity';
import { useRunRecords } from './ActivityDrawer';
import type { RunView } from './execution';
import type { RunEvidence } from './evidenceClient';

/**
 * The activity lines of the run being followed - THE one event stream: the bottom Activity dock and the active-run surface's recent-activity preview both read it here (the same records, the same
 * mapping, the same shared cache entry - never a second event truth). ``state`` is the records' own read state (loading / degraded / ready / error).
 */
export function useRunActivity(run: RunView | null): { entries: ActivityEntry[]; records: ReturnType<typeof useRunRecords>; data: RunEvidence['records'] | null } {
  const finished = run?.stage === 'finished';
  const records = useRunRecords(run?.launchId ?? '', run?.outcome?.traceRunId ?? null, Boolean(finished), run?.timing.refreshedAt != null, run?.progress.lastSeq ?? 0);
  const data = run && records && (records.state === 'ready' || records.state === 'degraded') ? records.data.records : null;
  const entries: ActivityEntry[] = run ? activityEntries(data ?? [], run.status ?? null) : [];
  return { entries, records, data };
}
