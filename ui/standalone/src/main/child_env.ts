/**
 * The environment the Electron host gives the REAL `kriya` child (owner decision 2026-10-04; 08 review F-1/F-2).
 * Pure module: no Electron, no child_process. One rule, one place; tests/_kup_fixtures.py::host_child_env in the
 * Kriya checkout is an exact copy of it so the sandbox write-boundary evidence is produced under this environment.
 *
 *   PATH                     fixed, approved directories only (never the operator's PATH)
 *   PYTHONDONTWRITEBYTECODE  fixed "1": set before the interpreter imports anything, so a fresh install never writes
 *                            __pycache__ into the Kriya tree (sys.dont_write_bytecode inside the command is too late)
 *   HOME                     the operator's own HOME (Kriya's default state, authority and approval roots hang off it)
 *   KRIYA_STATE_DIR          only when the operator set it, and only an absolute path (Kriya refuses a relative one)
 *
 * Nothing else: no PYTHONPATH / PYTHONHOME, no KRIYA_TRUST_FILE or other authority-granting variable, no model or
 * provider credentials, no arbitrary variable, and no invented config-path variable - configuration discovery stays
 * Kriya's own (working directory, then install directory). Fixture knobs for the stand-in live in main.ts::fakeEnv.
 */
export const KRIYA_CHILD_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';
export const KRIYA_CHILD_ENV_KEYS = ['PATH', 'PYTHONDONTWRITEBYTECODE', 'HOME', 'KRIYA_STATE_DIR'] as const;

export type ChildEnvResult = { ok: true; env: Record<string, string> } | { ok: false; message: string };

/** Build the child environment from the OPERATOR's environment (the Electron process's own), never pass it through. */
export function buildKriyaChildEnv(operator: Readonly<Record<string, string | undefined>>): ChildEnvResult {
  const home = operator.HOME;
  if (typeof home !== 'string' || !home.startsWith('/')) return { ok: false, message: 'HOME is not set to an absolute path in the host environment; Kriya cannot resolve its default state directory' };
  const env: Record<string, string> = { PATH: KRIYA_CHILD_PATH, PYTHONDONTWRITEBYTECODE: '1', HOME: home };
  const stateDir = operator.KRIYA_STATE_DIR;
  if (stateDir !== undefined) {
    // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
    if (typeof stateDir !== 'string' || !stateDir.startsWith('/') || /[\u0000-\u001f]/.test(stateDir)) return { ok: false, message: `KRIYA_STATE_DIR must be an absolute path without control characters (got ${JSON.stringify(stateDir)})` };
    env.KRIYA_STATE_DIR = stateDir;
  }
  return { ok: true, env };
}
