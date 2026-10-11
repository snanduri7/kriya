/**
 * GUI-F-A1: ONE decision of how the host answers KUP queries, consulted by both IPC handlers (hostInfo and query), so
 * the trust strip can never claim a host state the query handler does not use. Pure: no Electron, no filesystem of
 * its own (the caller injects `exists`).
 *
 *   fixture  D-9 holds: KRIYA_UI_ALLOW_REAL_KRIYA is not '1', or no kriya executable is configured
 *   real     the gate is lifted AND the configured executable exists
 *   packaged a shipped app has NO fixture stand-in (the fake Kriya is not in the bundle, GUI-APP-REAL-DEFAULT-001): it is always real, or an error
 *            that says what to set. It never depends on an environment variable a double-click launch cannot carry.
 *   error    the gate is lifted and an executable is configured, but it does not exist: every KUP call is a typed
 *            HOST_ERROR until the setting is fixed (the same contract as an invalid configuration directory, F-5) -
 *            never a silent fall-back to the fixture stand-in behind a "real host" trust strip.
 */
export type HostMode =
  | { mode: 'fixture' }
  | { mode: 'real'; executable: string }
  | { mode: 'error'; message: string };

export function resolveHostMode(allowReal: boolean, configured: string | null | undefined, exists: (path: string) => boolean, packaged = false): HostMode {
  if (packaged && !configured) return { mode: 'error', message: 'no Kriya executable is configured; set the Kriya executable (absolute path to the kriya command) in Settings' };
  if (!packaged && (!allowReal || !configured)) return { mode: 'fixture' };
  if (!configured || !exists(configured)) return { mode: 'error', message: `the configured kriya executable does not exist: ${configured}` };
  return { mode: 'real', executable: configured };
}

/** What HostInfo.fixtureMode reports for a mode: true only while the stand-in actually answers. */
export function isFixtureMode(mode: HostMode): boolean {
  return mode.mode === 'fixture';
}
