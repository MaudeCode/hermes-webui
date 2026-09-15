import { Link, useLocation } from '@tanstack/react-router'
import { m } from '../paraglide/messages.js'
import { useBootstrap } from '../app/bootstrap'
import { useDashboardStatusQuery, useSettingsQuery } from '../app/queries'
import { Tooltip } from '../ui/Tooltip'
import { cn } from '../ui/cn'
import { DASHBOARD_ICON, orderedNav, panelForPath } from './nav'
import { readHiddenTabs, readTabOrder } from './useShellState'
import { useNewChat } from '../features/sessions/useNewChat'
import { useLocale } from '../i18n/useLocale'
import { useExtensionManifests } from '../extensions/registry'
import { Puzzle } from 'lucide-react'

/** Desktop primary navigation (>= 641px). */
export function Rail() {
  useLocale()
  const location = useLocation()
  const settings = useSettingsQuery()
  const bootstrap = useBootstrap()
  const dashboard = useDashboardStatusQuery(bootstrap.features.dashboard)
  const newChat = useNewChat()
  const manifests = useExtensionManifests(bootstrap.features.extensions || true)
  const extNav = (manifests.data?.manifests ?? []).filter((e) => e.enabled && e.panel && e.nav)
  const hidden = settings.data?.hidden_tabs ?? readHiddenTabs()
  const { visible } = orderedNav(readTabOrder(), hidden)
  const current = panelForPath(location.pathname)
  const DashboardIcon = DASHBOARD_ICON
  return (
    <nav className="rail hidden w-12 shrink-0 flex-col items-center gap-1 border-r border-border bg-sidebar py-2 min-[641px]:flex" aria-label="Primary navigation">
      <button type="button" className="rail-brand mb-1.5 flex h-11 w-[52px] items-center justify-center rounded-md hover:bg-hover" aria-label={m.new_conversation()} onClick={() => { void newChat() }}>
        <span className="brandmark rail-brandmark inline-block h-5 w-5" aria-hidden="true" />
      </button>
      {visible.map((item) => {
        const Icon = item.icon
        const active = current === item.id
        const isSettings = item.id === 'settings'
        return (
          <div key={item.id} className={cn('flex w-full flex-col items-center', isSettings && 'mt-auto')}>
            <Tooltip label={item.label()}>
              <Link
                to={item.to}
                className={cn('rail-btn relative flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-text', active && 'active bg-accent-bg text-accent-text before:absolute before:-left-1.5 before:top-1/2 before:h-4 before:w-[3px] before:-translate-y-1/2 before:rounded-r-sm before:bg-accent')}
                aria-label={item.label()}
                aria-current={active ? 'page' : undefined}
                data-panel={item.id}
              >
                <Icon size={20} strokeWidth={1.5} aria-hidden="true" />
              </Link>
            </Tooltip>
          </div>
        )
      })}
      {extNav.map((e) => {
        const active = location.pathname.startsWith(`/ext/${e.id}`)
        return (
          <Tooltip key={e.id} label={e.nav?.label ?? e.name}>
            <Link to="/ext/$extensionId" params={{ extensionId: e.id }} className={cn('rail-btn relative flex h-9 w-9 items-center justify-center rounded-lg text-muted transition-colors hover:bg-hover hover:text-text', active && 'active bg-accent-bg text-accent-text')} aria-label={e.nav?.label ?? e.name} aria-current={active ? 'page' : undefined} data-extension={e.id}>
              <Puzzle size={20} strokeWidth={1.5} aria-hidden="true" />
            </Link>
          </Tooltip>
        )
      })}
      {dashboard.data?.running && (dashboard.data.browser_url ?? dashboard.data.url) && (
        <Tooltip label={m.tab_dashboard()}>
          <a href={dashboard.data.browser_url ?? dashboard.data.url} target="_blank" rel="noopener noreferrer" className="rail-btn dashboard-link flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-hover hover:text-text" aria-label={m.tab_dashboard()}>
            <DashboardIcon size={20} strokeWidth={1.5} aria-hidden="true" />
          </a>
        </Tooltip>
      )}
    </nav>
  )
}
