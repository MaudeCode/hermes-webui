import { createRouter } from '@tanstack/react-router'
import { routeTree } from './routeTree.gen'
import { routerBasepath } from './lib/appRoot'

export function getRouter(root?: URL) {
  return createRouter({
    routeTree,
    basepath: root ? routerBasepath(root) : '/',
    scrollRestoration: true,
    defaultPreload: 'intent',
    defaultPreloadStaleTime: 0,
    defaultStructuralSharing: true,
    notFoundMode: 'root',
  })
}

declare module '@tanstack/react-router' {
  interface Register {
    router: ReturnType<typeof getRouter>
  }
}
