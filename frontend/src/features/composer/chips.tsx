import { useMemo, useState } from 'react'
import { ChevronDown, Cpu, Brain, Wrench, FolderOpen } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import { workspaceLabel } from '../workspaces/label'
import { useModelsQuery, useWorkspacesQuery } from '../../app/queries'
import { Menu, MenuGroup, MenuGroupLabel, MenuItem, MenuRadioGroup, MenuRadioItem, MenuSeparator } from '../../ui/Menu'
import { cn } from '../../ui/cn'

function Chip({ icon, label, title, className, disabled }: { icon: React.ReactNode; label: string; title: string; className?: string; disabled?: boolean }) {
  return (
    <button type="button" disabled={disabled} title={title} aria-label={`${title}: ${label}`} className={cn('composer-chip inline-flex h-8 max-w-[280px] items-center gap-1.5 rounded-full border border-transparent px-2.5 text-[12.5px] font-medium text-muted hover:border-border hover:bg-hover hover:text-text disabled:opacity-60', className)}>
      {icon}
      <span className="truncate">{label}</span>
      <ChevronDown size={10} aria-hidden="true" />
    </button>
  )
}

const RADIO_CLASS = 'flex cursor-default select-none items-center gap-2 rounded-md px-2.5 py-1.5 text-sm outline-none data-[highlighted]:bg-hover data-[checked]:text-accent-text'

/** Conversation model picker: grouped by provider with a search field and a custom id entry. */
export function ModelChip({ value, onChange, defaultModel }: { value: string | null; onChange: (model: string, provider: string | null) => void; defaultModel: string | undefined }) {
  const models = useModelsQuery()
  const [query, setQuery] = useState('')
  const groups = useMemo(() => {
    const q = query.trim().toLowerCase()
    return (models.data?.groups ?? []).map((g) => ({ ...g, models: g.models.filter((mm) => !q || mm.id.toLowerCase().includes(q) || (mm.label ?? '').toLowerCase().includes(q)) })).filter((g) => g.models.length > 0)
  }, [models.data, query])
  const label = useMemo(() => {
    const id = value ?? defaultModel ?? ''
    for (const g of models.data?.groups ?? []) for (const mm of g.models) if (mm.id === id) return mm.label ?? mm.id
    return id || '—'
  }, [models.data, value, defaultModel])
  const providerOf = (id: string): string | null => { for (const g of models.data?.groups ?? []) if (g.models.some((mm) => mm.id === id)) return g.provider_id ?? g.provider; return null }
  return (
    <Menu label={m.composer_control_model()} side="top" className="max-h-[60vh] min-w-72" trigger={<Chip icon={<Cpu size={14} aria-hidden="true" />} label={label} title={m.composer_control_model()} className="composer-model-chip" />}>
      <div className="p-1"><input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder={m.model_search_placeholder()} aria-label={m.model_search_placeholder()} className="h-8 w-full rounded-md border border-border bg-input px-2 text-sm text-text" onKeyDown={(e) => e.stopPropagation()} /></div>
      <MenuRadioGroup value={value ?? defaultModel ?? ''} onValueChange={(v: string) => onChange(v, providerOf(v))}>
        {groups.map((g) => (
          <MenuGroup key={g.provider}>
            <MenuGroupLabel>{g.provider}</MenuGroupLabel>
            {g.models.map((mm) => <MenuRadioItem key={mm.id} value={mm.id} className={RADIO_CLASS}>{mm.label ?? mm.id}</MenuRadioItem>)}
          </MenuGroup>
        ))}
      </MenuRadioGroup>
      {query.trim() && !groups.some((g) => g.models.some((mm) => mm.id === query.trim())) && (
        <>
          <MenuSeparator />
          <MenuItem onClick={() => onChange(query.trim(), null)}><span className="font-mono text-xs">{query.trim()}</span> <span className="text-xs text-muted">{m.model_custom_placeholder()}</span></MenuItem>
        </>
      )}
    </Menu>
  )
}

