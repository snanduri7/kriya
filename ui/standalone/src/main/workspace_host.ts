/**
 * Workspace lifecycle on the host side (GUI M5 Open Folder). Pure: no Electron import (the native chooser is injected), so the rules are testable.
 *
 * The host's whole job is to obtain a PATH from the person and remember which paths the person has chosen. It decides nothing about the folder:
 * identity, repository facts, cleanliness and state all come from Kriya (`workspace.describe`). Authority rule: the renderer can never name an
 * arbitrary folder to Kriya. A `workspace` argument (and the current-workspace setting) is accepted only for a folder the person picked in the
 * native chooser this session, or one remembered from an earlier pick; remembering a path is not trusting it - Kriya validates it again on every open.
 */
import { LIMITS, RECENT_WORKSPACES_MAX, validateWorkspacePath, type KupRequest, type ValidationResult } from './ipc_contract';

export interface WorkspaceStore {
  getRecent(): string[];
  setRecent(list: string[]): void;
  getCurrent(): string | null;
  setCurrent(path: string | null): void;
}

export interface WorkspaceChoice { chosen: boolean; path: string | null; refused: 'RUN_ACTIVE' | null }

export interface RegistryOptions {
  /** The M1 inspector build only (ui_mode.ts): its typed "Recovery workspace" setting keeps working (see isApproved / refuseSetting / close). Never true for the cockpit. */
  legacyInspector?: boolean;
  /** true while a run this host started is in progress: the workspace cannot change then (M6-H). */
  runActive?: () => boolean;
}

export class WorkspaceRegistry {
  private readonly picked = new Set<string>();

  private readonly legacyInspector: boolean;
  private readonly runActive: () => boolean;

  constructor(private readonly store: WorkspaceStore, private readonly max: number = RECENT_WORKSPACES_MAX, options: RegistryOptions = {}) {
    this.legacyInspector = options.legacyInspector === true;
    this.runActive = options.runActive ?? (() => false);
  }

  recent(): string[] { return this.store.getRecent().slice(0, this.max); }
  current(): string | null { return this.store.getCurrent(); }

  /** Runs the native chooser. Cancellation is a normal outcome and changes nothing. */
  async choose(chooser: () => Promise<string | null>): Promise<WorkspaceChoice> {
    // M6-H: with a run in progress the dialog is not even shown - the workspace that owns the run stays the open one.
    if (this.runActive()) return { chosen: false, path: null, refused: 'RUN_ACTIVE' };
    const path = await chooser();
    if (path === null) return { chosen: false, path: null, refused: null };
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return { chosen: false, path: null, refused: null };
    this.picked.add(valid.value);
    return { chosen: true, path: valid.value, refused: null };
  }

  /** A folder Kriya created at the person's explicit request (New Project) is an approved workspace, like one chosen with the native chooser. */
  approveCreated(path: string): boolean {
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return false;
    this.picked.add(valid.value);
    return true;
  }

  /**
   * Approved = the person chose it with the native chooser this session, or it is remembered. M1 COMPATIBILITY (legacyInspector builds only): the
   * folder the person typed into the inspector's own "Recovery workspace" setting, as the accepted M1 behaved. A cockpit build never takes this branch.
   */
  isApproved(path: string): boolean {
    return this.picked.has(path) || this.recent().includes(path) || (this.legacyInspector && this.store.getCurrent() === path);
  }

  /** Makes an approved folder the current workspace and the most recent one. Kriya has already validated it (the renderer calls this after workspace.describe). */
  remember(path: unknown): ValidationResult<string[]> {
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return valid;
    if (!this.isApproved(valid.value)) return { ok: false, message: 'that folder was not chosen with Open Folder' };
    if (this.runActive() && this.store.getCurrent() !== valid.value) return { ok: false, message: 'a run is in progress; the workspace cannot change until it ends' };
    const list = [valid.value, ...this.recent().filter((p) => p !== valid.value)].slice(0, this.max);
    this.store.setRecent(list);
    this.store.setCurrent(valid.value);
    return { ok: true, value: list };
  }

  /** Removes a remembered folder (a stale entry the person dismissed). If it was the current workspace the workspace is closed. */
  forget(path: unknown): ValidationResult<string[]> {
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return valid;
    if (this.runActive() && this.store.getCurrent() === valid.value) return { ok: false, message: 'a run is in progress; the workspace cannot be closed until it ends' };
    const list = this.recent().filter((p) => p !== valid.value);
    this.store.setRecent(list);
    this.picked.delete(valid.value);
    if (this.store.getCurrent() === valid.value) this.store.setCurrent(null);
    return { ok: true, value: list };
  }

  /** The `workspace` argument a request carries, if any. */
  static workspaceOf(request: KupRequest): string | null {
    return 'workspace' in request && typeof request.workspace === 'string' ? request.workspace : null;
  }

  /** null = allowed. A request that names a workspace is allowed only for an approved folder. */
  refuse(request: KupRequest): string | null {
    const workspace = WorkspaceRegistry.workspaceOf(request);
    if (workspace === null || this.isApproved(workspace)) return null;
    return `${request.operation}: that folder was not chosen with Open Folder (a workspace argument must come from the native chooser or the recent list)`;
  }

  /** Application launch: no folder is open until the person opens one. Recent entries are kept. (The M1 inspector keeps its saved setting, as it always did.) */
  close(): void { if (!this.legacyInspector) this.store.setCurrent(null); }

  /**
   * The current-workspace setting is set only through remember() - except in the M1 inspector build, whose typed setting is accepted M1 behaviour.
   * Clearing it (closing the workspace) is always allowed, but never while a run this host started is in progress.
   */
  refuseSetting(value: string | null): string | null {
    if (this.runActive() && value !== this.store.getCurrent()) return 'a run is in progress; the workspace cannot change until it ends';
    if (value === null || this.legacyInspector) return null;
    return 'the workspace is chosen with Open Folder';
  }
}

export const PATH_MAX = LIMITS.pathMax;
