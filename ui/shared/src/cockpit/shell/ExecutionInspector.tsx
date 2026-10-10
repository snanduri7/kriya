import type { Resolved } from '../core';
import { ModuleHost } from './ModuleHost';

export function ExecutionInspector({ modules }: { modules: readonly Resolved[] }) {
  return <aside className="ck-inspector" aria-label="Execution inspector">{modules.map((m) => <ModuleHost key={m.def.id} resolved={m} />)}</aside>;
}
