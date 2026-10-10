/**
 * Start Run, UI core (GUI M6): the goal, Kriya's execution preflight, the one launch this window started, and what Kriya's DURABLE records say about it.
 *
 * Authority. Nothing here decides whether a run may start (Kriya's preflight reports; the launched process re-resolves it and refuses), runs a model,
 * starts a process or infers an outcome. The host's `startRun` launches the one allowlisted Kriya command; this file then FOLLOWS the launch by reading
 * Kriya: `launch.status` (launch record + RunRecord + evidence store + workspace lock) and `evidence.records` (the live, verified chain). A process exit is
 * shown as a process exit - never as the result of the run. Phase information is the recorded record kinds, counted verbatim; no stage is predicted,
 * no pending step is invented. Elapsed time is presentation-only, derived from the launch record's own timestamps.
 *
 * Polling is the whole live mechanism (no listener, no stream): a short interval while the window is visible, a long one while it is hidden, none once
 * the outcome is durable. When it is, the Runs/Timeline/Overview/... modules are refreshed through the ordinary snapshot path - they never see a special case.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { ExecutionPreflight, ExecutionState, LaunchStatus } from '../../model/kup';
import type { ProviderError, UIProvider } from './provider';
import { useWorkspace } from './workspace';
import type { SelectionStore } from './selectionStore';

export interface BriefRecord { seq: number; kind: string; unit: string | null; attempt: number | null; role: string | null; at: string }
export interface RunProgress {
  /** The highest evidence seq read so far (0 = none). */
  lastSeq: number;
  /** Records seen per recorded kind, verbatim. */
  kinds: Record<string, number>;
  recent: BriefRecord[];
  /** A typed reason the live records could not be read this time (the launch itself is still followed). */
  error: string | null;
}
export const NO_PROGRESS: RunProgress = { lastSeq: 0, kinds: {}, recent: [], error: null };

export type OutcomeKind = 'success' | 'failure' | 'needs_review' | 'refused' | 'interrupted' | 'not_recorded' | 'approval_required';
export interface RunOutcome {
  kind: OutcomeKind;
  label: string;
  detail: string;
  /** The words of the authority this outcome was read from. */
  source: string;
  /** The trace row id of the finished run, when the process sealed one (to select it in Runs). */
  traceRunId: string | null;
  workflowStatus: string | null;
  /** For approval_required: what Kriya recorded about the decision it reached (never an invented explanation). */
  approval?: { kind: string; reason: string; details: Record<string, unknown> };
  /** Decisions the run could proceed without that the desktop declined (nothing approved); informational, whatever the outcome. */
  declined: Array<{ kind: string; reason: string }>;
}

export interface RunTiming { clickedAt: number; acknowledgedAt: number | null; recordSeenAt: number | null; terminalAt: number | null; refreshedAt: number | null }
export type RunStage = 'starting' | 'running' | 'finished';
export interface RunView {
  launchId: string;
  workspace: string;
  host: ExecutionState | null;
  status: LaunchStatus | null;
  /** A read failure other than "not recorded yet" (which is the normal first moment of a launch). */
  statusError: ProviderError | null;
  progress: RunProgress;
  stage: RunStage;
  outcome: RunOutcome | null;
  timing: RunTiming;
}

export interface StartRefusal { code: string; message: string; blockers: Array<{ code: string; message: string }> }
export type PreflightState =
  | { state: 'unavailable'; reason: string }
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'ready'; data: ExecutionPreflight }
  | { state: 'error'; error: ProviderError };

export interface ExecutionApi {
  /** This host can start a run at all (a browser test host and the fixture stand-in cannot). */
  available: boolean;
  goal: string;
  setGoal(goal: string): void;
  preflight: PreflightState;
  refreshPreflight(): void;
  /** A Start Run request is in flight (preflight round trip + launch): the button is disabled and a second click is not sent. */
  starting: boolean;
  refusal: StartRefusal | null;
  start(): Promise<void>;
  run: RunView | null;
  /** A run is in progress (starting or running): the workspace must not change. */
  locked: boolean;
  /** Clear a finished run's banner. */
  dismiss(): void;
}

const ExecutionContext = createContext<ExecutionApi | null>(null);
export function useExecution(): ExecutionApi {
  const value = useContext(ExecutionContext);
  if (value === null) throw new Error('useExecution used outside the cockpit providers');
  return value;
}

export interface PollIntervals { visibleMs: number; slowMs: number; hiddenMs: number; slowAfterMs: number }
export const DEFAULT_INTERVALS: PollIntervals = { visibleMs: 1000, slowMs: 3000, hiddenMs: 5000, slowAfterMs: 60_000 };

