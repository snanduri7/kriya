/**
 * Project Setup, host side. Pure: no Electron import, no `child_process` import - the Kriya call, the open-workspace check and the run-active check are injected, so every
 * rule is tested with controlled executors.
 *
 * What this module IS: a narrow proxy. It takes ONE typed request {workspace}, checks it against its closed grammar and against the folder currently open in this host,
 * refuses while a run is active, asks KRIYA (`project.initialize`) and reports Kriya's own answer.
 *
 * What it is NOT: an author of configuration. The request carries no setting, model, path or file content; Kriya writes its own recommended profile (validated by its own
 * schema), never overwrites, and approves nothing - the protected settings it introduces still go through the configuration approval and its native confirmation.
 */
import { validateInitializeProject } from './ipc_contract';

export interface InitializeProjectResult { initialized: boolean; outcome: string | null; code: string | null; message: string | null; reason: string | null; summary: Record<string, unknown> | null }

export interface ProjectSetupDeps {
  /** Kriya's `project.initialize` envelope for {workspace}. */
  initialize(workspace: string): Promise<unknown>;
  currentWorkspace(): string | null;
  runActive(): boolean;
  log?(line: string): void;
}

const refused = (code: string, message: string, reason: string | null = null): InitializeProjectResult => ({ initialized: false, outcome: null, code, message, reason, summary: null });
const text = (v: unknown): string | null => (typeof v === 'string' ? v : null);
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

export class ProjectSetupHost {
  private inFlight = false;

  constructor(private readonly deps: ProjectSetupDeps) {}

  async initialize(raw: unknown): Promise<InitializeProjectResult> {
    const request = validateInitializeProject(raw);
    if (!request.ok) return refused('INVALID_REQUEST', request.message);
    if (this.inFlight) return refused('SETUP_IN_FLIGHT', 'a setup is already being created');
    const { workspace } = request.value;
    if (this.deps.currentWorkspace() !== workspace) return refused('WORKSPACE_NOT_OPEN', 'that folder is not the workspace currently open; a project can only be set up for the open folder');
    if (this.deps.runActive()) return refused('RUN_ACTIVE', 'a run is in progress in this window; set the project up when it has finished');
    this.inFlight = true;
    try {
      let envelope: unknown;
      try {
        envelope = await this.deps.initialize(workspace);
      } catch (error) {
        this.deps.log?.(`project.initialize: ${error instanceof Error ? error.message : String(error)}`);
        return refused('HOST_ERROR', 'Kriya could not be asked to set the project up; nothing is known to have been created - check the folder before trying again');
      }
      if (!isObject(envelope) || !('error' in envelope) || !('data' in envelope)) return refused('INVALID_RESPONSE', 'Kriya returned something that is not a KUP envelope');
      if (isObject(envelope.error)) {
        const e = envelope.error;
        return refused(text(e.code) ?? 'HOST_ERROR', text(e.message) ?? 'Kriya did not set the project up', text(e.reason));
      }
      const data = envelope.data;
      if (!isObject(data) || data.outcome !== 'created') return refused('INVALID_RESPONSE', 'Kriya did not report that the configuration was created');
      return { initialized: true, outcome: 'created', code: null, message: null, reason: null, summary: isObject(data.summary) ? data.summary : null };
    } finally {
      this.inFlight = false;
    }
  }
}
