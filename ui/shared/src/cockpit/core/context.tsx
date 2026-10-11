import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from 'react';
import type { Capabilities, RunDetail } from '../../model/kup';
import type { Selection, SelectionAction } from '../../state/selection';
import { useAsync, toAvailability } from './async';
import { failed, unavailable, type Availability } from './availability';
import { CapabilityRegistry } from './capability';
import { loadLiveRunEvidence, loadRunEvidence, type RunEvidence } from './evidenceClient';
import type { ModuleDefinition, ModuleRegistry, SelectionField } from './modules';
import type { UIProvider } from './provider';
import type { RunContext } from './runContext';
import { useSelectionState, type SelectionStore } from './selectionStore';
import { ExecutionProvider, type PollIntervals } from './execution';
import { WorkspaceProvider, useWorkspacePath } from './workspace';

export interface Services { provider: UIProvider; selection: SelectionStore; registry: ModuleRegistry; /** Tests only: how often a started run is polled. */ pollIntervals?: PollIntervals }
const ServicesContext = createContext<Services | null>(null);
const CapabilitiesContext = createContext<{ registry: CapabilityRegistry; state: Availability<Capabilities> }>({ registry: new CapabilityRegistry(null), state: { state: 'loading' } });
export interface Session { snapshotId: string | null; status: Availability<{ snapshotId: string }>; refresh(): void; acquire(workspace?: string): Promise<void>; /** Pin a chosen published snapshot (its digest is verified before anything is read; the selection is cleared). */ choose(snapshotId: string): void }
const SessionContext = createContext<Session | null>(null);
const RunEvidenceContext = createContext<Availability<RunEvidence>>({ state: 'loading' });
const RunContextContext = createContext<Availability<RunContext>>({ state: 'loading' });
const RunDetailContext = createContext<Availability<RunDetail>>({ state: 'loading' });
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
/** The selected run's trace row (S1) exactly as history.detail returned it: its run_events and evidence_records are read through core selectors. */
export const useRunDetail = () => useContext(RunDetailContext);
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
        <WorkspaceBinding>
          <SessionProvider>
            <ExecutionBinding>
              <RunEvidenceProvider>
                <RunContextProvider>{children}</RunContextProvider>
              </RunEvidenceProvider>
            </ExecutionBinding>
          </SessionProvider>
        </WorkspaceBinding>
      </CapabilitiesProvider>
    </ServicesContext.Provider>
  );
}

/** Switching workspace clears what belonged to the previous one (the selected run, record, attempt); nothing is carried across. */
function WorkspaceBinding({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const reset = useCallback(() => selection.dispatch({ type: 'reset' }), [selection]);
  return <WorkspaceProvider provider={provider} onChanged={reset}>{children}</WorkspaceProvider>;
}

/** Start Run state (execution.tsx): follows the launch through Kriya's records and refreshes the cockpit through the ordinary snapshot path when the outcome is durable. */
function ExecutionBinding({ children }: { children: ReactNode }) {
  const { provider, selection, pollIntervals } = useServices();
  const session = useSession();
  return <ExecutionProvider provider={provider} selection={selection} acquire={session.acquire} intervals={pollIntervals}>{children}</ExecutionProvider>;
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
  const openWorkspace = useWorkspacePath();
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
    // The open workspace travels with the acquisition so Kriya's own policy (no acquisition while a run is active there) applies.
    const res = await provider.acquireSnapshot(workspace ?? openWorkspace ?? undefined);
    if (!res.ok) { setAcquireError(failed(res.error.code, res.error.message)); return; }
    setAcquireError(null);
    selection.dispatch({ type: 'reset' });
    setAcquired(res.data.snapshot_id);
  }, [provider, selection, openWorkspace]);

  const choose = useCallback((id: string) => { setAcquireError(null); selection.dispatch({ type: 'reset' }); setAcquired(id); }, [selection]);
  const snapshotId = status.state === 'ready' ? status.data.snapshotId : null;
  const value = useMemo<Session>(() => ({ snapshotId, status, refresh, acquire, choose }), [snapshotId, status, refresh, acquire, choose]);
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

