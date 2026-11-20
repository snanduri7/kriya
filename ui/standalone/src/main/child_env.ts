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
 *   KRIYA_AUTHORITY_HOME     only when the operator set it in the HOST's own launch environment (owner ruling 2026-11-20), and
 *                            only a normalized absolute path. It RELOCATES Kriya's authority home (approvals, launch records,
 *                            human decisions) exactly as HOME already does by default; it grants nothing by itself: an empty
 *                            home holds no approval, and Kriya refuses a home that resolves inside the workspace. Every kriya
 *                            child (the service that approves and revokes, and the launched run) is built by this ONE function
 *                            from the same host environment, so they always agree on it. Unset = not passed (HOME decides).
 *
 * Nothing else: no PYTHONPATH / PYTHONHOME, no KRIYA_TRUST_FILE (an approval ARTIFACT named by the environment), no model or
 * provider credentials, no arbitrary variable, and no invented config-path variable - configuration discovery stays
 * Kriya's own (working directory, then install directory). Fixture knobs for the stand-in live in main.ts::fakeEnv.
 * The only input is the host process's environment as it was started: no renderer request, setting or IPC message is read here.
 */
export const KRIYA_CHILD_PATH = '/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin';
export const KRIYA_CHILD_ENV_KEYS = ['PATH', 'PYTHONDONTWRITEBYTECODE', 'HOME', 'KRIYA_STATE_DIR', 'KRIYA_AUTHORITY_HOME'] as const;

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
  const authorityHome = operator.KRIYA_AUTHORITY_HOME;
  if (authorityHome !== undefined) {
    // Strict: absolute, no control characters, already normalized (no empty, "." or ".." segment, no trailing slash) and not the
    // filesystem root. A value that fails is a refused launch, never a silent fall back to the default home.
    const segments = typeof authorityHome === 'string' && authorityHome.startsWith('/') ? authorityHome.slice(1).split('/') : [];
    // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
    if (segments.length === 0 || /[\u0000-\u001f\u007f]/.test(authorityHome) || segments.some((s) => s === '' || s === '.' || s === '..')) return { ok: false, message: `KRIYA_AUTHORITY_HOME must be a normalized absolute path without control characters (got ${JSON.stringify(authorityHome)})` };
    env.KRIYA_AUTHORITY_HOME = authorityHome;
  }
  return { ok: true, env };
}

/**
 * The CONFIGURATION DIRECTORY (08 review F-5, addendum 3): the child's working directory for EVERY kriya call, so
 * `kriya.yaml` discovery and SEC-009 classification happen in one explicit, visible place - never in Electron's own
 * launch directory (`/` from Finder, the shell's directory from a terminal). It is a host setting (`configDirectory`,
 * null = the operator's HOME), independent of the recovery workspace (`workspacePath`, which only ever travels as an
 * explicit argument: `runs status --workspace`, `traces --snapshot --workspace`). Changing one never changes the other.
 */
export type ConfigDirectorySource = 'setting' | 'default_home';
export type ConfigDirectoryResult =
  | { ok: true; directory: string; source: ConfigDirectorySource }
  | { ok: false; message: string };

export function resolveConfigDirectory(setting: string | null | undefined, operator: Readonly<Record<string, string | undefined>>, isDirectory: (path: string) => boolean): ConfigDirectoryResult {
  const source: ConfigDirectorySource = typeof setting === 'string' ? 'setting' : 'default_home';
  const candidate = source === 'setting' ? (setting as string) : operator.HOME;
  const label = source === 'setting' ? 'the configDirectory setting' : 'the default configuration directory (HOME)';
  // eslint-disable-next-line no-control-regex -- control characters are exactly what is refused
  if (typeof candidate !== 'string' || !candidate.startsWith('/') || /[\u0000-\u001f]/.test(candidate)) return { ok: false, message: `${label} must be an absolute path without control characters (got ${JSON.stringify(candidate ?? null)})` };
  if (!isDirectory(candidate)) return { ok: false, message: `${label} is not an existing directory: ${candidate}` };
  return { ok: true, directory: candidate, source };
}
