import { Link, useLocation } from '@tanstack/react-router'
import { m } from '../paraglide/messages.js'
import { useBootstrap } from '../app/bootstrap'
import { useDashboardStatusQuery, useSettingsQuery } from '../app/queries'
import { cn } from '../ui/cn'
import { DASHBOARD_ICON, orderedNav, panelForPath } from './nav'
import { readHiddenTabs, readTabOrder } from './useShellState'
import { useNewChat } from '../features/sessions/useNewChat'
import { useLocale } from '../i18n/useLocale'
import { useExtensionManifests } from '../extensions/registry'
import { Puzzle } from 'lucide-react'
import { Brandmark } from './Brandmark'

/**
 * Desktop primary navigation. Markup and class names follow the legacy shell
 * (`.rail > .rail-brand + .rail-btn.nav-tab.has-tooltip`): the stylesheet
 * renders each button's label from `data-tooltip` beneath its icon.
 */
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
  const settingsItem = visible.find((i) => i.id === 'settings')
  const mainItems = visible.filter((i) => i.id !== 'settings')
  return (
    <nav className="rail" aria-label="Primary navigation">
      <button type="button" className="rail-brand" aria-label={m.new_conversation()} onClick={() => { void newChat() }}>
        <Brandmark className="brandmark rail-brandmark" />
      </button>
      {mainItems.map((item) => {
        const Icon = item.icon
        const active = current === item.id
        return (
          <Link key={item.id} to={item.to} className={cn('rail-btn nav-tab has-tooltip', active && 'active')} data-tooltip={item.label()} aria-label={item.label()} aria-current={active ? 'page' : undefined} data-panel={item.id}>
            <Icon size={20} strokeWidth={1.5} aria-hidden="true" />
          </Link>
        )
      })}
      {extNav.map((e) => {
        const active = location.pathname.startsWith(`/ext/${e.id}`)
        const label = e.nav?.label ?? e.name
        return (
          <Link key={e.id} to="/ext/$extensionId" params={{ extensionId: e.id }} className={cn('rail-btn nav-tab has-tooltip', active && 'active')} data-tooltip={label} aria-label={label} aria-current={active ? 'page' : undefined} data-extension={e.id}>
            <Puzzle size={20} strokeWidth={1.5} aria-hidden="true" />
          </Link>
        )
      })}
      {dashboard.data?.running && (dashboard.data.browser_url ?? dashboard.data.url) && (
        <a href={dashboard.data.browser_url ?? dashboard.data.url} target="_blank" rel="noopener noreferrer" className="rail-btn nav-tab dashboard-link has-tooltip" data-tooltip={m.tab_dashboard()} aria-label={m.tab_dashboard()}>
          <DashboardIcon size={20} strokeWidth={1.5} aria-hidden="true" />
        </a>
      )}
      <div className="rail-spacer" />
      {settingsItem && (
        <Link to={settingsItem.to} className={cn('rail-btn nav-tab has-tooltip', current === 'settings' && 'active')} data-tooltip={settingsItem.label()} aria-label={settingsItem.label()} aria-current={current === 'settings' ? 'page' : undefined} data-panel="settings">
          <settingsItem.icon size={20} strokeWidth={1.5} aria-hidden="true" />
        </Link>
      )}
    </nav>
  )
}
