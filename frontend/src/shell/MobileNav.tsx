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
    <div className="sidebar-nav absolute inset-y-0 left-0 flex w-[52px] flex-col gap-1 overflow-y-auto border-r border-border p-2 pt-[calc(56px+var(--app-titlebar-safe-top,0px))]" role="tablist" aria-orientation="vertical">
      {visible.map((item) => {
        const Icon = item.icon
        const active = current === item.id
        return (
          <Link key={item.id} to={item.to} onClick={() => { if (item.id !== 'chat') closeMobileSidebar() }} className={cn('nav-tab relative flex h-11 w-9 items-center justify-center rounded-md text-muted', active && 'active text-accent-text after:absolute after:bottom-0 after:left-1/2 after:h-0.5 after:w-5 after:-translate-x-1/2 after:rounded-t-sm after:bg-accent')} aria-label={item.label()} aria-current={active ? 'page' : undefined} data-panel={item.id}>
            <Icon size={18} strokeWidth={2} aria-hidden="true" />
          </Link>
        )
      })}
    </div>
  )
}
