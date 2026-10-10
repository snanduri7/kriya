import type { KupConsistency, KupSource } from '../../model/kup';

/** Where a value came from. Composed responses keep this: operation, authoritative source, schema version, consistency, request identity. */
export interface Provenance {
  operation: string;
  source: KupSource | null;
  consistency: KupConsistency | null;
  schemaVersion: number;
  requestId: string;
  observedAt: string;
}

/** A pointer to one recorded fact; what a module hands to the inspector or the drawer instead of copying evidence. */
export interface EvidenceReference {
  runId: string;
  /** Attempt-evidence record seq. */
  seq?: number;
  kind?: string;
  /** A blob reference (sha256:...); content is read lazily through the provider, never held by the reference. */
  blobRef?: string;
  /** The verified chain head the reference was observed under. */
  headDigest?: string | null;
}
