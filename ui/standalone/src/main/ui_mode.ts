/**
 * Which renderer bundle this host loaded: the Run Cockpit or the M1 inspector (GUI M6 compatibility). Decided by the build, not by the renderer: the
 * Vite build writes ui-mode.json beside index.html (vite.config.ts). Anything missing or unexpected is the COCKPIT - the strict default; only an
 * explicit "inspector" marker from a non-cockpit build enables the narrow M1 compatibility in workspace_host.ts.
 */
export type UiMode = 'cockpit' | 'inspector';

export function resolveUiMode(read: () => string | null): UiMode {
  try {
    const text = read();
    if (text === null) return 'cockpit';
    const parsed = JSON.parse(text) as { ui?: unknown };
    return parsed && parsed.ui === 'inspector' ? 'inspector' : 'cockpit';
  } catch {
    return 'cockpit';
  }
}
