/**
 * Availability<T>: the one way a module learns whether it has something to show. Every state a user can be in is distinct and
 * never collapses into a generic "no data": a capability the build lacks, a store that was pruned, content the capture mode
 * never kept, content policy denied, a store that does not verify, and a plain error are different facts with different text.
 */
import type { Provenance } from './evidence';

export type UnavailableReason =
  | 'capability_absent' // the build does not advertise what this needs
  | 'store_absent' // no attempt-evidence store for the run (pruned / capture off / pre-recorder / unknown: see detail)
  | 'pruned' | 'capture_off' | 'pre_recorder' | 'recorder_unavailable'
  | 'digest_only' // the store keeps digests, not content
  | 'denied' // content visibility policy refuses
  | 'unverified' // the store does not verify; nothing is served from it
  | 'unknown_run' // no run selected
  | 'pending'; // this module's implementation is not part of this build yet

export type Availability<T> =
  | { state: 'loading' }
  | { state: 'ready'; data: T; provenance: Provenance }
  /** Data is served but something about it limits trust (an unsealed store, a partial tail). `reasons` are shown verbatim. */
  | { state: 'degraded'; data: T; provenance: Provenance; reasons: readonly string[] }
  /** `detail` is the semantic explanation shown by default; `rawDetail` is the exact recorded text (paths and all), shown on request. */
  | { state: 'unavailable'; reason: UnavailableReason; detail: string; rawDetail?: string }
  | { state: 'not_recorded'; detail: string }
  /** `message` is the typed, human-readable reason; `detail` is raw technical text (a child's stderr tail), shown only behind a disclosure. */
  | { state: 'error'; code: string; message: string; detail?: string };

export const loading = <T,>(): Availability<T> => ({ state: 'loading' });
export const unavailable = <T,>(reason: UnavailableReason, detail: string, rawDetail?: string): Availability<T> => ({ state: 'unavailable', reason, detail, ...(rawDetail !== undefined ? { rawDetail } : {}) });
export const notRecorded = <T,>(detail: string): Availability<T> => ({ state: 'not_recorded', detail });
export const failed = <T,>(code: string, message: string, detail?: string): Availability<T> => ({ state: 'error', code, message, ...(detail ? { detail } : {}) });

export function hasData<T>(a: Availability<T>): a is Extract<Availability<T>, { data: T }> {
  return a.state === 'ready' || a.state === 'degraded';
}

/** Stable, human-readable label of a state (used by the AvailabilityView and by tests; never the word "empty"). */
export function availabilityTitle<T>(a: Availability<T>): string {
  switch (a.state) {
    case 'loading': return 'Loading';
    case 'ready': return 'Recorded';
    case 'degraded': return 'Recorded, with limits';
    case 'not_recorded': return 'Not recorded';
    case 'error': return `Error ${a.code}`;
    case 'unavailable':
      switch (a.reason) {
        case 'capability_absent': return 'Not supported by this Kriya';
        case 'store_absent': return 'No evidence store';
        case 'pruned': return 'Evidence pruned by retention';
        case 'capture_off': return 'Capture was off';
        case 'pre_recorder': return 'Recorded before the recorder';
        case 'recorder_unavailable': return 'Recorder was unavailable';
        case 'digest_only': return 'Digest only';
        case 'denied': return 'Content not authorized';
        case 'unverified': return 'Store does not verify';
        case 'unknown_run': return 'No run selected';
        case 'pending': return 'Not in this build yet';
      }
  }
}
