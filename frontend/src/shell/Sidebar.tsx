import { useEffect, useRef, type ReactNode } from 'react'
import { X } from 'lucide-react'
import { m } from '../paraglide/messages.js'
import { cn } from '../ui/cn'
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
        className={cn('sidebar', mobileOpen && !isDesktop && 'mobile-open')}
        style={isDesktop && !collapsed ? { width: sidebarWidth } : undefined}
        aria-hidden={!isDesktop && !mobileOpen ? true : undefined}
        data-mobile-open={mobileOpen ? '1' : undefined}
      >
        {!isDesktop && (
          <>
            <button type="button" className="panel-head-btn mobile-sidebar-close has-tooltip has-tooltip--bottom-right" data-tooltip={m.close_menu()} aria-label={m.close_menu()} onClick={closeMobileSidebar}>
              <X size={18} aria-hidden="true" />
            </button>
            <MobileNav />
          </>
        )}
        {panel}
        {isDesktop && !collapsed && (
          <div className="resize-handle" id="sidebarResize" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" onPointerDown={startResize} />
        )}
      </aside>
    </>
  )
}

export function PanelHead({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="panel-head">
      <span>{title}</span>
      {actions && <div className="panel-head-actions">{actions}</div>}
      {children}
    </div>
  )
}

/** Legacy `.panel-head-btn`: a 28px icon button whose label doubles as the CSS tooltip. */
export function PanelHeadButton({ label, onClick, id, active, children, tooltipSide = 'bottom', className }: { label: string; onClick?: () => void; id?: string; active?: boolean; children: ReactNode; tooltipSide?: 'bottom' | 'bottom-right' | 'left'; className?: string }) {
  return (
    <button type="button" id={id} className={cn('panel-head-btn has-tooltip', `has-tooltip--${tooltipSide}`, active && 'active', className)} data-tooltip={label} aria-label={label} onClick={onClick}>
      {children}
    </button>
  )
}
