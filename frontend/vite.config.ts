import { paraglideVitePlugin } from '@inlang/paraglide-js'
import tailwindcss from '@tailwindcss/vite'
import { tanstackStart } from '@tanstack/react-start/plugin/vite'
import viteReact from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The Python server serves the committed output from ../static/dist (see
// scripts/finalize-dist.mjs). The service worker is built by scripts/build-sw.mjs
// (workbox injectManifest) after the app build, because the Start builder does
// not run vite-plugin-pwa's closeBundle for the client environment. Relative base keeps hashed asset URLs valid
// under any subpath mount; the shell's <base href> is filled per request.
export default defineConfig({
  base: './',
  resolve: { alias: { '~': new URL('./src', import.meta.url).pathname } },
  plugins: [
    paraglideVitePlugin({
      project: './project.inlang',
      outdir: './src/paraglide',
      strategy: ['globalVariable', 'baseLocale'],
      emitGitIgnore: false,
      emitPrettierIgnore: false,
      outputStructure: 'message-modules',
      disableAsyncLocalStorage: true,
      isServer: 'false',
    }),
    tailwindcss(),
    tanstackStart({
      srcDirectory: 'src',
      spa: { enabled: true, maskPath: '/', prerender: { enabled: true, outputPath: '/_shell', crawlLinks: false, retryCount: 0 } },
      client: { entry: './client.tsx' },
      router: { entry: './router.tsx' },
    }),
    viteReact(),
  ],
  build: {
    sourcemap: process.env.HERMES_WEBUI_SOURCEMAP === '1',
    manifest: true,
    rollupOptions: {
      output: {
        // Stable, sorted chunk naming; content hashes make output deterministic for identical inputs.
        hashCharacters: 'base36',
      },
    },
  },
})
