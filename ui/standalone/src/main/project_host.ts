/**
 * New Project, host side. Pure: no Electron import (the native chooser and the Kriya call are injected), so every rule is testable.
 *
 * The renderer never names WHERE a project is created and never creates anything itself. The flow is two typed steps:
 *   1. chooseParent: the NATIVE folder chooser runs here; the renderer receives an opaque single-purpose TOKEN (and the chosen path for display only);
 *   2. create({token, name}): the host looks the parent up by token and asks KRIYA (`workspace.create`) to make the folder. Kriya validates the name and
 *      performs the one `mkdir`; nothing in this module touches the filesystem or builds a path.
 * A created folder is then an approved workspace (it was made on the person's explicit request, in a folder the person chose), opened through the ordinary
 * `workspace.describe` path like any other.
 */
import { validateWorkspacePath } from './ipc_contract';

export const PROJECT_TOKEN_RE = /^pt-[0-9a-f]{32}$/;
export const PROJECT_NAME_MAX = 100;
export const TOKEN_TTL_MS = 15 * 60 * 1000;
export const TOKEN_MAX_LIVE = 4;

export interface ProjectParentChoice { chosen: boolean; token: string | null; parent: string | null; refused: 'RUN_ACTIVE' | null }
export interface ProjectCreateResult { created: boolean; path: string | null; code: string | null; message: string | null; reason: string | null }

export interface ProjectDeps {
  /** Kriya's `workspace.create` envelope for {parent, name}. */
  create(parent: string, name: string): Promise<unknown>;
  /** Marks a created folder as one the person asked for (the registry's picked set). */
  approveCreated(path: string): void;
  runActive(): boolean;
  now(): number;
  mintToken(): string;
}

const fail = (code: string, message: string, reason: string | null = null): ProjectCreateResult => ({ created: false, path: null, code, message, reason });

function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

export class ProjectHost {
  private readonly parents = new Map<string, { path: string; at: number }>();
  private readonly inFlight = new Set<string>();

  constructor(private readonly deps: ProjectDeps) {}

  private prune(): void {
    const now = this.deps.now();
    for (const [token, entry] of this.parents) if (now - entry.at > TOKEN_TTL_MS) this.parents.delete(token);
    while (this.parents.size > TOKEN_MAX_LIVE) {
      const oldest = this.parents.keys().next().value as string | undefined;
      if (oldest === undefined) break;
      this.parents.delete(oldest);
    }
  }

  /** Runs the native chooser for the parent folder. Cancellation is a normal outcome and changes nothing. */
  async chooseParent(chooser: () => Promise<string | null>): Promise<ProjectParentChoice> {
    if (this.deps.runActive()) return { chosen: false, token: null, parent: null, refused: 'RUN_ACTIVE' };
    const path = await chooser();
    if (path === null) return { chosen: false, token: null, parent: null, refused: null };
    const valid = validateWorkspacePath(path);
    if (!valid.ok) return { chosen: false, token: null, parent: null, refused: null };
    const token = this.deps.mintToken();
    this.parents.set(token, { path: valid.value, at: this.deps.now() });
    this.prune();
    return { chosen: true, token, parent: valid.value, refused: null };
  }

  async create(raw: unknown): Promise<ProjectCreateResult> {
    if (!isObject(raw) || Object.keys(raw).sort().join() !== 'name,token') return fail('INVALID_REQUEST', 'a project request has exactly a token and a name');
    const { token, name } = raw;
    if (typeof token !== 'string' || !PROJECT_TOKEN_RE.test(token)) return fail('INVALID_REQUEST', 'the parent folder token is not valid');
    if (typeof name !== 'string' || name.length < 1 || name.length > PROJECT_NAME_MAX * 2 || name.includes('\0')) return fail('INVALID_REQUEST', `the project name must have 1 to ${PROJECT_NAME_MAX} characters`, 'invalid_name');
    if (this.deps.runActive()) return fail('RUN_ACTIVE', 'a run is in progress; a project cannot be created until it ends');
    this.prune();
    const entry = this.parents.get(token);
    if (!entry) return fail('PARENT_EXPIRED', 'the parent folder choice has expired; choose the folder again');
    if (this.inFlight.has(token)) return fail('BUSY', 'this project is already being created');
    this.inFlight.add(token);
    try {
      let envelope: unknown;
      try {
        envelope = await this.deps.create(entry.path, name);
      } catch (error) {
        return fail('HOST_ERROR', `Kriya could not be asked to create the project: ${error instanceof Error ? error.message : String(error)}`);
      }
      if (!isObject(envelope) || !('error' in envelope) || !('data' in envelope)) return fail('INVALID_RESPONSE', 'Kriya returned something that is not a KUP envelope');
      if (isObject(envelope.error)) {
        const e = envelope.error;
        return fail(typeof e.code === 'string' ? e.code : 'HOST_ERROR', typeof e.message === 'string' ? e.message : 'the project was not created', typeof e.reason === 'string' ? e.reason : null);
      }
      const data = envelope.data;
      const path = isObject(data) && typeof data.workspace === 'string' ? validateWorkspacePath(data.workspace) : null;
      if (!path || !path.ok) return fail('INVALID_RESPONSE', 'Kriya did not name the created folder');
      this.deps.approveCreated(path.value);
      this.parents.delete(token); // single use: a created project consumes its parent choice
      return { created: true, path: path.value, code: null, message: null, reason: null };
    } finally {
      this.inFlight.delete(token);
    }
  }
}
