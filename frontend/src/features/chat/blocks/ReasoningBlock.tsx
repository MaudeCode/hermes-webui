import { useState } from 'react'
import { ChevronRight, Lightbulb } from 'lucide-react'
import { cn } from '../../../ui/cn'
import { m } from '../../../paraglide/messages.js'
import { stripToolCallXml } from '../render/text'

/** Reasoning / thinking. Collapsed by default; a live block shows the latest title. */
export function ReasoningBlock({ text, titles, live = false, defaultOpen = false }: { text: string; titles?: string[] | undefined; live?: boolean; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const clean = stripToolCallXml(text).trim()
  const latest = titles?.[titles.length - 1]
  if (!clean && !live && !latest) return null
  return (
    <div className={cn('thinking-card my-1 rounded-lg border border-border-subtle bg-surface-subtle text-[13px]', live && 'thinking-card-live')} data-live={live ? '1' : undefined}>
      <button type="button" className="flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-muted" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <ChevronRight size={14} className={cn('shrink-0 transition-transform', open && 'rotate-90')} aria-hidden="true" />
        <Lightbulb size={14} className="shrink-0" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate">{latest ?? (live ? m.voice_thinking() : m.thinking_label())}</span>
        {live && <span className="h-2 w-2 shrink-0 animate-pulse rounded-full bg-accent" aria-hidden="true" />}
      </button>
      {open && clean && <pre className="thinking-card-body max-h-80 overflow-auto whitespace-pre-wrap break-words border-t border-border-subtle px-2.5 py-2 font-sans text-[12.5px] leading-relaxed text-muted">{clean}</pre>}
    </div>
  )
}
