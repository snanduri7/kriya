import type { CapabilityRegistry } from './capability';
import type { ProviderKind } from './provider';

/**
 * Authoritative UI-facing facts about the selected run, and nothing inferred. Greenfield and brownfield are not modes of the UI:
 * they show up as the recorded baseline identity here and as the recorded per-file change semantics inside the Changes module.
 * `changeType` stays null because Kriya does not record a greenfield/brownfield classification; the UI never guesses one
 * (no assumption that a baseline commit exists, that files or tests pre-existed, or that Git HEAD is the workspace baseline).
 */
export interface RunContext {
  runId: string;
  snapshotId: string | null;
  provider: ProviderKind;
  /** The stored `goal` column, verbatim. For a unit row this is the COMPILED prompt: never a title. */
  goal: string | null;
  /** The ORIGINAL user request (the backend's display_goal, resolved from structured fields); null when none is recorded. This - never `goal` - is what a person is shown. */
  displayGoal: string | null;
  status: string | null;
  /** The trace row's stored failure_category, verbatim; null when none was stored. */
  failureCategory: string | null;
  /** The trace row's `attempts` column, verbatim. It is the workflow's RETRY counter (retries + best-of-N candidates), NOT the number of attempts made: a run that made one attempt and stopped at human approval stores 0. Shown only as stored, with that explanation; the attempt count is derived from the run's evidence. */
  storedAttempts: number | null;
  milestone: { groupId: string | null; index: number | null; total: number | null };
  workspace: { identity: string | null; path: string | null };
  baseline: {
    /** base_revision / base_tree_hash exactly as the run manifest recorded them; null = not recorded (never "HEAD"). */
    revision: string | null;
    treeHash: string | null;
    source: 'recorded' | 'not_recorded';
  };
  changeType: null;
  capabilities: CapabilityRegistry;
  evidence: {
    store: 'present' | 'absent';
    captureMode: string | null;
    sealed: boolean | null;
    verification: string | null;
    consistency: string | null;
  };
}