const RECENT_MAX = 12;
const PAGE = 200;

/** The outcome, from DURABLE records only. null = not over (or not provably over). Pure: the tests drive it with every combination. */
export function deriveOutcome(status: LaunchStatus | null, notRecorded: boolean, host: ExecutionState | null): RunOutcome | null {
  const outcome = deriveCore(status, notRecorded, host);
  const declined = (status?.launch.declined_decisions ?? []).map((d) => ({ kind: d.kind, reason: d.reason }));
  return outcome === null ? null : { ...outcome, declined };
}

function deriveCore(status: LaunchStatus | null, notRecorded: boolean, host: ExecutionState | null): Omit<RunOutcome, 'declined'> | null {
  const exited = host?.phase === 'exited';
  const exitText = host?.exit ? (host.exit.signal ? `signal ${host.exit.signal}` : `exit code ${host.exit.code ?? 'unknown'}`) : 'exit not reported';
  if (status === null) {
    return notRecorded && exited
      ? { kind: 'not_recorded', label: 'No run was recorded', detail: `The execution process ended (${exitText}) before Kriya recorded a launch. Nothing ran and nothing was changed by Kriya.`, source: 'Kriya has no launch record for this id; the host reports the process exit', traceRunId: null, workflowStatus: null }
      : null;
  }
  const { launch, run_record: record } = status;
  if (launch.phase === 'ENDED') {
    const ended = launch.ended;
    const workflowStatus = ended?.workflow_status ?? null;
    const traceRunId = ended?.trace_run_id ?? null;
    const needed = launch.approval_required;
    // The RunRecord is the authority: a run that reached SUCCESS was not stopped by any decision, whatever was noted along the way.
    if (needed && !(record.found && record.terminal_status === 'SUCCESS')) {
      return { kind: 'approval_required', label: 'Approval required', detail: needed.reason, source: 'Kriya launch record (the decision the workflow reached)', traceRunId, workflowStatus,
        approval: { kind: needed.kind, reason: needed.reason, details: needed.details } };
    }
    if (!launch.run) {
      return { kind: 'refused', label: 'Refused before a run started', detail: ended?.error ?? 'Kriya refused the launch before starting a run.', source: 'Kriya launch record (sealed by the launching process)', traceRunId, workflowStatus };
    }
    if (record.found && record.terminal_status) {
      const kind: OutcomeKind = record.terminal_status === 'SUCCESS' ? 'success' : record.terminal_status === 'FAILURE' ? 'failure' : 'needs_review';
      const word = kind === 'success' ? 'Run succeeded' : kind === 'failure' ? 'Run failed' : `Run ended: ${record.terminal_status}`;
      const extras = [ended?.failure_category ? `failure category ${ended.failure_category}` : null, record.commit_result ? `commit ${record.commit_result}` : null, ended && ended.exit_code !== 0 ? `process exit code ${ended.exit_code}` : null].filter(Boolean);
      return { kind, label: word, detail: extras.length ? extras.join(' · ') : 'Recorded by Kriya.', source: 'Kriya RunRecord (the run\'s own lifecycle authority)', traceRunId, workflowStatus };
    }
    return { kind: 'interrupted', label: 'The run did not finish', detail: ended?.error ?? 'The launching process ended and no terminal outcome is recorded in the run record.', source: 'Kriya launch record', traceRunId, workflowStatus };
  }
  if (launch.phase === 'STARTING' && status.liveness === 'starting' && exited) {
    return { kind: 'interrupted', label: 'The run did not finish', detail: `The execution process ended (${exitText}) before the run started: Kriya recorded the launch but never opened the run. Nothing ran.`, source: 'Kriya launch record (phase STARTING, workspace lock free; DERIVED); the host reports the process exit', traceRunId: null, workflowStatus: null };
  }
  if (status.liveness === 'interrupted' && exited) {
    return { kind: 'interrupted', label: 'The run did not finish', detail: `The execution process ended (${exitText}) without sealing its record. This is not a success; the evidence recorded so far is still inspectable.`, source: 'Kriya launch record + workspace run lock (DERIVED); the host reports the process exit', traceRunId: null, workflowStatus: null };
  }
  return null;
}

function brief(record: Record<string, unknown>): BriefRecord {
  const text = (v: unknown) => (typeof v === 'string' ? v : null);
  const num = (v: unknown) => (typeof v === 'number' ? v : null);
  return { seq: Number(record.seq), kind: String(record.kind), unit: text(record.unit_id), attempt: num(record.attempt_number), role: text(record.role), at: String(record.t_wall ?? '') };
}

