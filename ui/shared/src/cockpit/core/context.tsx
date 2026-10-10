import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Capabilities, RunDetail } from '../../model/kup';
import type { Selection, SelectionAction } from '../../state/selection';
import { useAsync, toAvailability } from './async';
import { failed, unavailable, type Availability } from './availability';
import { CapabilityRegistry } from './capability';
import { loadRunEvidence, type RunEvidence } from './evidenceClient';
import type { ModuleDefinition, ModuleRegistry, SelectionField } from './modules';
import type { UIProvider } from './provider';
import type { RunContext } from './runContext';
import { useSelectionState, type SelectionStore } from './selectionStore';

export interface Services { provider: UIProvider; selection: SelectionStore; registry: ModuleRegistry }
const ServicesContext = createContext<Services | null>(null);
const CapabilitiesContext = createContext<{ registry: CapabilityRegistry; state: Availability<Capabilities> }>({ registry: new CapabilityRegistry(null), state: { state: 'loading' } });
export interface Session { snapshotId: string | null; status: Availability<{ snapshotId: string }>; refresh(): void; acquire(workspace?: string): Promise<void> }
const SessionContext = createContext<Session | null>(null);
const RunEvidenceContext = createContext<Availability<RunEvidence>>({ state: 'loading' });
const RunContextContext = createContext<Availability<RunContext>>({ state: 'loading' });
const ModuleScopeContext = createContext<ModuleDefinition | null>(null);

function need<T>(value: T | null, name: string): T {
  if (value === null) throw new Error(`${name} used outside the cockpit providers`);
  return value;
}

export const useServices = () => need(useContext(ServicesContext), 'useServices');
export const useProvider = () => useServices().provider;
export const useCapabilities = () => useContext(CapabilitiesContext).registry;
export const useSession = () => need(useContext(SessionContext), 'useSession');
export const useRunEvidence = () => useContext(RunEvidenceContext);
export const useRunContext = () => useContext(RunContextContext);
export const useDispatch = () => { const { selection } = useServices(); return useCallback((a: SelectionAction) => selection.dispatch(a), [selection]); };

/** Read one selection field. A module may read only the fields its definition declares; anything else is a programming error. */
export function useModuleSelection<K extends SelectionField>(field: K): Selection[K] {
  const { selection } = useServices();
  const scope = useContext(ModuleScopeContext);
  if (scope && !scope.selectionDependencies.includes(field)) throw new Error(`module ${scope.id} reads selection.${field} but does not declare it in selectionDependencies`);
  return useSelectionState(selection, (s) => s[field]);
}

/** The shell, not the module, reads whatever the layout itself needs (never a module). */
export function useShellSelection<K extends SelectionField>(field: K): Selection[K] {
  const { selection } = useServices();
  return useSelectionState(selection, (s) => s[field]);
}

export function ModuleScope({ def, children }: { def: ModuleDefinition; children: ReactNode }) {
  return <ModuleScopeContext.Provider value={def}>{children}</ModuleScopeContext.Provider>;
}

export function CockpitProviders({ services, children }: { services: Services; children: ReactNode }) {
  return (
    <ServicesContext.Provider value={services}>
      <CapabilitiesProvider>
        <SessionProvider>
          <RunEvidenceProvider>
            <RunContextProvider>{children}</RunContextProvider>
          </RunEvidenceProvider>
        </SessionProvider>
      </CapabilitiesProvider>
    </ServicesContext.Provider>
  );
}

function CapabilitiesProvider({ children }: { children: ReactNode }) {
  const { provider } = useServices();
  const state = useAsync<Capabilities>('capabilities', async (signal) => toAvailability(await provider.capabilities(signal)), { state: 'loading' });
  const value = useMemo(() => ({ registry: new CapabilityRegistry(state.state === 'ready' ? state.data : null), state }), [state]);
  return <CapabilitiesContext.Provider value={value}>{children}</CapabilitiesContext.Provider>;
}

