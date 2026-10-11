import { toAvailability, unavailable, useAsync, useProvider, type Availability } from '../../core';
import type { ExecutionContext, ExecutionModelProbe } from '../../../model/kup';

/**
 * What a run here would have available, from Kriya: Skills, tools and per-role routing (`execution.context`, configuration/file-derived, starts nothing) and
 * whether the configured model server answers (`execution.model_probe`, one bounded local GET). Both are asked again whenever `nonce` or the workspace changes; an
 * answer for an earlier workspace/nonce is never shown (useAsync keys every answer and aborts the superseded request).
 */
export function useRunSetup(workspace: string | null, nonce: number): { context: Availability<ExecutionContext>; probe: Availability<ExecutionModelProbe> } {
  const provider = useProvider();
  const none = <T,>(): Availability<T> => unavailable('unknown_run', 'No workspace is open.');
  const context = useAsync<ExecutionContext>(workspace === null ? null : `ctx:${workspace}:${nonce}`, async (signal) => toAvailability(await provider.executionContext(workspace!, signal)), none());
  const probe = useAsync<ExecutionModelProbe>(workspace === null ? null : `probe:${workspace}:${nonce}`, async (signal) => toAvailability(await provider.executionModelProbe(workspace!, signal)), none());
  return { context, probe };
}