export function ExecutionProvider({ provider, selection, acquire, intervals = DEFAULT_INTERVALS, children }: { provider: UIProvider; selection: SelectionStore; /** The session's snapshot acquisition (the ordinary refresh path). */ acquire(workspace: string): Promise<void>; intervals?: PollIntervals; children: ReactNode }) {
  const port = provider.execution;
  const ws = useWorkspace();
  const workspace = ws.current?.path ?? null;
  const refreshWorkspaceRef = useRef(ws.refresh);
  const [goal, setGoal] = useState('');
  const [done, setDone] = useState<{ key: string; state: PreflightState } | null>(null);
  const [nonce, setNonce] = useState(0);
  const [starting, setStarting] = useState(false);
  const [refusal, setRefusal] = useState<StartRefusal | null>(null);
  const [run, setRun] = useState<RunView | null>(null);
  const startingRef = useRef(false);
  const runRef = useRef<RunView | null>(null);
  const pendingRef = useRef<string | null>(null);
  const acquireRef = useRef(acquire);
  useEffect(() => { runRef.current = run; acquireRef.current = acquire; refreshWorkspaceRef.current = ws.refresh; });

  // A different workspace: the goal and the preflight belonged to the previous one. (A run in progress prevents the switch; a finished run's banner is kept.)
  const previousWorkspace = useRef<string | null>(workspace);
  useEffect(() => {
    if (previousWorkspace.current === workspace) return;
    previousWorkspace.current = workspace;
    setGoal('');
    setRefusal(null);
    if (runRef.current && runRef.current.stage === 'finished') setRun(null);
  }, [workspace, port]);

  // The preflight is asked of Kriya whenever a workspace is open (and again on request) - never computed here, never cached across a refresh.
  const key = port && workspace !== null ? `${workspace}|${nonce}` : null;
  useEffect(() => {
    if (key === null || workspace === null) return undefined;
    const ac = new AbortController();
    void provider.executionPreflight(workspace, ac.signal).then((r) => {
      if (ac.signal.aborted) return;
      setDone({ key, state: r.ok ? { state: 'ready', data: r.data } : { state: 'error', error: r.error } });
    });
    return () => ac.abort();
  }, [provider, key, workspace]);
  const preflight = useMemo<PreflightState>(() => (!port ? { state: 'unavailable', reason: 'This host cannot start a run.' } : key === null ? { state: 'idle' } : done?.key === key ? done.state : done && key !== null && done.key.split('|')[0] === key.split('|')[0] ? done.state : { state: 'loading' }), [port, key, done]);
  // (a re-check of the SAME folder keeps showing the last answer until Kriya's new one arrives; the host re-resolves it authoritatively at Start regardless)

  const refreshPreflight = useCallback(() => setNonce((n) => n + 1), []);

  const start = useCallback(async () => {
    if (!port || workspace === null || startingRef.current) return;
    if (runRef.current && runRef.current.stage !== 'finished') return;
    startingRef.current = true; // synchronous: a second click in the same tick never reaches the host
    setStarting(true);
    setRefusal(null);
    const clickedAt = performance.now();
    try {
      const result = await port.startRun({ workspace, goal });
      const acknowledgedAt = performance.now();
      if (!result.started || result.launch_id === null) {
        const r = result.refusal;
        setRefusal({ code: r?.code ?? 'HOST_ERROR', message: r?.message ?? 'The host did not start the run.', blockers: (r?.blockers ?? []) as StartRefusal['blockers'] });
        if (r?.code === 'PREFLIGHT_BLOCKED') refreshPreflight();
        return;
      }
      setRun({ launchId: result.launch_id, workspace, host: null, status: null, statusError: null, progress: NO_PROGRESS, stage: 'starting', outcome: null, timing: { clickedAt, acknowledgedAt, recordSeenAt: null, terminalAt: null, refreshedAt: null } });
    } catch (error) {
      setRefusal({ code: 'HOST_ERROR', message: error instanceof Error ? error.message : String(error), blockers: [] });
    } finally {
      startingRef.current = false;
      setStarting(false);
    }
  }, [port, workspace, goal, refreshPreflight]);

  // Follow the launch. One chain of timeouts per launch id; it ends the moment the outcome is durable.
  const launchId = run && run.stage !== 'finished' ? run.launchId : null;
  const runWorkspace = run?.workspace ?? null;
  useEffect(() => {
    if (!port || launchId === null || runWorkspace === null) return undefined;
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const began = performance.now();
    const tick = async () => {
      const [hostState, status] = await Promise.all([port.state().catch(() => null), provider.launchStatus(runWorkspace, launchId)]);
      if (!live) return;
      const notRecorded = !status.ok && status.error.code === 'LAUNCH_NOT_FOUND';
      const data = status.ok ? status.data : null;
      let progress = runRef.current?.progress ?? NO_PROGRESS;
      let more = false;
      const evidenceRunId = data?.launch.run?.run_id ?? null;
      if (data && evidenceRunId && data.evidence.state !== 'absent' && (data.evidence.line_count ?? 0) > progress.lastSeq) {
        const page = await provider.evidenceRecords(evidenceRunId, { fromSeq: progress.lastSeq + 1, limit: PAGE });
        if (!live) return;
        if (page.ok) {
          const records = (page.data.records as unknown as Array<Record<string, unknown>>).map(brief).filter((b) => Number.isFinite(b.seq));
          const kinds = { ...progress.kinds };
          for (const r of records) kinds[r.kind] = (kinds[r.kind] ?? 0) + 1;
          progress = { lastSeq: records.length ? Math.max(progress.lastSeq, ...records.map((r) => r.seq)) : progress.lastSeq, kinds, recent: [...progress.recent, ...records].slice(-RECENT_MAX), error: null };
          more = Boolean(page.data.next_cursor);
        } else {
          progress = { ...progress, error: `${page.error.code}: ${page.error.message}` };
        }
      }
      let outcome = deriveOutcome(data, notRecorded, hostState);
      // A conclusion drawn from silence (not sealed by the process itself) must be seen by two consecutive polls: a status read and a process-exit read that
      // straddle the process's own final write must never be taken as 'the run did not finish'.
      const uncertain = outcome !== null && (data === null || data.launch.phase !== 'ENDED');
      const signature = uncertain && outcome ? `${outcome.kind}|${hostState?.exit?.code ?? ''}|${hostState?.exit?.signal ?? ''}` : null;
      const confirmed = signature !== null && pendingRef.current === signature;
      pendingRef.current = uncertain ? signature : null;
      if (uncertain && !confirmed) outcome = null;
      const now = performance.now();
      setRun((prev) => {
        if (!prev || prev.launchId !== launchId) return prev;
        const stage: RunStage = outcome ? 'finished' : data && data.launch.phase === 'RUNNING' ? 'running' : 'starting';
        return {
          ...prev, host: hostState ?? prev.host, status: data ?? prev.status, progress, stage, outcome,
          statusError: status.ok || notRecorded ? null : status.error,
          timing: { ...prev.timing, recordSeenAt: prev.timing.recordSeenAt ?? (data ? now : null), terminalAt: outcome ? now : null },
        };
      });
      if (outcome) {
        // The outcome is durable: refresh the cockpit through the ordinary snapshot path, then show the finished run if the process sealed its trace id.
        setNonce((n) => n + 1); // the run changed what the folder is (recovery state, history): ask Kriya for the preflight again
        // Not tied to this effect's lifetime: the state change above re-runs the effect (its cleanup marks it not live), but the refresh must still finish.
        void (async () => {
          void refreshWorkspaceRef.current(); // the folder's own facts (.kriya, trace database, Git state) changed during the run
          await acquireRef.current(runWorkspace);
          if (outcome.traceRunId) selection.dispatch({ type: 'selectRun', runId: outcome.traceRunId });
          setRun((prev) => (prev && prev.launchId === launchId ? { ...prev, timing: { ...prev.timing, refreshedAt: performance.now() } } : prev));
        })();
        return;
      }
      const hidden = typeof document !== 'undefined' && document.hidden;
      const delay = more || pendingRef.current !== null ? Math.min(intervals.visibleMs, 250) : hidden ? intervals.hiddenMs : now - began > intervals.slowAfterMs ? intervals.slowMs : intervals.visibleMs;
      timer = setTimeout(() => { void tick(); }, delay);
    };
    void tick();
    return () => { live = false; if (timer) clearTimeout(timer); };
  }, [port, provider, launchId, runWorkspace, selection, intervals.hiddenMs, intervals.slowMs, intervals.slowAfterMs, intervals.visibleMs]);

  const dismiss = useCallback(() => setRun((prev) => (prev && prev.stage === 'finished' ? null : prev)), []);
  const locked = starting || (run !== null && run.stage !== 'finished');
  const value = useMemo<ExecutionApi>(() => ({ available: port !== null, goal, setGoal, preflight, refreshPreflight, starting, refusal, start, run, locked, dismiss }),
    [port, goal, preflight, refreshPreflight, starting, refusal, start, run, locked, dismiss]);
  return <ExecutionContext.Provider value={value}>{children}</ExecutionContext.Provider>;
}
