/**
 * Gate A-1 hardening, as DATA so a test can check every setting (06 Phase E "a test checks every one of these").
 * Pure module: no Electron import. main.ts applies these verbatim.
 */
export const WEB_PREFERENCES = {
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  nodeIntegrationInWorker: false,
  nodeIntegrationInSubFrames: false,
  webviewTag: false,
  webSecurity: true,
  allowRunningInsecureContent: false,
  experimentalFeatures: false,
  enableBlinkFeatures: '',
  navigateOnDragDrop: false,
  spellcheck: false,
  devTools: false,
} as const;

/** Strict CSP: no remote content, no inline script, no eval, no frames, no forms, no navigation targets. */
export const CONTENT_SECURITY_POLICY = [
  "default-src 'none'",
  "script-src 'self'",
  "style-src 'self'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'none'",
  "object-src 'none'",
  "base-uri 'none'",
  "form-action 'none'",
  "frame-ancestors 'none'",
  "frame-src 'none'",
  "worker-src 'none'",
  "manifest-src 'none'",
  "media-src 'none'",
].join('; ');

/** Every permission request from the renderer is denied (no camera, clipboard-read, notifications, ...). */
export const PERMISSION_DECISION = false;

/** Behaviours main.ts must wire; the test checks the wiring list stays complete and that main.ts names each one. */
export const HARDENING_BEHAVIOURS = [
  'will-navigate',
  'will-redirect',
  'setWindowOpenHandler',
  'will-attach-webview',
  'setPermissionRequestHandler',
  'setPermissionCheckHandler',
  'onHeadersReceived',
  'onBeforeRequest',
  'loadFile',
] as const;
