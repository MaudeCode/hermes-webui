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
        className={cn('sidebar flex w-[300px] shrink-0 flex-col overflow-visible bg-transparent max-[641px]:bg-(--sidebar-bg) border-0 shadow-(--sidebar-shadow) min-[641px]:p-(--island-gap) transition-[width_.24s_ease,opacity_.18s_ease,transform_.24s_ease,margin_.24s_ease] min-[641px]:relative min-[901px]:shrink min-[901px]:min-w-[180px] max-[769px]:border-r max-[769px]:border-r-border max-[641px]:fixed max-[641px]:inset-y-0 max-[641px]:left-0 max-[641px]:w-screen max-[641px]:max-w-none max-[641px]:z-[200] max-[641px]:box-border max-[641px]:[transform:translateX(-100%)] max-[641px]:transition-[transform_.25s_ease] max-[641px]:will-change-transform max-[641px]:pb-[calc(56px+env(safe-area-inset-bottom,0px))] max-[641px]:[&.mobile-open]:[transform:translateX(0)]', mobileOpen && !isDesktop && 'mobile-open')}
        style={isDesktop && !collapsed ? { width: sidebarWidth } : undefined}
        aria-hidden={!isDesktop && !mobileOpen ? true : undefined}
        data-mobile-open={mobileOpen ? '1' : undefined}
      >
        {!isDesktop && (
          <>
            <button type="button" className={cn(PANEL_HEAD_BTN, 'mobile-sidebar-close has-tooltip--bottom-right')} data-tooltip={m.close_menu()} aria-label={m.close_menu()} onClick={closeMobileSidebar}>
              <X size={18} aria-hidden="true" />
            </button>
            <MobileNav />
          </>
        )}
        {panel}
        {isDesktop && !collapsed && (
          <div className="resize-handle absolute top-0 bottom-0 w-[5px] cursor-col-resize z-10 transition-[background] duration-150 hover:bg-accent" id="sidebarResize" role="separator" aria-orientation="vertical" aria-label="Resize sidebar" onPointerDown={startResize} />
        )}
      </aside>
    </>
  )
}

export function PanelHead({ title, actions, children }: { title: ReactNode; actions?: ReactNode; children?: ReactNode }) {
  return (
    <div className="panel-head flex items-center justify-between gap-2 min-h-11 px-3.5 py-2 border-b border-border text-[13px] font-semibold text-text normal-case tracking-[-.01em] shrink-0">
      <span>{title}</span>
      {actions && <div className="panel-head-actions flex items-center gap-1 normal-case tracking-normal">{actions}</div>}
      {children}
    </div>
  )
}

/** Legacy `.panel-head-btn`: a 24px icon button whose label doubles as the CSS tooltip. Toolbar (`.main-view-actions`) and mobile-close variants keep their legacy rules. */
export const PANEL_HEAD_BTN = 'panel-head-btn has-tooltip inline-flex size-6 p-0 items-center justify-center border-0 bg-transparent rounded-(--btn-radius) text-muted cursor-pointer shrink-0 transition-[background,color] duration-(--dur) ease-(--ease) hover:bg-(--panel-btn-hover-bg) hover:text-(--panel-btn-hover-fg) [&_svg]:block [&_svg]:size-3.5'

export function PanelHeadButton({ label, onClick, id, active, children, tooltipSide = 'bottom', className }: { label: string; onClick?: () => void; id?: string; active?: boolean; children: ReactNode; tooltipSide?: 'bottom' | 'bottom-right' | 'left'; className?: string }) {
  return (
    <button type="button" id={id} className={cn(PANEL_HEAD_BTN, `has-tooltip--${tooltipSide}`, active && 'active', className)} data-tooltip={label} aria-label={label} onClick={onClick}>
      {children}
    </button>
  )
}
