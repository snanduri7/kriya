/** Test infrastructure: mount one module (or the whole shell) over the deterministic fixture provider. Never imported by product code. */
import { render } from '@testing-library/react';
import { initialSelection, type Selection } from '../../state/selection';
import { createFixtureProvider, FixtureHost, type FixtureHostOptions } from '../providers/fixture';
import { HostProvider } from '../providers/hostProvider';
import { CockpitProviders, ModuleScope, createModuleRegistry, createSelectionStore, gateRows, loadRunEvidence, type ModuleDefinition, type ModuleRegistry, type SelectionStore, type UIProvider } from '../core';
import { FIXTURE_SNAPSHOT_ID } from '../providers/fixture';
import { AppShell } from '../shell/AppShell';
import { createCockpitRegistry } from '../composition';

export interface Mounted { provider: UIProvider; store: SelectionStore }

export function renderModule(def: ModuleDefinition, opts: { runId?: string | null; selection?: Partial<Selection>; fixture?: FixtureHostOptions; provider?: UIProvider } = {}): Mounted & { unmount(): void } {
  const provider = opts.provider ?? createFixtureProvider(opts.fixture);
  const store = createSelectionStore({ ...initialSelection, runId: opts.runId ?? null, ...opts.selection });
  const registry = createModuleRegistry([def], { pages: [{ id: def.placement.page, label: 'test', order: 1, layout: 'single' }] });
  const view = render(<CockpitProviders services={{ provider, selection: store, registry }}><ModuleScope def={def}><def.render /></ModuleScope></CockpitProviders>);
  return { provider, store, unmount: view.unmount };
}

/** The selection key of the n-th recorded gate of `type` in a fixture run (tests select by what is recorded, not by a hand-counted seq). */
export async function gateIdOf(trace: string, type: string, provider: UIProvider = createFixtureProvider(), nth = 0): Promise<string> {
  const a = await loadRunEvidence(provider, trace, FIXTURE_SNAPSHOT_ID);
  if (a.state !== 'ready' && a.state !== 'degraded') throw new Error(`no evidence for ${trace}`);
  const row = gateRows(a.data.records).filter((g) => g.type === type)[nth];
  if (!row) throw new Error(`no ${type} gate in ${trace}`);
  return row.id;
}

export function renderShell(opts: { registry?: ModuleRegistry; runId?: string | null; selection?: Partial<Selection>; fixture?: FixtureHostOptions; width?: number } = {}): Mounted {
  const provider = createFixtureProvider(opts.fixture);
  const store = createSelectionStore({ ...initialSelection, runId: opts.runId ?? null, ...opts.selection });
  render(<AppShell provider={provider} registry={opts.registry ?? createCockpitRegistry()} selection={store} viewportWidth={opts.width ?? 1440} />);
  return { provider, store };
}

/** The shell over a fixture host the test can see into: the scripted native chooser, the remembered list, the calls the host received and the menu. */
/** Polling fast enough for a test to see every scripted frame. */
export const FAST_POLL = { visibleMs: 15, slowMs: 15, hiddenMs: 15, slowAfterMs: 1_000_000 } as const;

export function renderWorkspaceShell(opts: { fixture?: FixtureHostOptions; registry?: ModuleRegistry; width?: number } = {}): Mounted & { host: FixtureHost } {
  const host = new FixtureHost(opts.fixture);
  const provider = new HostProvider(host, 'fixture');
  const store = createSelectionStore({ ...initialSelection });
  render(<AppShell provider={provider} registry={opts.registry ?? createCockpitRegistry()} selection={store} viewportWidth={opts.width ?? 1440} pollIntervals={FAST_POLL} />);
  return { provider, store, host };
}
