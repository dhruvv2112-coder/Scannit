import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

const nm = (p: string) => fileURLToPath(new URL(`./node_modules/${p}`, import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: { port: 5173, strictPort: true, fs: { allow: ['../..'] } },
  preview: { port: 4173, strictPort: true },
  build: { sourcemap: true, chunkSizeWarningLimit: 1500 },
  // tests/viewer lives outside this package (spec ownership layout), so pin its bare imports for tests only.
  test: { alias: { 'three/examples': nm('three/examples'), three: nm('three/build/three.module.js'), vitest: nm('vitest') }, dir: '../../tests/viewer/unit', include: ['**/*.test.ts'], environment: 'node' },
});
