import { useEffect, useState } from 'react'
import { useLocation, useNavigate } from '@tanstack/react-router'
import { m } from '../paraglide/messages.js'
import { cn } from '../ui/cn'
import { NAV_ITEMS, panelForPath, type PanelId } from './nav'
import { useLocale } from '../i18n/useLocale'
import { closeMobileSidebar, openMobileSidebar } from './useShellState'

interface Tab { key: string; label: () => string; panel?: PanelId; items?: PanelId[] }

/** Phone bottom tab bar (Chat · Tasks · Kanban · Agent · More); the stylesheet shows it only below 641px. */
const TABS: Tab[] = [
  { key: 'chat', label: () => m.tab_chat(), panel: 'chat' },
  { key: 'tasks', label: () => m.tab_tasks(), panel: 'tasks' },
  { key: 'kanban', label: () => m.tab_kanban(), panel: 'kanban' },
  { key: 'agent', label: () => m.tab_agent(), items: ['skills', 'memory', 'profiles', 'workspaces'] },
  { key: 'more', label: () => m.tab_more(), items: ['todos', 'insights', 'logs', 'settings'] },
]

export function Tabbar() {
  useLocale()
  const location = useLocation()
  const navigate = useNavigate()
  const [open, setOpen] = useState<string | null>(null)
  const active = panelForPath(location.pathname)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { const t = e.target as HTMLElement | null; if (!t?.closest('.tabbar-sheet') && !t?.closest('.tabbar-btn')) setOpen(null) }
    const id = window.setTimeout(() => document.addEventListener('click', onDoc), 0)
    return () => { window.clearTimeout(id); document.removeEventListener('click', onDoc) }
  }, [open])
  const goTo = (panel: PanelId) => {
    setOpen(null)
    const item = NAV_ITEMS.find((n) => n.id === panel)
    if (!item) return
    if (panel === 'chat') { openMobileSidebar(); return }
    closeMobileSidebar()
    void navigate({ to: item.to })
  }
  const iconFor = (panel: PanelId) => { const it = NAV_ITEMS.find((n) => n.id === panel); return it ? <it.icon size={20} strokeWidth={1.5} aria-hidden="true" /> : null }
  const sheet = open ? TABS.find((t) => t.key === open) : undefined
  return (
    <>
      <nav className="tabbar" aria-label="Primary navigation">
        {TABS.map((tab) => {
          const on = tab.panel ? tab.panel === active : (tab.items ?? []).includes(active)
          return (
            <button key={tab.key} type="button" className={cn('tabbar-btn', on && 'active', open === tab.key && 'open')} data-tab={tab.key} aria-expanded={tab.items ? open === tab.key : undefined} onClick={() => { if (tab.panel) goTo(tab.panel); else setOpen((o) => (o === tab.key ? null : tab.key)) }}>
              {iconFor(tab.panel ?? tab.items?.[0] ?? 'chat')}
              <span>{tab.label()}</span>
            </button>
          )
        })}
      </nav>
      {sheet?.items && (
        <div className="tabbar-sheet" role="menu">
          {sheet.items.map((panel) => {
            const item = NAV_ITEMS.find((n) => n.id === panel)
            return item ? <button key={panel} type="button" className="tabbar-sheet-item" role="menuitem" onClick={() => goTo(panel)}>{iconFor(panel)}<span>{item.label()}</span></button> : null
          })}
        </div>
      )}
    </>
  )
}
