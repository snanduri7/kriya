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

export interface WorkspaceChoice { chosen: boolean; path: string | null }

export class WorkspaceRegistry {
  private readonly picked = new Set<string>();

  constructor(private readonly store: WorkspaceStore, private readonly max: number = RECENT_WORKSPACES_MAX) {}

  recent(): string[] { return this.store.getRecent().slice(0, this.max); }
  current(): string | null { return this.store.getCurrent(); }

  /** Runs the native chooser. Cancellation is a normal outcome and changes nothing. */
  async choose(chooser: () => Promise<string | null>): Promise<WorkspaceChoice> {
    const path = await chooser();
    if (path === null) return { chosen: false, path: null };
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return { chosen: false, path: null };
    this.picked.add(valid.value);
    return { chosen: true, path: valid.value };
  }

  isApproved(path: string): boolean { return this.picked.has(path) || this.recent().includes(path); }

  /** Makes an approved folder the current workspace and the most recent one. Kriya has already validated it (the renderer calls this after workspace.describe). */
  remember(path: unknown): ValidationResult<string[]> {
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return valid;
    if (!this.isApproved(valid.value)) return { ok: false, message: 'that folder was not chosen with Open Folder' };
    const list = [valid.value, ...this.recent().filter((p) => p !== valid.value)].slice(0, this.max);
    this.store.setRecent(list);
    this.store.setCurrent(valid.value);
    return { ok: true, value: list };
  }

  /** Removes a remembered folder (a stale entry the person dismissed). If it was the current workspace the workspace is closed. */
  forget(path: unknown): ValidationResult<string[]> {
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return valid;
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

  /** No folder is open (application launch, or the person closed the workspace). Recent entries are kept. */
  close(): void { this.store.setCurrent(null); }

  /** The current-workspace setting may be cleared at any time but only set through remember(). */
  refuseSetting(value: string | null): string | null {
    return value === null ? null : 'the workspace is chosen with Open Folder';
  }
}

export const PATH_MAX = LIMITS.pathMax;
