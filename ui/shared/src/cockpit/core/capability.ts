import type { Capabilities } from '../../model/kup';

/** A feature flag name (`capabilities.features[name] === true`) or `op:<operation>` (the operation is listed in `capabilities.operations`). */
export type Capability = string;

/** What this Kriya build advertises. Fail closed: no capabilities loaded, an unknown flag or a non-true value means absent. */
export class CapabilityRegistry {
  private readonly features: Record<string, unknown>;
  private readonly operations: ReadonlySet<string>;
  readonly capabilitySet: string | null;
  readonly loaded: boolean;

  constructor(capabilities: Capabilities | null) {
    this.loaded = capabilities !== null;
    this.features = (capabilities?.features ?? {}) as Record<string, unknown>;
    this.operations = new Set(capabilities?.operations ?? []);
    const set = this.features.capability_set;
    this.capabilitySet = typeof set === 'string' ? set : null;
  }

  has(capability: Capability): boolean {
    return capability.startsWith('op:') ? this.operations.has(capability.slice(3)) : this.features[capability] === true;
  }

  missing(required: readonly Capability[]): Capability[] {
    return required.filter((c) => !this.has(c));
  }

  /** A limit the build advertises (page sizes and the like); null when absent. */
  static limit(capabilities: Capabilities | null, name: string): number | null {
    const v = (capabilities?.limits as Record<string, unknown> | undefined)?.[name];
    return typeof v === 'number' ? v : null;
  }
}
