import { Link, useLocation, useNavigate } from '@tanstack/react-router'
import { NAV_ITEMS, panelForPath } from './nav'
import { Menu as MenuIcon, Plus, RotateCw } from 'lucide-react'
import { m } from '../paraglide/messages.js'
import { useBootstrap } from '../app/bootstrap'
import { ProfileMenu } from './ProfileMenu'
import { toggleMobileSidebar } from './useShellState'
import { useNewChat } from '../features/sessions/useNewChat'
import { Brandmark } from './Brandmark'

/** Legacy `.app-titlebar`: hidden in desktop browsers, visible on mobile and in installed PWAs. */
export function Titlebar({ title, subtitle }: { title?: string; subtitle?: string }) {
  const bootstrap = useBootstrap()
  const navigate = useNavigate()
  const newChat = useNewChat()
  const location = useLocation()
  const panel = panelForPath(location.pathname)
  const panelLabel = NAV_ITEMS.find((n) => n.id === panel)?.label() ?? bootstrap.bot_name
  return (
    <header className="app-titlebar" role="banner">
      <div className="app-titlebar-left">
        <ProfileMenu />
        <button className="app-titlebar-hamburger has-tooltip has-tooltip--bottom" id="btnHamburger" type="button" data-tooltip={m.tab_more()} aria-label={m.tab_more()} onClick={toggleMobileSidebar}>
          <MenuIcon size={22} aria-hidden="true" />
        </button>
      </div>
      <div className="app-titlebar-inner">
        <Link to="/" className="app-titlebar-icon" aria-hidden="true" tabIndex={-1}><Brandmark className="brandmark" size={16} /></Link>
        <span className="app-titlebar-title" id="appTitlebarTitle">{title ?? panelLabel}</span>
        {subtitle && <span className="app-titlebar-sub" id="appTitlebarSub">{subtitle}</span>}
      </div>
      <div className="app-titlebar-spacer" aria-hidden="true" />
      <button className="app-titlebar-new-chat" id="btnTitlebarNewChat" type="button" aria-label={m.new_conversation()} title={m.new_conversation()} onClick={() => { void newChat() }}>
        <Plus size={16} aria-hidden="true" />
      </button>
      <button className="app-titlebar-reload" id="btnReload" type="button" aria-label={m.reload()} title={m.reload()} onClick={() => { void navigate({ to: '.' }); window.location.reload() }}>
        <RotateCw size={16} aria-hidden="true" />
      </button>
    </header>
  )
}
