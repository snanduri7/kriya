/**
 * The workspace lifecycle (GUI M5): Open Folder -> Kriya validates -> the cockpit shows that workspace. Lives in shared core, not in any feature
 * module: Runs, Workspace and the rest simply react to the workspace this context reports.
 *
 * Authority. The host's native chooser returns a PATH and nothing else. Everything the cockpit knows about the folder - canonical path, identity,
 * Git facts, recovery status, state directory - is Kriya's answer to `workspace.describe`; this file never inspects a path, never decides whether a
 * folder is empty, new, existing, Git or modified, and never shows a fact Kriya did not report. A folder that Kriya refuses (missing, not a directory,
 * unreadable) is an error notice and the workspace that was open stays open. Opening is navigation only: no run starts and nothing is written to the folder.
 *
 * Remembered folders are convenience, not trust: opening one from the recent list runs the same validation, and a stale entry fails cleanly and can be removed.
 */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import type { WorkspaceDescribe } from '../../model/kup';
import type { Provenance } from './evidence';
import type { UIProvider } from './provider';

export interface WorkspaceNotice {
  /** The Kriya error code (or HOST_ERROR) that refused the folder, verbatim. */
  code: string;
  message: string;
  /** The folder that was being opened. */
  path: string;
}

export interface OpenWorkspace { path: string; facts: WorkspaceDescribe; provenance: Provenance }

export interface WorkspaceState {
  /** false until the first launch decision (a still-valid setting or the welcome screen) is made. */
  ready: boolean;
  current: OpenWorkspace | null;
  recent: string[];
  /** The last refused open, until the next attempt or dismissal. */
  notice: WorkspaceNotice | null;
  busy: boolean;
  /** Whether this host has a native chooser at all (a browser test host may not). */
  canChoose: boolean;
}

export interface WorkspaceApi extends WorkspaceState {
  /** The native chooser, then the validated open. Cancelling changes nothing. */
  openFolder(): Promise<void>;
  /** The same validated open for a remembered folder. */
  openRecent(path: string): Promise<void>;
  forgetRecent(path: string): Promise<void>;
  dismissNotice(): void;
  /** Ask Kriya about the open folder again (a run changes what is in it: .kriya, the trace database, the Git state). Never changes which folder is open. */
  refresh(): Promise<void>;
  /** Leave the current workspace (back to the welcome screen). Remembered folders are kept. */
  close(): Promise<void>;
}

const INITIAL: WorkspaceState = { ready: false, current: null, recent: [], notice: null, busy: false, canChoose: false };
const WorkspaceContext = createContext<WorkspaceApi | null>(null);

export function useWorkspace(): WorkspaceApi {
  const value = useContext(WorkspaceContext);
  if (value === null) throw new Error('useWorkspace used outside the cockpit providers');
  return value;
}

/** The workspace path when one is open, else null (for modules that only need to react). */
export const useWorkspacePath = (): string | null => useWorkspace().current?.path ?? null;

