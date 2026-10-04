/**
 * Preload (gate A-1): exposes ONLY the typed HostAdapter messages, each bound to a fixed channel. No generic
 * ipcRenderer passthrough, no Node API, nothing else on window.
 */
import { contextBridge, ipcRenderer } from 'electron';
import { IPC_CHANNELS } from './ipc_contract';

const api = {
  query: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.query, request),
  openInIde: (request: unknown) => ipcRenderer.invoke(IPC_CHANNELS.openInIde, request),
  copyToClipboard: (text: string) => ipcRenderer.invoke(IPC_CHANNELS.clipboardWrite, text),
  getSetting: (key: string) => ipcRenderer.invoke(IPC_CHANNELS.settingGet, key),
  setSetting: (key: string, value: unknown) => ipcRenderer.invoke(IPC_CHANNELS.settingSet, key, value),
  hostInfo: () => ipcRenderer.invoke(IPC_CHANNELS.hostInfo),
  measureMode: process.argv.includes('--kriya-ui-measure'),
};
contextBridge.exposeInMainWorld('kriyaHost', Object.freeze(api));
