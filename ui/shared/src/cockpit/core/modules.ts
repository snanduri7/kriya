/**
 * Compile-time application modules. A module is a small declaration plus a component; there is no plugin lifecycle and no dynamic
 * loading. Modules depend only on core contracts (and the provider); they never import one another. They coordinate through the
 * shared SelectionStore and the capability/evidence contracts, and a module can read only the selection fields it declares.
 */
import type { ComponentType } from 'react';
import type { Selection } from '../../state/selection';
import type { Capability, CapabilityRegistry } from './capability';

/** Where a module renders. `page` modules fill a whole rail destination; the others compose the run workspace of their page. */
export type Surface = 'page' | 'secondary' | 'header' | 'center-tab' | 'inspector' | 'drawer-tab';
export const SURFACES: readonly Surface[] = ['page', 'secondary', 'header', 'center-tab', 'inspector', 'drawer-tab'];

export type SelectionField = keyof Selection;

export interface Placement {
  page: string;
  surface: Surface;
  /** Tab / rail / drawer label. */
  label: string;
  order: number;
}

export interface ModuleDefinition {
  id: string;
  placement: Placement;
  /** Absent => the module is hidden, or (whenUnavailable: 'explain') shows the explicit unavailable state naming what is missing. */
  requiredCapabilities: readonly Capability[];
  /** Absent => the module degrades; it must still render. */
  optionalCapabilities: readonly Capability[];
  /** The only selection fields this module may read (enforced at runtime by useModuleSelection). */
  selectionDependencies: readonly SelectionField[];
  whenUnavailable?: 'hide' | 'explain';
  render: ComponentType;
}

/** A rail destination. The composition root lists them; adding a module never changes this list. */
export interface PageDefinition {
  id: string;
  label: string;
  order: number;
  layout: 'run' | 'single';
  /** A single glyph for the rail; the label shows on hover/focus (title) and is the accessible name. */
  icon?: string;
}

export type Resolved =
  | { def: ModuleDefinition; state: 'visible' }
  | { def: ModuleDefinition; state: 'unavailable'; missing: Capability[] };

export interface ModuleRegistry {
  readonly modules: readonly ModuleDefinition[];
  readonly pages: readonly PageDefinition[];
  /** The modules of one page and surface, ordered, resolved against the capabilities. Hidden ones are absent from the result. */
  resolve(page: string, surface: Surface, capabilities: CapabilityRegistry): Resolved[];
  /** Pages with at least one module that is visible or explains itself (hidden destinations have no rail slot). */
  visiblePages(capabilities: CapabilityRegistry): PageDefinition[];
  byId(id: string): ModuleDefinition | undefined;
}

export interface RegistryOptions {
  pages: readonly PageDefinition[];
  /** Module ids switched off (a build or test decision). Switching a module off changes nothing else. */
  disabled?: readonly string[];
}

const CAPABILITY_RE = /^(op:)?[A-Za-z][A-Za-z0-9_.]*$/;

export function createModuleRegistry(definitions: readonly ModuleDefinition[], options: RegistryOptions): ModuleRegistry {
  const disabled = new Set(options.disabled ?? []);
  const pageIds = new Set(options.pages.map((p) => p.id));
  if (pageIds.size !== options.pages.length) throw new Error('duplicate page id');
  const seen = new Set<string>();
  for (const def of definitions) {
    if (seen.has(def.id)) throw new Error(`duplicate module id ${def.id}`);
    seen.add(def.id);
    if (!pageIds.has(def.placement.page)) throw new Error(`module ${def.id} is placed on unknown page ${def.placement.page}`);
    if (!SURFACES.includes(def.placement.surface)) throw new Error(`module ${def.id} has unknown surface ${def.placement.surface}`);
    for (const c of [...def.requiredCapabilities, ...def.optionalCapabilities]) if (!CAPABILITY_RE.test(c)) throw new Error(`module ${def.id} declares a malformed capability ${JSON.stringify(c)}`);
  }
  const modules = definitions.filter((d) => !disabled.has(d.id));
  const ordered = (list: ModuleDefinition[]) => [...list].sort((a, b) => a.placement.order - b.placement.order || a.id.localeCompare(b.id));

  const resolve = (page: string, surface: Surface, caps: CapabilityRegistry): Resolved[] => {
    const out: Resolved[] = [];
    for (const def of ordered(modules.filter((m) => m.placement.page === page && m.placement.surface === surface))) {
      const missing = caps.missing(def.requiredCapabilities);
      if (!missing.length) out.push({ def, state: 'visible' });
      else if (def.whenUnavailable === 'explain') out.push({ def, state: 'unavailable', missing });
    }
    return out;
  };
  return {
    modules,
    pages: options.pages,
    resolve,
    visiblePages: (caps) => [...options.pages].sort((a, b) => a.order - b.order).filter((p) => modules.some((m) => m.placement.page === p.id && (caps.missing(m.requiredCapabilities).length === 0 || m.whenUnavailable === 'explain'))),
    byId: (id) => modules.find((m) => m.id === id),
  };
}
