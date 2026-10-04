import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

/** Renderer build: relative asset paths so the bundle loads from file:// inside the hardened BrowserWindow. */
export default defineConfig({
  plugins: [react()],
  root: resolve(__dirname, 'src/renderer'),
  base: './',
  build: { outDir: resolve(__dirname, 'dist/renderer'), emptyOutDir: true },
  test: { environment: 'node', root: __dirname, include: ['test/**/*.test.ts'] },
});