const EFFORTS = ['default', 'minimal', 'low', 'medium', 'high', 'xhigh'] as const
export function ReasoningChip({ value, levels, onChange }: { value: string | null; levels: string[] | undefined; onChange: (level: string | null) => void }) {
  const options = levels?.length ? ['default', ...levels] : [...EFFORTS]
  return (
    <Menu label={m.composer_control_reasoning()} side="top" trigger={<Chip icon={<Brain size={14} aria-hidden="true" />} label={value ?? m.reasoning_default()} title={m.composer_control_reasoning()} className="composer-reasoning-chip" />}>
      <MenuRadioGroup value={value ?? 'default'} onValueChange={(v: string) => onChange(v === 'default' ? null : v)}>
        {options.map((o) => <MenuRadioItem key={o} value={o} className={RADIO_CLASS}>{o === 'default' ? m.reasoning_default() : o}</MenuRadioItem>)}
      </MenuRadioGroup>
    </Menu>
  )
}

export function ToolsetsChip({ value, onChange }: { value: string[] | null; onChange: (toolsets: string[] | null) => void }) {
  const [draft, setDraft] = useState((value ?? []).join(', '))
  return (
    <Menu label={m.composer_control_toolsets()} side="top" className="min-w-72" trigger={<Chip icon={<Wrench size={14} aria-hidden="true" />} label={value?.length ? value.join(', ') : m.toolsets_global()} title={m.composer_control_toolsets()} className="composer-toolsets-chip" />}>
      <form className="flex flex-col gap-2 p-2" onSubmit={(e) => { e.preventDefault(); const list = draft.split(',').map((s) => s.trim()).filter(Boolean); onChange(list.length ? list : null) }}>
        <input value={draft} onChange={(e) => setDraft(e.target.value)} placeholder={m.session_toolsets_placeholder()} aria-label={m.composer_control_toolsets()} className="h-8 w-full rounded-md border border-border bg-input px-2 font-mono text-xs text-text" onKeyDown={(e) => e.stopPropagation()} />
        <div className="text-[11px] text-muted">{m.toolsets_hint()}</div>
        <button type="submit" className="self-end rounded-md bg-accent px-2.5 py-1 text-xs font-medium text-white">{m.save()}</button>
      </form>
    </Menu>
  )
}

export function WorkspaceChip({ value, onChange }: { value: string | undefined; onChange: (path: string) => void }) {
  const ws = useWorkspacesQuery()
  const list = ws.data?.workspaces ?? []
  const label = workspaceLabel(list, value) || '—'
  return (
    <Menu label={m.composer_control_workspace()} side="top" className="min-w-64" trigger={<Chip icon={<FolderOpen size={14} aria-hidden="true" />} label={label} title={m.composer_control_workspace()} className="composer-workspace-chip" disabled={list.length === 0} />}>
      <MenuRadioGroup value={value ?? ''} onValueChange={(v: string) => onChange(v)}>
        {list.map((w) => <MenuRadioItem key={w.path} value={w.path} className={RADIO_CLASS}><span className="flex min-w-0 flex-col"><span className="truncate">{w.name ?? w.path}</span><span className="truncate font-mono text-[10px] text-muted">{w.path}</span></span></MenuRadioItem>)}
      </MenuRadioGroup>
    </Menu>
  )
}

/** Context window ring: last prompt tokens over the model context length. */
export function ContextRing({ used, total, threshold }: { used: number | null | undefined; total: number | null | undefined; threshold?: number | null | undefined }) {
  if (!used || !total) return null
  const pct = Math.min(100, Math.round((used / total) * 100))
  const r = 9.75
  const c = 2 * Math.PI * r
  const tone = pct >= 90 ? 'text-error' : pct >= 70 ? 'text-warning' : 'text-accent'
  const title = `${m.composer_context_usage()}: ${pct}% (${used.toLocaleString()} / ${total.toLocaleString()})${threshold ? ` · ${threshold.toLocaleString()}` : ''}`
  return (
    <div className="ctx-indicator-wrap relative flex items-center" id="ctxIndicatorWrap">
      <button type="button" className={cn('ctx-indicator relative flex h-8 w-8 items-center justify-center rounded-full', tone)} aria-label={title} title={title} id="ctxIndicator">
        <svg viewBox="0 0 24 24" className="h-6 w-6 -rotate-90" aria-hidden="true">
          <circle cx="12" cy="12" r={r} fill="none" stroke="currentColor" strokeWidth="2" opacity="0.2" />
          <circle cx="12" cy="12" r={r} fill="none" stroke="currentColor" strokeWidth="2" strokeDasharray={c} strokeDashoffset={c - (c * pct) / 100} strokeLinecap="round" />
        </svg>
        <span className="absolute text-[8px] font-semibold tabular-nums" id="ctxPercent">{pct}</span>
      </button>
    </div>
  )
}
