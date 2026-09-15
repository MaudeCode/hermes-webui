import { Link, useLocation } from '@tanstack/react-router'
import { useSettingsQuery } from '../app/queries'
import { cn } from '../ui/cn'
import { orderedNav, panelForPath } from './nav'
import { closeMobileSidebar, readHiddenTabs, readTabOrder } from './useShellState'
import { useLocale } from '../i18n/useLocale'

/** Mobile drawer navigation column (<= 640px), mirrors the rail. */
export function MobileNav() {
  useLocale()
  const location = useLocation()
  const settings = useSettingsQuery()
  const { visible } = orderedNav(readTabOrder(), settings.data?.hidden_tabs ?? readHiddenTabs())
  const current = panelForPath(location.pathname)
  return (
    <div className="sidebar-nav" role="tablist" aria-orientation="vertical">
      {visible.map((item) => {
        const Icon = item.icon
        const active = current === item.id
        return (
          <Link key={item.id} to={item.to} onClick={() => { if (item.id !== 'chat') closeMobileSidebar() }} className={cn('nav-tab has-tooltip has-tooltip--bottom', active && 'active')} data-label={item.label()} data-tooltip={item.label()} data-panel={item.id} aria-label={item.label()} aria-current={active ? 'page' : undefined}>
            <Icon size={18} strokeWidth={2} aria-hidden="true" />
          </Link>
        )
      })}
    </div>
  )
}