/** Pins a browsing session to one verified snapshot (digest verified at pin, as in M1); it never substitutes another id. */
function SessionProvider({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const [nonce, setNonce] = useState(0);
  const [acquired, setAcquired] = useState<string | null>(null);
  const [acquireError, setAcquireError] = useState<Availability<{ snapshotId: string }> | null>(null);
  const refresh = useCallback(() => { setAcquired(null); setAcquireError(null); setNonce((n) => n + 1); }, []);

  const pinned = useAsync<{ snapshotId: string }>(`session:${nonce}:${acquired ?? ''}`, async (signal) => {
    let id = acquired;
    if (id === null) {
      const list = await provider.listSnapshots(signal);
      if (!list.ok) return failed(list.error.code, list.error.message);
      const newest = list.data.snapshots[0];
      if (!newest) return unavailable('store_absent', 'No published snapshot yet: acquire one to read run history.');
      id = newest.snapshot_id;
    }
    const verified = await provider.verifySnapshot(id, signal);
    if (!verified.ok) return failed(verified.error.code, verified.error.message);
    if (verified.data.snapshot_id !== id) return failed('INVALID_RESPONSE', `verification named ${verified.data.snapshot_id}, not the requested ${id}; it is not pinned`);
    return { state: 'ready', data: { snapshotId: id }, provenance: verified.provenance };
  }, { state: 'loading' });
  const status = acquireError ?? pinned;

  const acquire = useCallback(async (workspace?: string) => {
    const res = await provider.acquireSnapshot(workspace);
    if (!res.ok) { setAcquireError(failed(res.error.code, res.error.message)); return; }
    setAcquireError(null);
    selection.dispatch({ type: 'reset' });
    setAcquired(res.data.snapshot_id);
  }, [provider, selection]);

  const snapshotId = status.state === 'ready' ? status.data.snapshotId : null;
  const value = useMemo<Session>(() => ({ snapshotId, status, refresh, acquire }), [snapshotId, status, refresh, acquire]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

function RunEvidenceProvider({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const { snapshotId } = useSession();
  const runId = useSelectionState(selection, (s) => s.runId);
  const state = useAsync<RunEvidence>(runId ? `evidence:${runId}:${snapshotId ?? ''}` : null, (signal) => loadRunEvidence(provider, runId!, snapshotId, signal), unavailable('unknown_run', 'Select a run.'));
  return <RunEvidenceContext.Provider value={state}>{children}</RunEvidenceContext.Provider>;
}

function RunContextProvider({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const { snapshotId } = useSession();
  const caps = useCapabilities();
  const runId = useSelectionState(selection, (s) => s.runId);
  const evidence = useRunEvidence();
  const detail = useAsync<RunDetail>(runId && snapshotId ? `detail:${runId}:${snapshotId}` : null, async (signal) => toAvailability(await provider.runDetail(snapshotId!, runId!, signal)), unavailable('unknown_run', 'Select a run.'));
  const value = useMemo<Availability<RunContext>>(() => {
    if (!runId) return unavailable('unknown_run', 'Select a run.');
    if (detail.state === 'loading') return { state: 'loading' };
    if (detail.state !== 'ready') return detail.state === 'error' ? failed(detail.code, detail.message) : unavailable('unknown_run', 'The run row is not readable.');
    const run = detail.data.run;
    const header = evidence.state === 'ready' || evidence.state === 'degraded' ? evidence.data : null;
    const manifest = (header?.manifest.manifest ?? {}) as Record<string, unknown>;
    const text = (v: unknown) => (typeof v === 'string' && v.length ? v : null);
    const revision = text(manifest.base_revision);
    const treeHash = text(manifest.base_tree_hash);
    const ctx: RunContext = {
      runId, snapshotId, provider: provider.kind, goal: run.goal ?? null, status: run.status ?? null,
      milestone: { groupId: run.milestone_group_id ?? null, index: run.milestone_index ?? null, total: run.milestone_total ?? null },
      workspace: { identity: text(manifest.workspace_identity), path: text(manifest.workspace_path) },
      baseline: { revision, treeHash, source: revision || treeHash ? 'recorded' : 'not_recorded' },
      changeType: null,
      capabilities: caps,
      evidence: {
        store: header ? 'present' : 'absent',
        captureMode: text(header?.manifest.manifest?.capture),
        sealed: header ? header.verify?.sealed ?? null : null,
        verification: header?.verify?.status ?? null,
        consistency: header?.provenance.records?.consistency?.kind ?? null,
      },
    };
    return { state: 'ready', data: ctx, provenance: detail.provenance };
  }, [runId, detail, evidence, caps, provider.kind, snapshotId]);
  return <RunContextContext.Provider value={value}>{children}</RunContextContext.Provider>;
}
