import { defineConfig } from 'vite';
import { resolve } from 'node:path';

const here = import.meta.dirname;

/** A sandboxed preload (gate A-1: sandbox: true) cannot require() local files, so the preload is bundled into ONE
 * self-contained CommonJS file (MEASURED 2026-10-04: `module not found: ./ipc_contract` from the tsc output). */
export default defineConfig({
  build: {
    lib: { entry: resolve(here, 'src/main/preload.ts'), formats: ['cjs'], fileName: () => 'preload.js' },
    outDir: resolve(here, 'dist/main'),
    emptyOutDir: false,
    minify: false,
    sourcemap: true,
    rollupOptions: { external: ['electron'] },
  },
});
