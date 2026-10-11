import { useEffect, useRef, useState } from 'react';
import { failed, loading, type Availability } from './availability';
import type { ProviderResult } from './provider';

/** Raw technical text a host attached to an error (a child's stderr tail), or undefined. It is shown only behind a disclosure, never in the message. */
export const technicalDetail = (error: { extra: Record<string, unknown> }): string | undefined => (typeof error.extra.detail === 'string' ? error.extra.detail : undefined);

/** A provider result as an Availability (a typed error keeps its code and message). */
export function toAvailability<T>(result: ProviderResult<T>): Availability<T> {
  return result.ok ? { state: 'ready', data: result.data, provenance: result.provenance } : failed(result.error.code, result.error.message, technicalDetail(result.error));
}

/**
 * Run an async load whenever `key` changes; `key` null means "nothing to load" and yields `whenNull`. Stale answers are dropped
 * (the load is aborted, and an answer is shown only for the key that asked), and state is only set from the async callback.
 */
export function useAsync<T>(key: string | null, load: (signal: AbortSignal) => Promise<Availability<T>>, whenNull: Availability<T>): Availability<T> {
  const [done, setDone] = useState<{ key: string; value: Availability<T> } | null>(null);
  const latest = useRef(load);
  useEffect(() => { latest.current = load; });
  useEffect(() => {
    if (key === null) return undefined;
    const ac = new AbortController();
    void latest.current(ac.signal).then((value) => { if (!ac.signal.aborted) setDone({ key, value }); });
    return () => ac.abort();
  }, [key]);
  if (key === null) return whenNull;
  return done?.key === key ? done.value : loading();
}
