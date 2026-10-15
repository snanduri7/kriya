import { useCallback, useState } from 'react';
import { useAsync } from './async';
import { activityEntries, resultsFromRecords, type ActivityEntry } from './activity';
import { useProvider, useSession } from './context';
import { loadLiveRunEvidence, loadRunEvidence, type RunEvidence } from './evidenceClient';
import { useExecution } from './execution';
import type { Availability } from './availability';

type Tab = 'activity' | 'build' | 'tests';
const TABS: ReadonlyArray<{ id: Tab; label: string }> = [{ id: 'activity', label: 'Activity' }, { id: 'build', label: 'Build' }, { id: 'tests', label: 'Tests' }];
const MARK = { info: '•', ok: '✓', bad: '✕', wait: '⚠' } as const;
const clock = (iso: string | null): string => (iso && /T(\d\d:\d\d:\d\d)/.test(iso) ? iso.match(/T(\d\d:\d\d:\d\d)/)![1]! : '');

/** The records of the run being followed: its live evidence store while it executes, its trace row's store once it ended. Re-read when a new record arrives (`lastSeq`). */
export function useRunRecords(launchId: string, traceRunId: string | null, finished: boolean, snapshotReady: boolean, lastSeq: number): Availability<RunEvidence> | null {
  const provider = useProvider();
  const snapshotId = useSession().snapshotId;
  const load = useCallback(async (signal: AbortSignal) => (finished && traceRunId ? loadRunEvidence(provider, traceRunId, snapshotId, signal) : loadLiveRunEvidence(provider, launchId, signal)), [provider, launchId, traceRunId, finished, snapshotId]);
  const key = finished && traceRunId ? (snapshotReady && snapshotId ? `activity:row:${traceRunId}:${snapshotId}` : null) : `activity:live:${launchId}:${lastSeq}`;
  const a = useAsync<RunEvidence>(key, load, { state: 'loading' });
  return key === null ? null : a;
}

/**
 * Activity drawer (Demo V2): what Kriya did, in plain words, from RECORDED facts only (activity.ts). Tabs: Activity (default), Build, Tests. There is deliberately no Technical Log tab: no safe,
 * bounded, redacted projection of process output exists yet, and a log tail through the renderer is not built (deferred; the technical evidence stays under View Details). A lines with no
 * record behind it does not exist here.
 */
export function ActivityDrawer({ launchId, onClose }: { launchId: string; onClose(): void }) {
  const ex = useExecution();
  const run = ex.run;
  const [tab, setTab] = useState<Tab>('activity');
  const finished = run?.stage === 'finished';
  const records = useRunRecords(launchId, run?.outcome?.traceRunId ?? null, finished, run?.timing.refreshedAt != null, run?.progress.lastSeq ?? 0);
  const data = records && (records.state === 'ready' || records.state === 'degraded') ? records.data.records : null;
  const entries: ActivityEntry[] = activityEntries(data ?? [], run?.status ?? null);
  return (
    <aside className="ck-activity" role="complementary" aria-label="Activity" data-testid="activity-drawer">
      <div className="ck-runbar-head">
        <div role="tablist" aria-label="Activity views">
          {TABS.map((t) => <button key={t.id} type="button" role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'ck-primary' : ''} onClick={() => setTab(t.id)}>{t.label}</button>)}
        </div>
        <span className="grow" />
        <button type="button" onClick={onClose}>Hide Activity</button>
      </div>
      {tab === 'activity' ? (
        entries.length === 0 ? <p className="muted" role="status">{records && records.state === 'loading' ? 'Reading what Kriya recorded…' : 'Nothing has been recorded yet.'}</p>
          : <ol className="ck-human-timeline" aria-label="What happened" data-testid="activity-list">{entries.map((e) => <li key={e.key} data-tone={e.tone} title={`Recorded as ${e.source}`}><span className="mono small muted">{clock(e.at)}</span> <span aria-hidden="true">{MARK[e.tone]}</span> {e.text}</li>)}</ol>
      ) : null}
      {tab === 'build' || tab === 'tests' ? <RecordedResults records={data} family={tab} loading={records?.state === 'loading'} /> : null}
      {records && records.state !== 'loading' && data === null ? <p className="muted small" role="note">The records could not be read here{records.state === 'error' ? ` (${records.code})` : ''}. Use View Details.</p> : null}
    </aside>
  );
}

function RecordedResults({ records, family, loading }: { records: RunEvidence['records'] | null; family: 'build' | 'tests'; loading: boolean }) {
  const rows = records ? resultsFromRecords(records, family) : [];
  if (rows.length === 0) return <p className="muted" role="status">{loading ? 'Reading what Kriya recorded…' : family === 'build' ? 'No build result is recorded yet.' : 'No test result is recorded yet.'}</p>;
  return <ul aria-label={family === 'build' ? 'Build results' : 'Test results'} data-testid={`activity-${family}`}>{rows.map((r) => <li key={r.key} data-status={r.ok === true ? 'passed' : r.ok === false ? 'failed' : 'unknown'}><span aria-hidden="true">{r.ok === true ? '✓' : r.ok === false ? '✕' : '○'}</span> {r.text}{r.attempt !== null && r.attempt > 1 ? <span className="muted small"> · attempt {r.attempt}</span> : null}</li>)}</ul>;
}
