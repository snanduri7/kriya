import type { Resolved } from '../core';
import { ModuleHost } from './ModuleHost';

export function SecondaryPane({ modules }: { modules: readonly Resolved[] }) {
  return <aside className="ck-secondary" aria-label="Secondary pane">{modules.map((m) => <ModuleHost key={m.def.id} resolved={m} />)}</aside>;
}
