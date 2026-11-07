/**
 * "Open in Finder": decide WHAT the host may hand to the operating system's file manager. The target is only ever the folder the HOST has open (never a renderer-supplied path), resolved to its
 * canonical real path, and it must still be a directory. A directory that is a macOS/Windows application or document PACKAGE (`Foo.app`, `Foo.workflow`, ...) is refused: `shell.openPath` on such a
 * folder would LAUNCH it instead of showing its contents. This module is pure (the filesystem is injected) so every refusal is unit-tested; main.ts is the one place that calls `shell.openPath`.
 */
import { basename, extname } from 'node:path';

export interface OpenFolderFs {
  /** Canonical real path (symlinks resolved); throws when the folder is gone. */
  realpath(path: string): string;
  isDirectory(path: string): boolean;
}

export type OpenFolderPlan = { ok: true; path: string } | { ok: false; reason: string };

/** Directory extensions an OS treats as a launchable or opaque package rather than a plain folder. Lower-case, with the dot. */
export const PACKAGE_EXTENSIONS: ReadonlySet<string> = new Set([
  '.app', '.appex', '.bundle', '.framework', '.plugin', '.kext', '.prefpane', '.saver', '.workflow', '.action', '.xpc', '.component', '.wdgt', '.pkg', '.mpkg', '.qlgenerator', '.mdimporter', '.systemextension',
]);

export function planOpenFolder(current: string | null, fs: OpenFolderFs): OpenFolderPlan {
  if (current === null) return { ok: false, reason: 'no workspace is open' };
  let real: string;
  try { real = fs.realpath(current); } catch { return { ok: false, reason: 'the folder is no longer there' }; }
  if (!fs.isDirectory(real)) return { ok: false, reason: 'the folder is no longer there' };
  if (PACKAGE_EXTENSIONS.has(extname(basename(real)).toLowerCase())) return { ok: false, reason: 'this folder is an application package, which Finder would launch instead of showing; it was not opened' };
  return { ok: true, path: real };
}

/** `shell.openPath` resolves to '' on success and to an error message otherwise (it does not throw). */
export function openFolderResult(openPathAnswer: string): { revealed: boolean; reason: string | null } {
  const text = openPathAnswer.trim();
  return text === '' ? { revealed: true, reason: null } : { revealed: false, reason: `the system could not open the folder (${text.slice(0, 200)})` };
}
