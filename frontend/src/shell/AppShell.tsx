import type { ReactNode } from 'react'
import { Titlebar } from './Titlebar'
import { Rail } from './Rail'
import { Sidebar } from './Sidebar'
import { Tabbar } from './Tabbar'
import { useShortcuts } from './useShortcuts'
import { Toaster } from '../features/toast/Toaster'
import { TooltipProvider } from '../ui/Tooltip'
import { useEffect } from 'react'
import { registerExtensionSkins, useExtensionManifests } from '../extensions/registry'
import { reapplyExtensionSkin } from '../app/appearance'
import { useShellState } from './useShellState'
import { cn } from '../ui/cn'

/** Titlebar + `.layout` (rail, sidebar, main) on the legacy island shell. Routes supply `sidebar` and render into `children`. */
export function AppShell({ sidebar, children, title, subtitle, hub, showing }: { sidebar: ReactNode; children: ReactNode; title?: string; subtitle?: string; hub?: boolean; showing?: string }) {
  useShortcuts()
  const { collapsed } = useShellState()
  const manifests = useExtensionManifests()
  useEffect(() => {
    if (manifests.data) { registerExtensionSkins(manifests.data.manifests); reapplyExtensionSkin() }
  }, [manifests.data])
  useEffect(() => {
    document.documentElement.classList.toggle('hub-active', !!hub)
    return () => document.documentElement.classList.remove('hub-active')
  }, [hub])
  return (
    <TooltipProvider>
      <Titlebar {...(title !== undefined ? { title } : {})} {...(subtitle !== undefined ? { subtitle } : {})} />
      <div className={cn('layout', collapsed && 'sidebar-collapsed')}>
        <Rail />
        <Sidebar panel={sidebar} />
        <main className={cn('main', showing && `showing-${showing}`)} id="main">
          {children}
        </main>
      </div>
      <Tabbar />
      <Toaster />
    </TooltipProvider>
  )
}

/** Hub page frame (skills, memory, spaces, profiles, tasks, insights, logs). */
export function HubPage({ title, actions, toolbar, children, id }: { title: string; actions?: ReactNode; toolbar?: ReactNode; children: ReactNode; id?: string }) {
  return (
    <div className="main-view hub-page active" id={id} style={{ display: 'flex' }}>
      <header className="main-view-header">
        <h1 className="main-view-title">{title}</h1>
        {actions && <div className="main-view-actions">{actions}</div>}
      </header>
      {toolbar && <div className="hub-toolbar">{toolbar}</div>}
      <div className="main-view-body">{children}</div>
    </div>
  )
}
