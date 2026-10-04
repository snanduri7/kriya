import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { HostAdapter, OpenInIdeResult } from './host/HostAdapter';
import type { Capabilities, HistoryList, KupEnvelope, KupRequest, Prompt, RunDetail, RunSummary, WorkspaceStatus } from './model/kup';
import { checkEnvelope, normalizeDetail } from './model/normalize';
import { isRecorded } from './model/availability';
import { GenerationCounter, applyFailure, applyResponse, emptySlot, startRequest, type Slot, type SlotState } from './state/requests';
import { initialSelection, selectionReducer } from './state/selection';
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
}

export function App({ host, exposeDriver }: AppProps) {
  const [selection, dispatch] = useReducer(selectionReducer, initialSelection);
  const [slots, setSlots] = useState<Slots>({ capabilities: emptySlot(), list: emptySlot(), detail: emptySlot(), prompt: emptySlot(), status: emptySlot() });
  const [runs, setRuns] = useState<RunSummary[]>([]);
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

  const loadList = useCallback((cursor?: string) => request<HistoryList>('list', { operation: 'history.list', limit: 50, ...(cursor ? { cursor } : {}) }, (env) => {
    const page = env.data?.runs ?? [];
    setRuns((prev) => (cursor ? [...prev, ...page.filter((p) => !prev.some((q) => q.run_id === p.run_id))] : page));
  }), [request]);

  const refresh = useCallback(async () => {
    await Promise.all([
      request<Capabilities>('capabilities', { operation: 'capabilities' }),
      loadList(),
      workspacePath ? request<WorkspaceStatus>('status', { operation: 'workspace.status', workspace: workspacePath }) : Promise.resolve(),
    ]);
  }, [request, loadList, workspacePath]);

  useEffect(() => { void host.getSetting('workspacePath').then((w) => setWorkspacePath(w ?? null)); }, [host]);
  useEffect(() => { void refresh(); }, [refresh]);

  useEffect(() => {
    if (!selection.runId) return;
    setSlots((s) => ({ ...s, prompt: emptySlot() }));
    void request<RunDetail>('detail', { operation: 'history.detail', run_id: selection.runId });
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
    });
  }, [exposeDriver, runs, refresh]);

  const selectedEvent = detail && isRecorded(detail.run_events) && selection.eventIndex !== null ? detail.run_events.data[selection.eventIndex] ?? null : null;
  const onOpenResult = (r: OpenInIdeResult) => setNotice(`${r.ok ? 'opened' : 'not opened'} in ${r.editor}${r.verified ? '' : ' (unverified editor)'}: ${sanitizeText(r.message)}`);
  const listHeight = 520, timelineHeight = 320, diffHeight = 220;

  return (
    <div className="app">
      <TrustStrip capabilities={slots.capabilities} list={slots.list} status={slots.status} detail={detail} workspacePath={workspacePath} hostKind={info.kind} fixtureMode={info.fixtureMode} />
      <div className="toolbar">
        <button type="button" onClick={() => void refresh()} disabled={slots.list.pending}>Refresh</button>
        <span className="muted">{slots.list.pending ? 'refreshing…' : slots.list.current ? 'list is current' : slots.list.error ? `list not current: ${slots.list.error.code} - ${sanitizeText(slots.list.error.message)}` : 'not loaded'}</span>
        <span className="narrow-only">
          <button type="button" className="small" aria-pressed={narrowPane === 'timeline'} onClick={() => setNarrowPane('timeline')}>Timeline</button>
          <button type="button" className="small" aria-pressed={narrowPane === 'inspector'} onClick={() => setNarrowPane('inspector')}>Inspector</button>
        </span>
        {notice ? <span role="status" className="notice">{notice}</span> : null}
      </div>
      <main className={`layout pane-${narrowPane}`}>
        <RunsColumn runs={runs} selectedRunId={selection.runId} onSelect={(id) => dispatch({ type: 'selectRun', runId: id })} onLoadMore={slots.list.value?.data?.next_cursor ? () => void loadList(slots.list.value?.data?.next_cursor ?? undefined) : null} pending={slots.list.pending} height={listHeight} />
        <Timeline detail={detail} pending={slots.detail.pending} error={slots.detail.error} attempt={selection.attempt} eventIndex={selection.eventIndex} onSelectAttempt={(a) => dispatch({ type: 'selectAttempt', attempt: a })} onSelectEvent={(i) => { dispatch({ type: 'selectEvent', eventIndex: i }); if (i !== null) dispatch({ type: 'inspectorTab', tab: 'evidence' }); }} height={timelineHeight} />
        <Inspector detail={detail} selectedEvent={selectedEvent} tab={selection.inspectorTab} onTab={(t) => dispatch({ type: 'inspectorTab', tab: t })} prompt={slots.prompt} onLoadPrompt={() => { if (selection.runId) void request<Prompt>('prompt', { operation: 'history.prompt', run_id: selection.runId }); }} host={host} onOpenResult={onOpenResult} />
      </main>
      <Drawer detail={detail} open={selection.drawerOpen} tab={selection.drawerTab} comparisonPath={selection.comparisonPath} onTab={(t) => dispatch({ type: 'drawerTab', tab: t })} onToggle={() => dispatch({ type: 'toggleDrawer' })} onSelectComparison={(p) => dispatch({ type: 'selectComparison', path: p })} host={host} onOpenResult={onOpenResult} height={diffHeight} />
    </div>
  );
}
