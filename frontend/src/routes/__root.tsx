import { HeadContent, Outlet, Scripts, createRootRoute } from '@tanstack/react-router'

/**
 * Root route. The shell component describes the static HTML document the SPA
 * prerender emits (`static/dist/index.html`). It contains no server-rendered
 * UI: the client entry mounts into #app. Every href here is relative so the
 * Python-injected <base href> resolves it under any mount prefix.
 */
export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: 'utf-8' },
      { name: 'viewport', content: 'width=device-width, initial-scale=1, viewport-fit=cover' },
      { title: 'Hermes' },
      { name: 'mobile-web-app-capable', content: 'yes' },
      { name: 'apple-mobile-web-app-capable', content: 'yes' },
      { name: 'apple-mobile-web-app-status-bar-style', content: 'black-translucent' },
      { name: 'apple-mobile-web-app-title', content: 'Hermes' },
      { name: 'theme-color', content: '#0D0D1A' },
      { name: 'color-scheme', content: 'dark light' },
    ],
    links: [
      { rel: 'icon', type: 'image/png', sizes: '32x32', href: 'static/brand/favicon-32.png' },
      { rel: 'shortcut icon', href: 'static/brand/favicon.ico' },
      { rel: 'icon', type: 'image/svg+xml', sizes: 'any', href: 'static/brand/favicon.svg' },
      { rel: 'apple-touch-icon', sizes: '512x512', href: 'static/brand/apple-touch-icon.png' },
      { rel: 'manifest', href: 'manifest.webmanifest', crossOrigin: 'use-credentials' },
    ],
  }),
  shellComponent: RootShell,
  component: RootComponent,
})

function RootShell() {
  return (
    <html lang="en">
      <head>
        <HeadContent />
      </head>
      <body>
        <div id="app" />
        <Scripts />
      </body>
    </html>
  )
}

function RootComponent() {
  return <Outlet />
}
