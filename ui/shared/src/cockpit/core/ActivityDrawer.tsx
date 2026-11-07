import { useCallback } from 'react';
import { useAsync } from './async';
import { resultsFromRecords } from './activity';
import { useProvider, useSession } from './context';
import { loadLiveRunEvidence, loadRunEvidence, type RunEvidence } from './evidenceClient';
import type { Availability } from './availability';


/** The records of the run being followed: its live evidence store while it executes, its trace row's store once it ended. Re-read when a new record arrives (`lastSeq`). */
export function useRunRecords(launchId: string, traceRunId: string | null, finished: boolean, snapshotReady: boolean, lastSeq: number): Availability<RunEvidence> | null {
  const provider = useProvider();
  const snapshotId = useSession().snapshotId;
  const load = useCallback(async (signal: AbortSignal) => (finished && traceRunId ? loadRunEvidence(provider, traceRunId, snapshotId, signal) : loadLiveRunEvidence(provider, launchId, signal)), [provider, launchId, traceRunId, finished, snapshotId]);
  const key = finished && traceRunId ? (snapshotReady && snapshotId ? `activity:row:${traceRunId}:${snapshotId}` : null) : `activity:live:${launchId}:${lastSeq}`;
  const a = useAsync<RunEvidence>(key, load, { state: 'loading' });
  return key === null ? null : a;
}

export function RecordedResults({ records, family, loading }: { records: RunEvidence['records'] | null; family: 'build' | 'tests'; loading: boolean }) {
  const rows = records ? resultsFromRecords(records, family) : [];
  if (rows.length === 0) return <p className="muted" role="status">{loading ? 'Reading what Kriya recorded…' : family === 'build' ? 'No build result is recorded yet.' : 'No test result is recorded yet.'}</p>;
  return <ul aria-label={family === 'build' ? 'Build results' : 'Test results'} data-testid={`activity-${family}`}>{rows.map((r) => <li key={r.key} data-status={r.ok === true ? 'passed' : r.ok === false ? 'failed' : 'unknown'}><span aria-hidden="true">{r.ok === true ? '✓' : r.ok === false ? '✕' : '○'}</span> {r.text}{r.attempt !== null && r.attempt > 1 ? <span className="muted small"> · attempt {r.attempt}</span> : null}</li>)}</ul>;
}
