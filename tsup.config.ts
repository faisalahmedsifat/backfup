import { defineConfig } from 'tsup';

export default defineConfig({
  entry: ['src/bin.ts'],
  format: ['esm'],
  dts: true,
  clean: true,
  splitting: false,
  sourcemap: true,
  minify: false,
  target: 'node18',
  outDir: 'dist',
  esbuildOptions(options) {
    options.platform = 'node';
  },
});
