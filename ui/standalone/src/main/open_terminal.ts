/**
 * "Open Terminal Here": decide WHAT the host may start. The target is only ever the folder the HOST has open (never a renderer-supplied path), in its canonical real path - the same plan "Open in Finder"
 * uses (``planOpenFolder``: the folder must still exist, be a directory and not be an application package). The program is a fixed table entry, never run data: on macOS the system ``open`` with the
 * fixed arguments ``-a Terminal <folder>``, which opens a terminal window in that folder. NOTHING is executed in that terminal: no command is typed, pasted or run - the person decides whether to paste the
 * verified run command. Pure planning (the filesystem and the platform are injected); main.ts is the one place that spawns, with no shell.
 */
import { planOpenFolder, type OpenFolderFs } from './open_folder';

export type OpenTerminalPlan = { ok: true; binary: string; argv: string[]; path: string } | { ok: false; reason: string };

export function planOpenTerminal(current: string | null, fs: OpenFolderFs, platform: string = process.platform): OpenTerminalPlan {
  const folder = planOpenFolder(current, fs);
  if (!folder.ok) return { ok: false, reason: folder.reason };
  if (platform !== 'darwin') return { ok: false, reason: 'opening a terminal from here is only available on macOS in this version; open a terminal in the project folder yourself' };
  return { ok: true, binary: '/usr/bin/open', argv: ['-a', 'Terminal', folder.path], path: folder.path };
}
