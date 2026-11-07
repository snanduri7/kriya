/**
 * Recover Project, host side. Pure: no Electron, no `child_process` - the Kriya call, the native confirmation, the open-workspace check and the run-active check are injected, so every
 * rule is tested with controlled executors.
 *
 * What this IS: a narrow proxy for Kriya's own `runs recover` on the folder THIS host has open. The request carries nothing (no path, no flag): the host names the folder, a native
 * confirmation is shown first (recover is the only action here that writes), and Kriya's own report - the recovery status AFTER - is passed through. `--complete-partial` is never
 * sent (kriya_argv.ts recoverArgv): source files are never changed from here.
 *
 * What it is NOT: a judge of cleanliness. `recovered` is true only when Kriya itself reports the workspace CLEAN afterwards; anything else is reported with Kriya's own status word.
 */
import { basename } from 'node:path';

/** The native confirmation: Cancel first, default and Escape; only `Recover Project` (index 1) commits. Its text is a function of the folder NAME alone - no renderer text, path or flag. */
export const RECOVER_BUTTONS = ['Cancel', 'Recover Project'] as const;
export const RECOVER_INDEX = 1;
export interface RecoverDialog { type: 'question'; title: string; message: string; detail: string; buttons: string[]; defaultId: number; cancelId: number; noLink: true }
export function recoverDialog(workspace: string): RecoverDialog {
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is being removed
  const name = basename(workspace).replace(/[\u0000-\u001f\u007f]/g, ' ').trim();
  const label = name.length > 80 ? `${name.slice(0, 79)}…` : name || 'this project';
  return {
    type: 'question', title: 'Recover Project', message: `Recover ${label}?`,
    detail: 'Kriya will settle the records of an interrupted run in this folder so a new run can start. It does not change your source files.',
    buttons: [...RECOVER_BUTTONS], defaultId: 0, cancelId: 0, noLink: true,
  };
}
export const isRecoverResponse = (response: number): boolean => response === RECOVER_INDEX;

export interface RecoverResult {
  recovered: boolean;
  /** Kriya's recovery status after the attempt (CLEAN, RECOVERY_AVAILABLE, ...), or null when Kriya gave none. Never invented. */
  status: string | null;
  /** A closed refusal code when nothing was attempted or Kriya could not answer; null when Kriya answered. */
  code: 'WORKSPACE_NOT_OPEN' | 'RUN_ACTIVE' | 'CONFIRMATION_DECLINED' | 'RECOVER_IN_FLIGHT' | 'HOST_ERROR' | 'INVALID_RESPONSE' | null;
  message: string | null;
  /** Kriya's own error lines, when it reported any (redacted by Kriya; bounded here). */
  errors: string[];
}

export interface RecoverDeps {
  /** Kriya's `runs recover --workspace <folder> --json` report (the parsed JSON), or throws. */
  recover(workspace: string): Promise<unknown>;
  /** The NATIVE confirmation, shown by the main process. */
  confirm(workspace: string): Promise<boolean>;
  currentWorkspace(): string | null;
  runActive(): boolean;
  log?(line: string): void;
}

const refused = (code: NonNullable<RecoverResult['code']>, message: string): RecoverResult => ({ recovered: false, status: null, code, message, errors: [] });
function isObject(value: unknown): value is Record<string, unknown> { return typeof value === 'object' && value !== null && !Array.isArray(value); }

export class RecoverHost {
  private inFlight = false;
  constructor(private readonly deps: RecoverDeps) {}

  async recover(): Promise<RecoverResult> {
    const workspace = this.deps.currentWorkspace();
    if (workspace === null) return refused('WORKSPACE_NOT_OPEN', 'no folder is open');
    if (this.deps.runActive()) return refused('RUN_ACTIVE', 'a run is in progress in this window; recover the project when it has ended');
    if (this.inFlight) return refused('RECOVER_IN_FLIGHT', 'a recovery is already running');
    this.inFlight = true;
    try {
      if (!(await this.deps.confirm(workspace))) return refused('CONFIRMATION_DECLINED', 'recovery was not confirmed; nothing was changed');
      let report: unknown;
      try {
        report = await this.deps.recover(workspace);
      } catch (error) {
        this.deps.log?.(`runs recover: ${error instanceof Error ? error.message : String(error)}`);
        return refused('HOST_ERROR', 'Kriya could not be asked to recover the project; its state is unchanged as far as is known - check it before trying again');
      }
      if (!isObject(report) || !isObject(report.after) || typeof report.after.status !== 'string') return refused('INVALID_RESPONSE', 'Kriya returned a recovery report this version cannot read');
      const status = report.after.status;
      const errors = Array.isArray(report.errors) ? report.errors.filter((e): e is string => typeof e === 'string').slice(0, 10).map((e) => e.slice(0, 300)) : [];
      return { recovered: status === 'CLEAN' && errors.length === 0, status, code: null, message: null, errors };
    } finally {
      this.inFlight = false;
    }
  }
}
