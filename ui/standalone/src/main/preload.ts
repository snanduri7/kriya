/**
 * Preload (gate A-1): exposes ONLY the typed HostAdapter messages, each bound to a fixed channel. No generic
 * ipcRenderer passthrough, no Node API, nothing else on window.
 */
import { contextBridge, ipcRenderer } from 'electron';
import { IPC_CHANNELS, MENU_CHANNEL, MENU_COMMANDS, type MenuCommand } from './ipc_contract';

const api = {
  query: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.query, request),
  openInIde: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.openInIde, request),
  copyToClipboard: (text: string) => ipcRenderer.invoke(IPC_CHANNELS.clipboardWrite, text),
  getSetting: (key: string) => ipcRenderer.invoke(IPC_CHANNELS.settingGet, key),
  setSetting: (key: string, value: unknown) => ipcRenderer.invoke(IPC_CHANNELS.settingSet, key, value),
  hostInfo: () => ipcRenderer.invoke(IPC_CHANNELS.hostInfo),
  chooseWorkspace: () => ipcRenderer.invoke(IPC_CHANNELS.chooseWorkspace),
  recentWorkspaces: () => ipcRenderer.invoke(IPC_CHANNELS.recentWorkspaces),
  rememberWorkspace: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.rememberWorkspace, path),
  forgetWorkspace: (path: string) => ipcRenderer.invoke(IPC_CHANNELS.forgetWorkspace, path),
  chooseProjectParent: () => ipcRenderer.invoke(IPC_CHANNELS.chooseProjectParent),
  createProject: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.createProject, request),
  revealWorkspace: () => ipcRenderer.invoke(IPC_CHANNELS.revealWorkspace),
  startRun: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.startRun, request),
  executionState: () => ipcRenderer.invoke(IPC_CHANNELS.executionState),
  decideApproval: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.decideApproval, request),
  approveConfiguration: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.approveConfiguration, request),
  initializeProject: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.initializeProject, request),
  notifyRun: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.notifyRun, request),
  /** The ONLY inbound channel: a fixed command name from the application menu. The event object and any payload are dropped, never forwarded. */
  onMenuCommand: (listener: (command: MenuCommand) => void) => {
    const handler = (_event: unknown, command: unknown) => { if ((MENU_COMMANDS as readonly unknown[]).includes(command)) listener(command as MenuCommand); };
    ipcRenderer.on(MENU_CHANNEL, handler);
    return () => { ipcRenderer.removeListener(MENU_CHANNEL, handler); };
  },
  measureMode: process.argv.includes('--kriya-ui-measure'),
};
contextBridge.exposeInMainWorld('kriyaHost', Object.freeze(api));
