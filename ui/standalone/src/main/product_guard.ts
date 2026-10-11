/**
 * The product refuses to start with a debugger / remote-control switch (independent review P2-2). The Electron fuses (mac-app.mjs) remove `--inspect` and
 * NODE_OPTIONS from the packaged product; `--remote-debugging-port` / `--remote-debugging-pipe` are Chromium switches no fuse covers, and either one hands a
 * local process a full-control channel to the window and its privileged IPC. Pure so the rule is tested without Electron.
 */
const DEBUG_SWITCH = /^--(?:inspect(?:-brk|-port|-publish-uid)?|debug(?:-brk|-port)?|remote-debugging-(?:port|pipe|address)|remote-allow-origins|js-flags)(?:=|$)/;

export function debugSwitches(argv: readonly string[]): string[] {
  return argv.filter((a) => DEBUG_SWITCH.test(a));
}

/** The product (packaged, no dev drivers) must refuse a debug switch; a dev/evidence build is not restricted. */
export function mustRefuseStart(packaged: boolean, hasDevDrivers: boolean, argv: readonly string[]): string | null {
  if (!packaged || hasDevDrivers) return null;
  const found = debugSwitches(argv);
  return found.length ? `Kriya was started with a debugging switch (${found.join(' ')}); the application does not run with remote control enabled.` : null;
}
