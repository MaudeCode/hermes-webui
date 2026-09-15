import type { ReactNode } from 'react'
import { Titlebar } from './Titlebar'
import { Rail } from './Rail'
import { Sidebar } from './Sidebar'
import { useShortcuts } from './useShortcuts'
import { Toaster } from '../features/toast/Toaster'
import { TooltipProvider } from '../ui/Tooltip'
import { useEffect } from 'react'
import { registerExtensionSkins, useExtensionManifests } from '../extensions/registry'
import { reapplyExtensionSkin } from '../app/appearance'

/** Titlebar + rail + sidebar + main. Routes supply `sidebar` and render into `children`. */
export function AppShell({ sidebar, children, title, subtitle }: { sidebar: ReactNode; children: ReactNode; title?: string; subtitle?: string }) {
  useShortcuts()
  const manifests = useExtensionManifests()
  useEffect(() => {
    if (manifests.data) { registerExtensionSkins(manifests.data.manifests); reapplyExtensionSkin() }
  }, [manifests.data])
  return (
    <TooltipProvider>
      <div className="flex h-full min-h-0 flex-col bg-bg text-text">
        <Titlebar {...(title !== undefined ? { title } : {})} {...(subtitle !== undefined ? { subtitle } : {})} />
        <div className="layout flex min-h-0 w-full flex-1 overflow-x-clip">
          <Rail />
          <Sidebar panel={sidebar} />
          <main className="main flex min-w-0 flex-1 flex-col overflow-hidden bg-main" id="main">
            {children}
          </main>
        </div>
        <Toaster />
      </div>
    </TooltipProvider>
  )
}

/** Hub page frame (skills, memory, spaces, profiles, tasks, insights, logs). */
export function HubPage({ title, actions, toolbar, children }: { title: string; actions?: ReactNode; toolbar?: ReactNode; children: ReactNode }) {
  return (
    <div className="main-view hub-page flex min-h-0 flex-1 flex-col bg-bg">
      <header className="main-view-header flex min-h-12 items-center justify-between gap-3 border-b border-border px-5 py-2.5 max-[768px]:px-3.5">
        <h1 className="main-view-title truncate text-[17px] font-semibold text-strong">{title}</h1>
        {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
      </header>
      {toolbar && <div className="hub-toolbar flex flex-wrap items-center gap-2 border-b border-border px-5 py-2 max-[768px]:px-3.5">{toolbar}</div>}
      <div className="main-view-body min-h-0 flex-1 overflow-y-auto px-5 py-4 max-[768px]:px-3.5">{children}</div>
    </div>
  )
}