export function WorkspaceProvider({ provider, onChanged, children }: { provider: UIProvider; onChanged?: (path: string | null) => void; children: ReactNode }) {
  const desktop = provider.desktop;
  const [state, setState] = useState<WorkspaceState>({ ...INITIAL, canChoose: desktop !== null });
  const openingRef = useRef(false);
  const changed = useRef(onChanged);
  useEffect(() => { changed.current = onChanged; });
  // The folder that is open, as a ref so a change can be told from a first open: only LEAVING one workspace for another (or for none) clears what
  // belonged to it. Opening the first folder, or re-opening the same one, clears nothing.
  const openPath = useRef<string | null>(null);
  const settle = useCallback((next: string | null) => {
    const previous = openPath.current;
    openPath.current = next;
    if (previous !== null && previous !== next) changed.current?.(next);
  }, []);

  /** Validate with Kriya, then commit. Returns true when the folder is now open. The previous workspace stays open on any failure. */
  const open = useCallback(async (path: string, options: { remember: boolean }): Promise<boolean> => {
    if (openingRef.current) return false;
    openingRef.current = true;
    setState((s) => ({ ...s, busy: true, notice: null }));
    try {
      const described = await provider.describeWorkspace(path);
      if (!described.ok) {
        setState((s) => ({ ...s, busy: false, notice: { code: described.error.code, message: described.error.message, path } }));
        return false;
      }
      let recent: string[] | null = null;
      if (options.remember && desktop) {
        try {
          recent = await desktop.remember(path);
        } catch (error) {
          setState((s) => ({ ...s, busy: false, notice: { code: 'HOST_ERROR', message: error instanceof Error ? error.message : String(error), path } }));
          return false;
        }
      }
      setState((s) => ({ ...s, busy: false, notice: null, current: { path, facts: described.data, provenance: described.provenance }, ...(recent ? { recent } : {}) }));
      settle(path);
      return true;
    } finally {
      openingRef.current = false;
    }
  }, [provider, desktop, settle]);

  // Launch: the host decides whether a workspace is already open (the desktop app starts with none; a development host may preset one). A preset one
  // is validated like any other; if Kriya refuses it the person lands on the welcome screen with the reason.
  useEffect(() => {
    let live = true;
    void (async () => {
      const [preset, recent] = await Promise.all([provider.getSetting('workspacePath'), desktop ? desktop.recent() : Promise.resolve([] as string[])]);
      if (!live) return;
      setState((s) => ({ ...s, recent, ready: preset == null }));
      if (preset != null) {
        await open(preset, { remember: false });
        if (live) setState((s) => ({ ...s, ready: true }));
      }
    })();
    return () => { live = false; };
  }, [provider, desktop, open]);

  const openFolder = useCallback(async () => {
    if (!desktop || openingRef.current) return;
    const choice = await desktop.choose();
    if (choice.refused === 'RUN_ACTIVE') {
      // A run this host started owns the open workspace: the dialog was not shown and nothing changes.
      setState((s) => ({ ...s, notice: { code: 'RUN_ACTIVE', message: 'A run is in progress; the workspace cannot change until it ends.', path: s.current?.path ?? '' } }));
      return;
    }
    if (!choice.chosen || choice.path === null) return; // cancelling is normal: nothing changes
    await open(choice.path, { remember: true });
  }, [desktop, open]);

  const openRecent = useCallback(async (path: string) => { await open(path, { remember: true }); }, [open]);

  const forgetRecent = useCallback(async (path: string) => {
    if (!desktop) return;
    const recent = await desktop.forget(path);
    setState((s) => ({ ...s, recent, notice: s.notice?.path === path ? null : s.notice, current: s.current?.path === path ? null : s.current }));
    if (openPath.current === path) settle(null);
  }, [desktop, settle]);

  const refresh = useCallback(async () => {
    const path = openPath.current;
    if (path === null) return;
    const described = await provider.describeWorkspace(path);
    if (!described.ok || openPath.current !== path) return; // a refused re-read keeps the last facts; the folder stays open
    setState((s) => (s.current && s.current.path === path ? { ...s, current: { path, facts: described.data, provenance: described.provenance } } : s));
  }, [provider]);

  const dismissNotice = useCallback(() => setState((s) => ({ ...s, notice: null })), []);

  const close = useCallback(async () => {
    await provider.setSetting('workspacePath', null);
    setState((s) => ({ ...s, current: null, notice: null }));
    settle(null);
  }, [provider, settle]);

  // The application menu (File > Open Folder...) and the welcome screen are the same action.
  useEffect(() => {
    if (!desktop) return undefined;
    return desktop.onMenuCommand((command) => { if (command === 'openFolder') void openFolder(); });
  }, [desktop, openFolder]);

  const api = useMemo<WorkspaceApi>(() => ({ ...state, openFolder, openRecent, forgetRecent, dismissNotice, refresh, close }), [state, openFolder, openRecent, forgetRecent, dismissNotice, refresh, close]);
  return <WorkspaceContext.Provider value={api}>{children}</WorkspaceContext.Provider>;
}
