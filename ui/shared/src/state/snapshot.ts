/**
 * Pinned-snapshot session state (gate C-1, C-2, C-3). Lives ONLY here so every host shares one rule set:
 * - every history request carries the pinned snapshot id; the newest snapshot is never selected implicitly;
 * - "Acquire new snapshot" is an explicit action distinct from "Refresh displayed snapshot" (a refresh never acquires);
 * - switching the pinned snapshot invalidates prior responses and cursors (the caller bumps request generations and
 *   clears the list/detail/prompt slots);
 * - labels: "snapshot acquired at …" and "source metadata change detected"; metadata equality is never shown as
 *   unchanged/current/latest, and the snapshot never implies live run status.
 */
import type { Consistency, SnapshotSummary } from '../model/kup';

export interface SnapshotSession {
  pinnedId: string | null;
  /** How the pin was established: acquired by this session, or chosen from the published list. */
  pinnedBy: 'acquired' | 'chosen' | null;
  available: SnapshotSummary[];
}

export const initialSnapshotSession: SnapshotSession = { pinnedId: null, pinnedBy: null, available: [] };

export type SnapshotAction =
  | { type: 'acquired'; summary: SnapshotSummary }
  | { type: 'choose'; snapshotId: string }
  | { type: 'listed'; snapshots: SnapshotSummary[] }
  | { type: 'unavailable' };

/** Returns the next state and whether the pin CHANGED (callers invalidate responses and cursors on true). */
export function snapshotReducer(state: SnapshotSession, action: SnapshotAction): { state: SnapshotSession; pinChanged: boolean } {
  switch (action.type) {
    case 'acquired': {
      const available = [action.summary, ...state.available.filter((s) => s.snapshot_id !== action.summary.snapshot_id)];
      return { state: { pinnedId: action.summary.snapshot_id, pinnedBy: 'acquired', available }, pinChanged: action.summary.snapshot_id !== state.pinnedId };
    }
    case 'choose':
      if (!state.available.some((s) => s.snapshot_id === action.snapshotId)) return { state, pinChanged: false };
      return { state: { ...state, pinnedId: action.snapshotId, pinnedBy: 'chosen' }, pinChanged: action.snapshotId !== state.pinnedId };
    case 'listed':
      // Listing never moves the pin (gate C-2): it only updates what the user may choose from.
      return { state: { ...state, available: action.snapshots }, pinChanged: false };
    case 'unavailable':
      return { state: { ...state, pinnedId: null, pinnedBy: null }, pinChanged: state.pinnedId !== null };
    default:
      return { state, pinChanged: false };
  }
}

export interface FreshnessLabel {
  headline: string; // "snapshot acquired at …" or "no snapshot"
  metadata: 'change_detected' | 'no_change_detected' | 'unknown';
  metadataText: string;
}

/** Honest labels (gate C-3). */
export function freshnessLabel(consistency: Consistency | null | undefined, summary?: SnapshotSummary | null): FreshnessLabel {
  const acquiredAt = consistency?.acquisition_completed_at ?? summary?.acquisition_completed_at ?? null;
  const changed = consistency?.source_metadata_changed ?? summary?.source_metadata_changed ?? null;
  const headline = acquiredAt ? `snapshot acquired at ${acquiredAt}` : 'no snapshot displayed';
  if (changed === true) return { headline, metadata: 'change_detected', metadataText: 'source metadata change detected since acquisition' };
  if (changed === false) return { headline, metadata: 'no_change_detected', metadataText: 'no source metadata change detected (not a freshness guarantee)' };
  return { headline, metadata: 'unknown', metadataText: 'source metadata not observed' };
}
