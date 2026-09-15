// Client entry: a client-only React root. The prerendered shell contains no
// server-rendered UI and no inline scripts (see scripts/finalize-dist.mjs), so
// there is nothing to hydrate. Order matters: freeze the base URL and apply
// persisted appearance before the first paint, then mount the router.
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { RouterProvider } from '@tanstack/react-router'
import { freezeAppRoot } from './lib/appRoot'
import { applyBootAppearance } from './theme/boot'
import { getRouter } from './router'
import './theme/tailwind.css'

const appRoot = freezeAppRoot()
applyBootAppearance()

const router = getRouter(appRoot)
const container = document.getElementById('app')
if (!container) throw new Error('missing #app mount node')
createRoot(container).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
)
