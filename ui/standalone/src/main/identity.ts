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
export function applicationMenuTemplate(isMac: boolean, name: string = PRODUCT_NAME): MenuItemConstructorOptions[] {
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
  const edit: MenuItemConstructorOptions = { label: 'Edit', submenu: [{ role: 'undo' }, { role: 'redo' }, { type: 'separator' }, { role: 'cut' }, { role: 'copy' }, { role: 'paste' }, { role: 'selectAll' }] };
  const view: MenuItemConstructorOptions = { label: 'View', submenu: [{ role: 'togglefullscreen' }] };
  const windowMenu: MenuItemConstructorOptions = { label: 'Window', role: 'windowMenu' };
  return isMac ? [appMenu, edit, view, windowMenu] : [{ label: 'File', submenu: [{ role: 'quit', label: `Quit ${name}` }] }, edit, view, windowMenu];
}
