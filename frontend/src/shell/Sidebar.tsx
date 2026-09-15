import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { m } from '../paraglide/messages.js'
import { cn } from '../ui/cn'
import { IconButton } from '../ui/Button'
import { MobileNav } from './MobileNav'
import { closeMobileSidebar, setSidebarWidth, useIsDesktop, useShellState } from './useShellState'

/**
 * Left column: on desktop a resizable panel next to the rail; on mobile a
 * full-width drawer with its own nav column. `panel` is the route's sidebar
 * content (session list, section menu, filters).
 */
export function Sidebar({ panel }: { panel: ReactNode }) {
  const { collapsed, mobileOpen, sidebarWidth } = useShellState()
  const isDesktop = useIsDesktop()
  const ref = useRef<HTMLElement>(null)
  useEffect(() => {
    if (!mobileOpen) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') closeMobileSidebar() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mobileOpen])

  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const startX = e.clientX
    const startW = ref.current?.getBoundingClientRect().width ?? sidebarWidth
    const move = (ev: PointerEvent) => setSidebarWidth(startW + (ev.clientX - startX) * (document.documentElement.dir === 'rtl' ? -1 : 1))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }

  return (
    <>
      {mobileOpen && !isDesktop && <div className="fixed inset-0 z-[190] bg-black/40 min-[641px]:hidden" aria-hidden="true" onClick={closeMobileSidebar} />}
      <aside
        ref={ref}
        className={cn(
          'sidebar relative flex shrink-0 flex-col border-r border-border bg-sidebar transition-[width,opacity,transform] duration-200',
          'max-[640px]:fixed max-[640px]:inset-y-0 max-[640px]:left-0 max-[640px]:z-[200] max-[640px]:w-screen max-[640px]:max-w-none max-[640px]:pl-[52px]',
          !isDesktop && !mobileOpen && 'max-[640px]:-translate-x-full',
          isDesktop && collapsed && 'pointer-events-none w-0! min-w-0 overflow-hidden border-transparent opacity-0',
        )}
        style={isDesktop && !collapsed ? { width: sidebarWidth, minWidth: 180 } : undefined}
        aria-hidden={!isDesktop && !mobileOpen ? true : undefined}
        data-mobile-open={mobileOpen ? '1' : undefined}
      >
        {!isDesktop && (
          <>
            <IconButton label={m.close_menu()} className="absolute right-1 z-[4] h-11 w-11 border border-border bg-surface" style={{ top: 'calc(4px + var(--app-titlebar-safe-top, 0px))' }} onClick={closeMobileSidebar}>
              <X size={18} aria-hidden="true" />
            </IconButton>
            <MobileNav />
          </>
        )}
        <div className="flex min-h-0 flex-1 flex-col">{panel}</div>
        {isDesktop && !collapsed && (
          <div className="resize-handle absolute inset-y-0 -right-[3px] z-10 w-[5px] cursor-col-resize hover:bg-accent-bg-strong" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" onPointerDown={startResize} />
        )}
      </aside>
    </>
  )
}

export function PanelHead({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="panel-head flex min-h-12 shrink-0 items-center justify-between gap-2 border-b border-border px-3.5 py-2.5 text-sm font-semibold text-text">
      <div className="min-w-0 truncate">{title}</div>
      {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
      {children}
    </div>
  )
}
