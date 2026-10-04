import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { HostAdapter, OpenInIdeResult } from './host/HostAdapter';
import type { Capabilities, HistoryList, KupEnvelope, KupRequest, Prompt, RunDetail, RunSummary, SnapshotAcquireResult, SnapshotList, SnapshotVerify, WorkspaceStatus } from './model/kup';
import { checkEnvelope, normalizeDetail } from './model/normalize';
import { isRecorded } from './model/availability';
import { GenerationCounter, applyFailure, applyResponse, emptySlot, startRequest, type Slot, type SlotState } from './state/requests';
import { initialSelection, selectionReducer } from './state/selection';
import { freshnessLabel, initialSnapshotSession, snapshotReducer, type PinVerification, type SnapshotAction, type SnapshotSession } from './state/snapshot';
import { Drawer } from './panels/Drawer';
import { Inspector } from './panels/Inspector';
import { RunsColumn } from './panels/RunsColumn';
import { Timeline } from './panels/Timeline';
import { TrustStrip } from './panels/TrustStrip';
import { sanitizeText } from './render/sanitize';

/** Driver a host may use to automate the UI (measurements). Exposed only when the host asks for it. */
export interface AppDriver {
  listRunIds(): string[];
  selectRun(runId: string): Promise<{ populatedMs: number }>;
  refresh(): Promise<void>;
  acquire(): Promise<void>;
  pinnedSnapshotId(): string | null;
}

export interface AppProps {
  host: HostAdapter;
  exposeDriver?: (driver: AppDriver) => void;
}

interface Slots {
  capabilities: SlotState<KupEnvelope<Capabilities>>;
  list: SlotState<KupEnvelope<HistoryList>>;
  detail: SlotState<KupEnvelope<RunDetail>>;
  prompt: SlotState<KupEnvelope<Prompt>>;
  status: SlotState<KupEnvelope<WorkspaceStatus>>;
  snapshots: SlotState<KupEnvelope<SnapshotList>>;
  acquire: SlotState<KupEnvelope<SnapshotAcquireResult>>;
  verify: SlotState<KupEnvelope<SnapshotVerify>>;
}

