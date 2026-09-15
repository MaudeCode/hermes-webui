import { Link, useNavigate } from '@tanstack/react-router'
import { Menu as MenuIcon, Plus, RotateCw } from 'lucide-react'
import { m } from '../paraglide/messages.js'
import { useBootstrap } from '../app/bootstrap'
import { ProfileMenu } from './ProfileMenu'
import { toggleMobileSidebar } from './useShellState'
import { IconButton } from '../ui/Button'
import { useNewChat } from '../features/sessions/useNewChat'

export function Titlebar({ title, subtitle }: { title?: string; subtitle?: string }) {
  const bootstrap = useBootstrap()
  const navigate = useNavigate()
  const newChat = useNewChat()
  return (
    <header className="app-titlebar relative flex h-[38px] shrink-0 select-none items-center justify-between border-b border-border bg-sidebar px-3 text-xs text-muted max-[640px]:h-[52px]" role="banner" style={{ paddingTop: 'var(--app-titlebar-safe-top, 0px)' }}>
      <div className="flex items-center gap-1">
        <ProfileMenu />
        <IconButton label={m.tab_more()} className="hidden h-11 w-11 max-[640px]:inline-flex" onClick={toggleMobileSidebar}>
          <MenuIcon size={22} aria-hidden="true" />
        </IconButton>
      </div>
      <div className="flex min-w-0 flex-1 items-center justify-center gap-2">
        <Link to="/" className="brandmark app-titlebar-icon inline-block h-4 w-4 shrink-0" aria-hidden="true" tabIndex={-1} />
        <span className="truncate font-medium text-text">{title ?? bootstrap.bot_name}</span>
        {subtitle && <span className="truncate text-muted">{subtitle}</span>}
      </div>
      <div className="flex items-center gap-1">
        <IconButton label={m.new_conversation()} className="hidden h-11 w-11 max-[640px]:inline-flex" onClick={() => { void newChat() }}>
          <Plus size={16} aria-hidden="true" />
        </IconButton>
        <IconButton label={m.reload()} className="hidden h-11 w-11 max-[640px]:inline-flex" onClick={() => { void navigate({ to: '.' }); window.location.reload() }}>
          <RotateCw size={16} aria-hidden="true" />
        </IconButton>
      </div>
    </header>
  )
}
