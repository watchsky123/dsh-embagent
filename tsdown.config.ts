/**
 * Client bundle for the HW workbench, in DSH's closure-factory shape.
 *
 * The artifact calls `window.__ModuleLoader__.load({ id, factory })` and
 * resolves externals through the injected `require` (the loader's frozen module
 * table). Everything else — including **three.js** — is inlined, because a
 * `require()` miss is a guaranteed runtime throw.
 *
 * Vendored from `dsh-hwforge`'s `tsdown.config.ts`, which is itself a copy of
 * DSH's own `packages/client/tsdown.client.ts` (rc.6).
 */
import { defineConfig } from 'tsdown'

/** dsh PLATFORM_MODULES — the exact loader module-table keys. */
const CLIENT_EXTERNALS: readonly string[] = [
  'react', 'react/jsx-runtime', 'react-dom', 'react-dom/client', '@deepseek-ai/cordis',
  '@deepseek-ai/dsh-client-ui-slots',
  '@deepseek-ai/dsh-client-web-react',
  '@deepseek-ai/dsh-client-ui-primitives',
  '@deepseek-ai/dsh-client-ui-attachment',
  '@deepseek-ai/dsh-client-schema-form',
  '@deepseek-ai/dsh-client-runtime/client',
]

const ID = 'dsh-hw-velxio'

export default defineConfig({
  entry: { client: 'src/client/index.ts' },
  outDir: 'dist',
  format: 'cjs',
  platform: 'browser',
  dts: false,
  sourcemap: true,
  // The node half already lives in dist/ (tsc); a default clean would wipe it.
  clean: false,
  external: [...CLIENT_EXTERNALS],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  noExternal: (id: string) => (CLIENT_EXTERNALS.includes(id) ? undefined : true),
  plugins: [{
    // Purity gate: the client half must not value-import any @deepseek-ai
    // package outside the loader table. Type-only imports are erased and never
    // reach resolveId, so `import type {} from '...'` stays legal.
    name: 'dsh-client-bundle-purity',
    resolveId(source: string) {
      if (!source.startsWith('@deepseek-ai/')) return null
      if (CLIENT_EXTERNALS.includes(source)) return null
      throw new Error(
        `client bundle purity: value import of "${source}" is not in the loader module table; `
        + 'use an import type or collaborate through a cordis service',
      )
    },
  }],
  outputOptions: {
    entryFileNames: 'client.js',
    banner: `window.__ModuleLoader__.load({ id: ${JSON.stringify(ID)}, factory: (require) => {`,
    footer: 'return module.exports; } });',
    intro: 'var module = { exports: {} }; var exports = module.exports;',
  },
})
