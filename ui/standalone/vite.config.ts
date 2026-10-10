import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

/** Records which UI this bundle is (src/main/ui_mode.ts reads it): the M1 inspector only for the explicit legacy build (VITE_KRIYA_UI=inspector); every other build is the cockpit. */
const uiMode = { name: 'kriya-ui-mode', generateBundle() { this.emitFile({ type: 'asset', fileName: 'ui-mode.json', source: JSON.stringify({ ui: process.env.VITE_KRIYA_UI === 'inspector' ? 'inspector' : 'cockpit' }) }); } };

/** Renderer build: relative asset paths so the bundle loads from file:// inside the hardened BrowserWindow. */
export default defineConfig({
  plugins: [react(), uiMode],
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  build: { outDir: resolve(__dirname, 'dist/renderer'), emptyOutDir: true },
  test: { environment: 'node', root: __dirname, include: ['test/**/*.test.ts'] },
});
