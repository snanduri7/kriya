/**
 * The ONE place the product identity lives for the main process: name, window title, application menu, About panel and the
 * userData rule. Pure data and pure functions (no Electron import), so tests can assert that the word "Electron" is never a
 * product name anywhere a user can see. The bundle identity (Info.plist: menu-bar title, Dock label, helper apps) is produced
 * from the same constants by scripts/mac-app.mjs; test/identity.test.ts keeps the two in step.
 */
import { join } from 'node:path';
import type { MenuItemConstructorOptions } from 'electron';

export const PRODUCT_NAME = 'Kriya';
export const WINDOW_TITLE = 'Kriya';
export const BUNDLE_ID = 'com.kriya.app';
export const COPYRIGHT = 'Kriya';

/** Settings written by every earlier build live under this directory name (the former package name); renaming the product must not orphan them. */
export const LEGACY_USER_DATA_SEGMENTS = ['@kriya-ui', 'standalone'] as const;

/** userData stays where earlier builds kept it, unless the operator chose a directory with --user-data-dir (which always wins). */
export function resolveUserData(appData: string, userDataSwitchPresent: boolean): string | null {
  return userDataSwitchPresent ? null : join(appData, ...LEGACY_USER_DATA_SEGMENTS);
}

export function aboutPanelOptions(version: string): { applicationName: string; applicationVersion: string; version: string; copyright: string } {
  return { applicationName: PRODUCT_NAME, applicationVersion: version, version, copyright: COPYRIGHT };
}

/** The macOS application menu. Labels are explicit (`About Kriya`, `Hide Kriya`, `Quit Kriya`), never derived from the Electron binary's name. */
export interface MenuCommands { openFolder(): void; newProject(): void }
const NO_COMMANDS: MenuCommands = { openFolder: () => undefined, newProject: () => undefined };

export function applicationMenuTemplate(isMac: boolean, name: string = PRODUCT_NAME, commands: MenuCommands = NO_COMMANDS): MenuItemConstructorOptions[] {
  const appMenu: MenuItemConstructorOptions = {
    label: name,
    submenu: [
      { role: 'about', label: `About ${name}` },
      { type: 'separator' },
      { role: 'services', label: 'Services' },
      { type: 'separator' },
      { role: 'hide', label: `Hide ${name}` },
      { role: 'hideOthers', label: 'Hide Others' },
      { role: 'unhide', label: 'Show All' },
      { type: 'separator' },
      { role: 'quit', label: `Quit ${name}` },
    ],
  };
  // File > Open Folder... (Cmd+O / Ctrl+O): the same action as the welcome screen and the toolbar. It only asks the renderer to open the chooser.
  const newProject: MenuItemConstructorOptions = { label: 'New Project…', accelerator: 'CmdOrCtrl+N', click: () => commands.newProject() };
  const openFolder: MenuItemConstructorOptions = { label: 'Open Folder…', accelerator: 'CmdOrCtrl+O', click: () => commands.openFolder() };
  const file: MenuItemConstructorOptions = isMac
    ? { label: 'File', submenu: [newProject, openFolder, { type: 'separator' }, { role: 'close', label: 'Close Window' }] }
    : { label: 'File', submenu: [newProject, openFolder, { type: 'separator' }, { role: 'quit', label: `Quit ${name}` }] };
  const edit: MenuItemConstructorOptions = { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] };
  const view: MenuItemConstructorOptions = { label: 'View', submenu: [{ role: 'togglefullscreen' }] };
  const windowMenu: MenuItemConstructorOptions = { label: 'Window', role: 'windowMenu' };
  return isMac ? [appMenu, file, edit, view, windowMenu] : [file, edit, view, windowMenu];
}
