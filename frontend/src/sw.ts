/// <reference lib="webworker" />
/**
 * Hermes WebUI service worker (HWEB-100). Built by vite-plugin-pwa in
 * injectManifest mode: the hashed precache list is injected at build time.
 *
 * Behaviour carried over from the legacy sw.js:
 * - precache the app shell and hashed assets, keyed by build version;
 * - never cache API responses or SSE (the UI needs a live backend);
 * - navigations are network-first and fall back to the cached shell so an
 *   installed app still opens offline and shows its own offline notice;
 * - obsolete caches from previous builds are removed on activate;
 * - activation waits for the page's confirmation (`SKIP_WAITING` message) so
 *   the in-app update prompt controls when the new version takes over;
 * - the scope is the mount root, so subpath installs keep working.
 */
import { cleanupOutdatedCaches, precacheAndRoute, matchPrecache } from 'workbox-precaching'

declare const self: ServiceWorkerGlobalScope & { __WB_MANIFEST: { url: string; revision: string | null }[] }

const SHELL_URL = './index.html'

precacheAndRoute(self.__WB_MANIFEST)
cleanupOutdatedCaches()

self.addEventListener('message', (event: ExtendableMessageEvent) => {
  const data: unknown = event.data
  if (typeof data === 'object' && data !== null && (data as { type?: unknown }).type === 'SKIP_WAITING') void self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim())
})

function isServerOwned(url: URL, scope: URL): boolean {
  const rel = url.pathname.startsWith(scope.pathname) ? url.pathname.slice(scope.pathname.length) : url.pathname
  return (
    rel.startsWith('api/') || rel === 'health' || rel.startsWith('extensions/') || rel.startsWith('plugins/') || rel.startsWith('dashboard-plugins/') || rel === 'sw.js' || (!rel.startsWith('static/') && rel.includes('/static/'))
  )
}

self.addEventListener('fetch', (event) => {
  const request = event.request
  if (request.method !== 'GET') return
  const url = new URL(request.url)
  if (url.origin !== self.location.origin) return
  const scope = new URL(self.registration.scope)
  if (isServerOwned(url, scope)) return
  if (request.mode !== 'navigate') return
  // Navigation: network first; offline falls back to the precached shell only for a
  // successful, non-redirected shell (a login redirect must never be replaced by the app).
  event.respondWith(
    (async () => {
      try {
        const response = await fetch(request)
        return response
      } catch {
        const shell = await matchPrecache(SHELL_URL)
        if (shell) return shell
        return new Response('Hermes is offline and no cached shell is available.', { status: 503, headers: { 'Content-Type': 'text/plain; charset=utf-8' } })
      }
    })(),
  )
})
