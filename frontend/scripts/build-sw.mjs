// Build the service worker (workbox injectManifest strategy) into static/dist/sw.js.
//
// 1. Bundle src/sw.ts with Vite in library mode (workbox-precaching inlined, no hash).
// 2. Inject the precache manifest for everything under static/dist except the
//    worker itself, using content-hash revisions so the output is deterministic.
// The TanStack Start builder does not run vite-plugin-pwa's closeBundle for the
// client environment, so this script drives the same workbox pipeline directly.
import { build } from 'vite'
import { injectManifest } from 'workbox-build'
import { resolve } from 'node:path'
import { readFileSync, writeFileSync, rmSync, existsSync } from 'node:fs'

const here = resolve(import.meta.dirname)
const distRoot = resolve(here, '../../static/dist')
const swTmp = resolve(here, '../dist/sw')

if (!existsSync(resolve(distRoot, 'index.html'))) {
  console.error('build-sw: static/dist/index.html missing; run finalize-dist first')
  process.exit(1)
}

await build({
  configFile: false,
  logLevel: 'warn',
  root: resolve(here, '..'),
  build: {
    outDir: swTmp,
    emptyOutDir: true,
    sourcemap: false,
    minify: 'oxc',
    target: 'es2022',
    lib: { entry: resolve(here, '../src/sw.ts'), formats: ['es'], fileName: () => 'sw.js' },
    rollupOptions: { output: { inlineDynamicImports: true } },
  },
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
})

const { count, size, warnings } = await injectManifest({
  swSrc: resolve(swTmp, 'sw.js'),
  swDest: resolve(distRoot, 'sw.js'),
  globDirectory: distRoot,
  globPatterns: ['**/*.{js,css,html,webmanifest,svg,png,ico,woff2,json}'],
  globIgnores: ['sw.js', 'FILES.txt'],
  injectionPoint: 'self.__WB_MANIFEST',
  // The shell is precached under its served name; the worker resolves entries against its scope.
  manifestTransforms: [
    async (entries) => ({
      manifest: entries
        .map((e) => ({ ...e, url: e.url === 'index.html' ? './index.html' : `./${e.url}` }))
        .sort((a, b) => a.url.localeCompare(b.url)),
      warnings: [],
    }),
  ],
  maximumFileSizeToCacheInBytes: 8 * 1024 * 1024,
})
for (const w of warnings) console.warn('build-sw:', w)
rmSync(swTmp, { recursive: true, force: true })

// Keep FILES.txt complete.
const filesPath = resolve(distRoot, 'FILES.txt')
const files = readFileSync(filesPath, 'utf8').split('\n').filter(Boolean)
if (!files.includes('sw.js')) writeFileSync(filesPath, [...files, 'sw.js'].sort().join('\n') + '\n')
console.log(`build-sw: precached ${count} files (${size} bytes)`)
