/**
 * Reads a run's recorded gates and staged changes for the Project page, through the same evidence client the run documents use - but WITHOUT touching the selection.
 * The approval panel's diff-viewed logic depends on which run the person selected, and that must not change as a side effect of showing a summary; so these are
 * independent reads of one live run (while it waits for review) or one finished trace row (its outcome).
 */
import { useCallback } from 'react';
import { toAvailability, useAsync } from './async';
import type { ApplicationOutcome } from '../../model/kup';
import type { Availability } from './availability';
import { useProvider, useSession } from './context';
import { loadLiveRunEvidence, loadRunEvidence, type RunEvidence } from './evidenceClient';
import { changeFacts, validationFacts, type ChangeFact, type GateFact } from './runFacts';

export type FactsState =
  | { state: 'loading' }
  | { state: 'unavailable'; detail: string }
  | { state: 'ready'; gates: GateFact[]; changes: ChangeFact[] };

function toFacts(a: Availability<RunEvidence>): FactsState {
  switch (a.state) {
    case 'ready':
    case 'degraded': return { state: 'ready', gates: validationFacts(a.data.records), changes: changeFacts(a.data.records) };
    case 'loading': return { state: 'loading' };
    case 'error': return { state: 'unavailable', detail: `${a.message} (${a.code})` };
    default: return { state: 'unavailable', detail: a.detail };
  }
}

/** The recorded facts of a run that is still executing (its live evidence store). `version` re-reads when a different change comes up for review. */
export function useLiveRunFacts(launchId: string | null, version: string): FactsState | null {
  const provider = useProvider();
  const load = useCallback(async (signal: AbortSignal) => loadLiveRunEvidence(provider, launchId!, signal), [provider, launchId]);
  const a = useAsync<RunEvidence>(launchId === null ? null : `facts:live:${launchId}:${version}`, load, { state: 'loading' });
  return launchId === null ? null : toFacts(a);
}

/** The recorded facts of a finished run (its trace row), read from the pinned snapshot once the refreshed snapshot is available. */
export function useRunRowFacts(runId: string | null, ready: boolean): FactsState | null {
  const provider = useProvider();
  const snapshotId = useSession().snapshotId;
  const load = useCallback(async (signal: AbortSignal) => loadRunEvidence(provider, runId!, snapshotId, signal), [provider, runId, snapshotId]);
  const a = useAsync<RunEvidence>(runId === null || !ready || snapshotId === null ? null : `facts:row:${runId}:${snapshotId}`, load, { state: 'loading' });
  return runId === null ? null : toFacts(a);
}

/**
 * What Kriya recorded about EXECUTING the application a finished run built (a read-only projection of the runtime-gate record, from the pinned snapshot). Null while the
 * snapshot is not yet available or the run has no trace row; an older Kriya with no projection yields NOT_APPLICABLE-like absence (the data is simply missing).
 */
export function useApplicationOutcome(runId: string | null, ready: boolean): Availability<ApplicationOutcome | null> | null {
  const provider = useProvider();
  const snapshotId = useSession().snapshotId;
  const a = useAsync<ApplicationOutcome | null>(runId === null || !ready || snapshotId === null ? null : `app-outcome:${runId}:${snapshotId}`,
    async (signal) => {
      const detail = toAvailability(await provider.runDetail(snapshotId!, runId!, signal));
      if (detail.state !== 'ready' && detail.state !== 'degraded') return detail as Availability<ApplicationOutcome | null>;
      const section = detail.data.application_outcome;
      return { ...detail, data: section && section.availability === 'recorded' ? (section.data ?? null) : null };
    }, { state: 'loading' });
  return runId === null ? null : a;
}
