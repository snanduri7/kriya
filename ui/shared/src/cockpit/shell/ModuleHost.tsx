import { Component, type ReactNode } from 'react';
import { AvailabilityView, ModuleScope, type ModuleDefinition, type Resolved } from '../core';

class Boundary extends Component<{ id: string; children: ReactNode }, { error: string | null }> {
  override state = { error: null as string | null };
  static getDerivedStateFromError(error: unknown) { return { error: error instanceof Error ? error.message : String(error) }; }
  override render() {
    return this.state.error === null ? this.props.children : <div className="ck-state" role="alert"><strong>Module {this.props.id} failed</strong>{this.state.error}</div>;
  }
}

/** Mounts one resolved module inside its selection scope and an error boundary: one module failing never takes the shell down. */
export function ModuleHost({ resolved }: { resolved: Resolved }) {
  const { def } = resolved;
  if (resolved.state === 'unavailable') {
    return <AvailabilityView value={{ state: 'unavailable', reason: 'capability_absent', detail: `${def.placement.label} needs: ${resolved.missing.join(', ')}` }}>{() => null}</AvailabilityView>;
  }
  const Render = def.render;
  return <ModuleScope def={def}><Boundary key={def.id} id={def.id}><section aria-label={def.placement.label} data-module={def.id}><Render /></section></Boundary></ModuleScope>;
}

export const labelOf = (def: ModuleDefinition) => def.placement.label;