export function App({ host, exposeDriver }: AppProps) {
  const [selection, dispatch] = useReducer(selectionReducer, initialSelection);
  const [slots, setSlots] = useState<Slots>({ capabilities: emptySlot(), list: emptySlot(), detail: emptySlot(), prompt: emptySlot(), status: emptySlot(), snapshots: emptySlot(), acquire: emptySlot(), verify: emptySlot() });
  const [pinRefusal, setPinRefusal] = useState<string | null>(null);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [snapshot, setSnapshot] = useState<SnapshotSession>(initialSnapshotSession);
  const snapshotRef = useRef<SnapshotSession>(initialSnapshotSession);
  snapshotRef.current = snapshot;
  const [workspacePath, setWorkspacePath] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [narrowPane, setNarrowPane] = useState<'timeline' | 'inspector'>('timeline');
  const counter = useRef(new GenerationCounter()).current;
  const populateResolvers = useRef(new Map<string, (ms: number) => void>());
  const selectStarted = useRef(new Map<string, number>());
  const info = host.hostInfo();

  const request = useCallback(async <T,>(slot: Slot & keyof Slots, req: KupRequest, after?: (env: KupEnvelope<T>) => void) => {
    const generation = counter.next(slot);
    const update = (fn: (prev: SlotState<KupEnvelope<T>>) => SlotState<KupEnvelope<T>>) =>
      setSlots((s) => ({ ...s, [slot]: fn(s[slot] as unknown as SlotState<KupEnvelope<T>>) }));
    update((prev) => startRequest(prev, generation));
    let raw: unknown;
    try {
      raw = await host.query(req);
    } catch (e) {
      update((prev) => applyFailure(prev, generation, counter, slot, { code: 'HOST_ERROR', message: e instanceof Error ? e.message : String(e) }));
      return;
    }
    const checked = checkEnvelope(raw);
    if (!checked.ok) {
      update((prev) => applyFailure(prev, generation, counter, slot, { code: checked.code, message: checked.message }));
      return;
    }
    const env = checked.envelope as KupEnvelope<T>;
    if (env.error) {
      update((prev) => applyFailure(prev, generation, counter, slot, { code: String(env.error?.code), message: String(env.error?.message ?? '') }));
      return;
    }
    if (!counter.isCurrent(slot, generation)) return; // stale (P-32)
    update((prev) => applyResponse(prev, generation, counter, slot, env, env.observed_at));
    after?.(env);
  }, [host, counter]);

  /** Every history read is PINNED to the displayed snapshot (gate C-2); without a pin there is nothing to read. */
  const loadList = useCallback((cursor?: string) => {
    const pinned = snapshotRef.current.pinnedId;
    if (!pinned) { setRuns([]); return Promise.resolve(); }
    return request<HistoryList>('list', { operation: 'history.list', snapshot_id: pinned, limit: 50, ...(cursor ? { cursor } : {}) }, (env) => {
      const page = env.data?.runs ?? [];
      setRuns((prev) => (cursor ? [...prev, ...page.filter((p) => !prev.some((q) => q.run_id === p.run_id))] : page));
    });
  }, [request]);

  /** Invalidate everything that belonged to the previous pin (gate C-2): responses, cursors, selection. */
  const invalidateForNewPin = useCallback(() => {
    counter.next('list'); counter.next('detail'); counter.next('prompt');
    setRuns([]);
    setSlots((s) => ({ ...s, list: emptySlot(), detail: emptySlot(), prompt: emptySlot() }));
    dispatch({ type: 'selectRun', runId: null });
  }, [counter]);

  const listSnapshots = useCallback(() => request<SnapshotList>('snapshots', { operation: 'snapshot.list' }, (env) => {
    const snaps = env.data?.snapshots ?? [];
    setSnapshot((prev) => {
      const next = snapshotReducer(prev, { type: 'listed', snapshots: snaps }).state;
      if (next.pinnedId && !snaps.some((x) => x.snapshot_id === next.pinnedId)) return snapshotReducer(next, { type: 'unavailable' }).state;
      return next;
    });
  }), [request]);

  /** "Refresh displayed snapshot": re-reads the pinned snapshot and the live observations. NEVER acquires (gate C-1). */
  const refresh = useCallback(async () => {
    await Promise.all([
      request<Capabilities>('capabilities', { operation: 'capabilities' }),
      listSnapshots(),
      loadList(),
      workspacePath ? request<WorkspaceStatus>('status', { operation: 'workspace.status', workspace: workspacePath }) : Promise.resolve(),
    ]);
  }, [request, listSnapshots, loadList, workspacePath]);

  /** Digest verified at pin (08 review F-4): Kriya verifies the SHA-256 of EXACTLY `snapshotId` before anything of it is
   * displayed; a failed verification, or an answer naming another id, leaves the pin unchanged. Every later query of
   * the pinned snapshot checks size/mtime only - that is the whole guarantee, stated as such in the trust strip. */
  const verifyAndPin = useCallback(async (snapshotId: string, pin: (verification: PinVerification) => SnapshotAction): Promise<boolean> => {
    let pinned = false;
    setPinRefusal(null);
    await request<SnapshotVerify>('verify', { operation: 'snapshot.verify', snapshot_id: snapshotId }, (env) => {
      if (!env.data || env.data.snapshot_id !== snapshotId || env.data.digest_verified !== true) {
        setPinRefusal(`snapshot ${snapshotId} not displayed: the verification answer did not confirm this exact snapshot`);
        return;
      }
      const result = snapshotReducer(snapshotRef.current, pin({ verified_at: env.data.verified_at, sha256: env.data.sha256 ?? null }));
      snapshotRef.current = result.state;
      setSnapshot(result.state);
      if (result.pinChanged) invalidateForNewPin();
      pinned = true;
    });
    return pinned;
  }, [request, invalidateForNewPin]);

  /** "Acquire new snapshot": the one explicit acquisition request; on success the returned id is verified, then pinned. */
  const acquire = useCallback(async () => {
    let acquired: SnapshotAcquireResult | null = null;
    await request<SnapshotAcquireResult>('acquire', { operation: 'snapshot.acquire', ...(workspacePath ? { workspace: workspacePath } : {}) }, (env) => { acquired = env.data; });
    const summary = acquired as SnapshotAcquireResult | null;
    if (summary) await verifyAndPin(summary.snapshot_id, (verification) => ({ type: 'acquired', summary, verification }));
    await Promise.all([listSnapshots(), loadList()]);
  }, [request, workspacePath, verifyAndPin, listSnapshots, loadList]);

  /** A user-chosen switch to another published snapshot: verified first, then it invalidates prior responses and
   * cursors (gate C-2); a snapshot that fails verification is never displayed. */
  const chooseSnapshot = useCallback((snapshotId: string) => {
    if (!snapshotRef.current.available.some((s) => s.snapshot_id === snapshotId)) return;
    void verifyAndPin(snapshotId, (verification) => ({ type: 'choose', snapshotId, verification })).then((ok) => { if (ok) void loadList(); });
  }, [verifyAndPin, loadList]);

  useEffect(() => { void host.getSetting('workspacePath').then((w) => setWorkspacePath(w ?? null)); }, [host]);
  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!selection.runId) return;
    const pinned = snapshotRef.current.pinnedId;
    if (!pinned) return;
    setSlots((s) => ({ ...s, prompt: emptySlot() }));
    void request<RunDetail>('detail', { operation: 'history.detail', snapshot_id: pinned, run_id: selection.runId });
  }, [selection.runId, request]);

  const detail = useMemo(() => (slots.detail.value?.data && slots.detail.value.data.run?.run_id === selection.runId ? normalizeDetail(slots.detail.value.data) : null), [slots.detail.value, selection.runId]);

  useEffect(() => {
    if (!detail) return;
    const started = selectStarted.current.get(detail.run.run_id);
    const resolve = populateResolvers.current.get(detail.run.run_id);
    if (started !== undefined && resolve) {
      resolve(performance.now() - started);
      populateResolvers.current.delete(detail.run.run_id);
      selectStarted.current.delete(detail.run.run_id);
    }
  }, [detail]);

  useEffect(() => {
    if (!exposeDriver) return;
    exposeDriver({
      listRunIds: () => runs.map((r) => r.run_id),
      selectRun: (runId) => new Promise((resolve) => {
        populateResolvers.current.set(runId, (ms) => resolve({ populatedMs: ms }));
        selectStarted.current.set(runId, performance.now());
        dispatch({ type: 'selectRun', runId });
      }),
      refresh,
      acquire,
      pinnedSnapshotId: () => snapshotRef.current.pinnedId,
    });
  }, [exposeDriver, runs, refresh, acquire]);

  const selectedEvent = detail && isRecorded(detail.run_events) && selection.eventIndex !== null ? detail.run_events.data[selection.eventIndex] ?? null : null;
  const onOpenResult = (r: OpenInIdeResult) => setNotice(`${r.ok ? 'opened' : 'not opened'} in ${r.editor}${r.verified ? '' : ' (unverified editor)'}: ${sanitizeText(r.message)}`);
  const listHeight = 520, timelineHeight = 320, diffHeight = 220;
  const pinnedSummary = snapshot.available.find((s) => s.snapshot_id === snapshot.pinnedId) ?? null;
  const label = freshnessLabel(slots.list.value?.consistency ?? null, pinnedSummary);

  return (
    <div className="app">
      <TrustStrip capabilities={slots.capabilities} list={slots.list} status={slots.status} detail={detail} workspacePath={workspacePath} hostKind={info.kind} fixtureMode={info.fixtureMode} snapshot={snapshot} label={label} />
      <div className="toolbar">
        <button type="button" onClick={() => void acquire()} disabled={slots.acquire.pending} title="Explicit acquisition: Kriya copies the live store into a new published snapshot and this session pins it">Acquire new snapshot</button>
        <button type="button" onClick={() => void refresh()} disabled={slots.list.pending || !snapshot.pinnedId} title="Re-read the displayed snapshot and the live observations; never acquires">Refresh displayed snapshot</button>
        <label className="muted">displayed snapshot
          <select aria-label="Displayed snapshot" value={snapshot.pinnedId ?? ''} onChange={(e) => { if (e.target.value) chooseSnapshot(e.target.value); }}>
            {!snapshot.pinnedId ? <option value="">none - acquire or choose</option> : null}
            {snapshot.available.map((s) => <option key={s.snapshot_id} value={s.snapshot_id}>{s.snapshot_id} (acquired {s.acquisition_completed_at ?? '?'})</option>)}
          </select>
        </label>
        <span className="muted" role="status" aria-label="Snapshot state" aria-live="polite">{slots.acquire.pending ? 'acquiring…' : slots.acquire.error ? `acquisition failed: ${slots.acquire.error.code} - ${sanitizeText(slots.acquire.error.message)}` : slots.verify.pending ? 'verifying snapshot digest…' : slots.verify.error ? `snapshot not displayed: digest verification failed: ${slots.verify.error.code} - ${sanitizeText(slots.verify.error.message)}` : pinRefusal ? sanitizeText(pinRefusal) : slots.snapshots.error ? `snapshot list failed: ${slots.snapshots.error.code} - ${sanitizeText(slots.snapshots.error.message)}` : slots.list.pending ? 'reading snapshot…' : slots.list.error ? `snapshot read failed: ${slots.list.error.code} - ${sanitizeText(slots.list.error.message)}` : snapshot.pinnedId ? `${label.headline}; ${label.metadataText}` : 'no snapshot displayed: acquire one, or choose a published snapshot'}</span>
        <span className="narrow-only">
          <button type="button" className="small" aria-pressed={narrowPane === 'timeline'} onClick={() => setNarrowPane('timeline')}>Timeline</button>
          <button type="button" className="small" aria-pressed={narrowPane === 'inspector'} onClick={() => setNarrowPane('inspector')}>Inspector</button>
        </span>
        {notice ? <span role="status" aria-label="Host action result" className="notice">{notice}</span> : null}
      </div>
      <main className={`layout pane-${narrowPane}`}>
        <RunsColumn runs={runs} selectedRunId={selection.runId} onSelect={(id) => dispatch({ type: 'selectRun', runId: id })} onLoadMore={slots.list.value?.data?.next_cursor ? () => void loadList(slots.list.value?.data?.next_cursor ?? undefined) : null} pending={slots.list.pending} height={listHeight} emptyText={snapshot.pinnedId ? undefined : 'no snapshot displayed'} />
        <Timeline detail={detail} pending={slots.detail.pending} error={slots.detail.error} attempt={selection.attempt} eventIndex={selection.eventIndex} onSelectAttempt={(a) => dispatch({ type: 'selectAttempt', attempt: a })} onSelectEvent={(i) => { dispatch({ type: 'selectEvent', eventIndex: i }); if (i !== null) dispatch({ type: 'inspectorTab', tab: 'evidence' }); }} height={timelineHeight} />
        <Inspector detail={detail} selectedEvent={selectedEvent} tab={selection.inspectorTab} onTab={(t) => dispatch({ type: 'inspectorTab', tab: t })} prompt={slots.prompt} onLoadPrompt={() => { const pinned = snapshotRef.current.pinnedId; if (selection.runId && pinned) void request<Prompt>('prompt', { operation: 'history.prompt', snapshot_id: pinned, run_id: selection.runId }); }} host={host} onOpenResult={onOpenResult} />
      </main>
      <Drawer detail={detail} open={selection.drawerOpen} tab={selection.drawerTab} comparisonPath={selection.comparisonPath} onTab={(t) => dispatch({ type: 'drawerTab', tab: t })} onToggle={() => dispatch({ type: 'toggleDrawer' })} onSelectComparison={(p) => dispatch({ type: 'selectComparison', path: p })} host={host} onOpenResult={onOpenResult} height={diffHeight} />
    </div>
  );
}
