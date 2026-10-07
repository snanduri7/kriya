import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'node:path';

/** Plain-browser test host (gate A-2, P-R3). Serves the generated fixtures as static files; no server logic. */
export default defineConfig({
  plugins: [react()],
  publicDir: resolve(__dirname, '../fixtures'),
  server: { port: 5181, strictPort: true, host: '127.0.0.1' },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { environment: 'jsdom', include: ['test/**/*.test.{ts,tsx}'], setupFiles: ['./test/setup.ts'], css: false },
});