function RunEvidenceProvider({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const { snapshotId } = useSession();
  const runId = useSelectionState(selection, (s) => s.runId);
  const live = useSelectionState(selection, (s) => s.liveLaunchId) === runId && runId !== null;
  const state = useAsync<RunEvidence>(runId ? `evidence:${live ? 'live' : 'row'}:${runId}:${live ? '' : snapshotId ?? ''}` : null, (signal) => (live ? loadLiveRunEvidence(provider, runId!, signal) : loadRunEvidence(provider, runId!, snapshotId, signal)), unavailable('unknown_run', 'Select a run.'));
  return <RunEvidenceContext.Provider value={state}>{children}</RunEvidenceContext.Provider>;
}

function RunContextProvider({ children }: { children: ReactNode }) {
  const { provider, selection } = useServices();
  const { snapshotId } = useSession();
  const caps = useCapabilities();
  const runId = useSelectionState(selection, (s) => s.runId);
  const live = useSelectionState(selection, (s) => s.liveLaunchId) === runId && runId !== null;
  const evidence = useRunEvidence();
  const detail = useAsync<RunDetail>(runId && snapshotId && !live ? `detail:${runId}:${snapshotId}` : null, async (signal) => toAvailability(await provider.runDetail(snapshotId!, runId!, signal)), unavailable('unknown_run', live ? "This run is still executing: its trace row is written when it ends." : 'Select a run.'));
  const value = useMemo<Availability<RunContext>>(() => {
    if (!runId) return unavailable('unknown_run', 'Select a run.');
    if (live) {
      // A run waiting on the human's review: there is no trace row yet, so nothing about its goal, status or failure category is claimed. The baseline and the store come from its manifest.
      if (evidence.state === 'loading') return { state: 'loading' };
      if (evidence.state !== 'ready' && evidence.state !== 'degraded') return unavailable('unknown_run', 'The live evidence of this run is not readable.');
      const manifest = (evidence.data.manifest.manifest ?? {}) as Record<string, unknown>;
      const text = (v: unknown) => (typeof v === 'string' && v.length ? v : null);
      const revision = text(manifest.base_revision);
      const treeHash = text(manifest.base_tree_hash);
      const ctx: RunContext = {
        runId, snapshotId, provider: provider.kind, goal: null, status: 'RUNNING', failureCategory: null, storedAttempts: null, milestone: { groupId: null, index: null, total: null },
        workspace: { identity: text(manifest.workspace_identity), path: text(manifest.workspace_path) }, baseline: { revision, treeHash, source: revision || treeHash ? 'recorded' : 'not_recorded' }, changeType: null, capabilities: caps,
        evidence: { store: 'present', captureMode: text(manifest.capture), sealed: evidence.data.verify?.sealed ?? null, verification: evidence.data.verify?.status ?? null, consistency: evidence.data.provenance.records?.consistency?.kind ?? null },
      };
      return { state: 'ready', data: ctx, provenance: evidence.provenance };
    }
    if (detail.state === 'loading') return { state: 'loading' };
    if (detail.state !== 'ready') return detail.state === 'error' ? failed(detail.code, detail.message) : unavailable('unknown_run', 'The run row is not readable.');
    const run = detail.data.run;
    const header = evidence.state === 'ready' || evidence.state === 'degraded' ? evidence.data : null;
    const manifest = (header?.manifest.manifest ?? {}) as Record<string, unknown>;
    const text = (v: unknown) => (typeof v === 'string' && v.length ? v : null);
    const revision = text(manifest.base_revision);
    const treeHash = text(manifest.base_tree_hash);
    const ctx: RunContext = {
      runId, snapshotId, provider: provider.kind, goal: run.goal ?? null, status: run.status ?? null, failureCategory: run.failure_category ?? null, storedAttempts: typeof run.attempts === 'number' ? run.attempts : null,
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
  }, [runId, live, detail, evidence, caps, provider.kind, snapshotId]);
  return <RunDetailContext.Provider value={detail}><RunContextContext.Provider value={value}>{children}</RunContextContext.Provider></RunDetailContext.Provider>;
}
