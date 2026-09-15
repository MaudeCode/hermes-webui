import { useState, type ReactNode } from 'react'
import { ChevronRight } from 'lucide-react'
import { cn } from '../../../ui/cn'
import { toolText } from '../../../i18n/toolText'
import { useLocale } from '../../../i18n/useLocale'
import { toolKind } from '../toolKind'
import type { ToolCardData } from './ToolCard'

export type ActivityMode = 'compact_worklog' | 'transparent_stream' | 'hide_all_activity'

/**
 * Compact worklog: one summary line ("Read 3 files · Ran a command") that
 * expands to the individual cards. Transparent stream renders the children
 * inline in order. Hide mode renders nothing.
 */
export function Worklog({ mode, calls, live, children, defaultOpen }: { mode: ActivityMode; calls: ToolCardData[]; live: boolean; children: ReactNode; defaultOpen?: boolean }) {
  const locale = useLocale()
  const [open, setOpen] = useState(!!defaultOpen)
  if (mode === 'hide_all_activity') return null
  if (mode === 'transparent_stream') return <div className="transparent-stream flex flex-col">{children}</div>
  if (calls.length === 0) return <>{children}</>
  const text = toolText(locale)
  const byKind = new Map<string, number>()
  for (const c of calls) byKind.set(toolKind(c.name), (byKind.get(toolKind(c.name)) ?? 0) + 1)
  const state = live && calls.some((c) => !c.done) ? 'running' : 'done'
  const summary = text.summaryJoin([...byKind.entries()].map(([kind, n]) => text.worklogSummary(kind, state, n)))
  const failed = calls.filter((c) => c.isError).length
  return (
    <div className="tool-worklog my-1" data-open={open ? '1' : '0'}>
      <button type="button" className="tool-worklog-summary flex w-full items-center gap-2 rounded-lg px-1 py-1 text-left text-[13px] text-muted hover:text-text" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={14} className={cn('shrink-0 transition-transform', open && 'rotate-90')} aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{summary}</span>
        {failed > 0 && <span className="shrink-0 text-[11px] text-error">{failed}✕</span>}
        {state === 'running' && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent" aria-hidden="true" />}
      </button>
      {open && <div className="tool-worklog-list ml-2 border-l border-border-subtle pl-2">{children}</div>}
    </div>
  )
}
