// Client entry: a client-only React root. The prerendered shell contains no
// server-rendered UI and no inline scripts (see scripts/finalize-dist.mjs), so
// there is nothing to hydrate. Order matters: freeze the base URL and apply
// persisted appearance before the first paint, load the bootstrap payload,
// then mount the router with its Query client.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { QueryClientProvider } from '@tanstack/react-query'
import { freezeAppRoot } from './lib/appRoot'
import { applyBootAppearance } from './theme/boot'
import { getRouter } from './router'
import { createQueryClient } from './api/queryClient'
import { loadBootstrap, BootstrapContext } from './app/bootstrap'
import { FatalError } from './features/shell/ErrorBoundary'
import './theme/tailwind.css'
import 'virtual:hermes-theme.css'

const appRoot = freezeAppRoot()
applyBootAppearance()

const container = document.getElementById('app')
if (!container) throw new Error('missing #app mount node')
const root = createRoot(container)

loadBootstrap().then(
  (bootstrap) => {
    const queryClient = createQueryClient()
    const router = getRouter({ root: appRoot, queryClient, bootstrap })
    root.render(
      <StrictMode>
        <QueryClientProvider client={queryClient}>
          <BootstrapContext.Provider value={bootstrap}>
            <RouterProvider router={router} />
          </BootstrapContext.Provider>
        </QueryClientProvider>
      </StrictMode>,
    )
  },
  (error: unknown) => {
    root.render(<FatalError error={error} />)
  },
)
