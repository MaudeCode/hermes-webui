import { useEffect, useMemo, useState } from 'react'
import { Link, useParams } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Search, X } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import { openSessionListStream } from '../../api/sse'
import type { SessionRow } from '../../contracts'
import { PanelHead } from '../../shell/Sidebar'
import { IconButton } from '../../ui/Button'
import { cn } from '../../ui/cn'
import { useNewChat } from './useNewChat'
import { SessionContextMenu } from './SessionContextMenu'
import { closeMobileSidebar } from '../../shell/useShellState'
import { useLocale } from '../../i18n/useLocale'

export function useSessionListQuery(params: api.SessionListParams = {}) {
  return useQuery({ queryKey: keys.sessions.list(params as Record<string, string | boolean | number | undefined>), queryFn: () => api.fetchSessions(params), staleTime: 10_000 })
}

/** One EventSource for sidebar invalidation (`sessions_changed`), bounded to the panel's lifetime. */
export function useSessionListStream() {
  const qc = useQueryClient()
  useEffect(() => {
    let closed = false
    let handle = openSessionListStream({
      onEvent: (ev) => {
        if (ev.event === 'sessions_changed' || ev.event === 'initial') void qc.invalidateQueries({ queryKey: keys.sessions.all })
      },
      onError: () => { /* EventSource retries on its own; the poll below covers long outages */ },
    })
    const poll = window.setInterval(() => { if (!closed && document.visibilityState === 'visible') void qc.invalidateQueries({ queryKey: keys.sessions.all }) }, 60_000)
    return () => {
      closed = true
      handle.close()
      handle = { close: () => undefined, readyState: () => 2 }
      window.clearInterval(poll)
    }
  }, [qc])
}

export function relativeTime(ts: number | null | undefined, now = Date.now()): string {
  if (!ts) return ''
  const diff = Math.max(0, now / 1000 - ts)
  if (diff < 60) return m.session_time_just_now()
  if (diff < 3600) return m.session_time_minutes_ago({ n: Math.floor(diff / 60) })
  if (diff < 86400) return m.session_time_hours_ago({ n: Math.floor(diff / 3600) })
  return m.session_time_days_ago({ n: Math.floor(diff / 86400) })
}

function groupLabel(row: SessionRow): 'pinned' | 'today' | 'yesterday' | 'week' | 'older' {
  if (row.pinned) return 'pinned'
  const ts = (row.last_message_at ?? row.updated_at ?? row.created_at ?? 0) * 1000
  const d = new Date(ts)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  if (d >= today) return 'today'
  const yesterday = new Date(today)
  yesterday.setDate(today.getDate() - 1)
  if (d >= yesterday) return 'yesterday'
  const week = new Date(today)
  week.setDate(today.getDate() - 7)
  if (d >= week) return 'week'
  return 'older'
}

const GROUP_LABEL: Record<ReturnType<typeof groupLabel>, () => string> = {
  pinned: () => m.session_time_bucket_pinned(),
  today: () => m.session_time_bucket_today(),
  yesterday: () => m.session_time_bucket_yesterday(),
  week: () => m.session_time_bucket_this_week(),
  older: () => m.session_time_bucket_older(),
}

export function SessionListPanel() {
  useLocale()
  const params: { sessionId?: string } = useParams({ strict: false })
  const activeId = params.sessionId ?? null
  const [filter, setFilter] = useState('')
  const list = useSessionListQuery()
  useSessionListStream()
  const newChat = useNewChat()
  const data = list.data
  const filtered = useMemo(() => {
    const rows = data?.sessions ?? []
    const q = filter.trim().toLowerCase()
    const visible = rows.filter((r) => !r.archived)
    return q ? visible.filter((r) => r.title.toLowerCase().includes(q)) : visible
  }, [data, filter])
  const groups = useMemo(() => {
    const order: ReturnType<typeof groupLabel>[] = ['pinned', 'today', 'yesterday', 'week', 'older']
    const byGroup = new Map<string, SessionRow[]>()
    for (const r of filtered) {
      const g = groupLabel(r)
      const arr = byGroup.get(g) ?? []
      arr.push(r)
      byGroup.set(g, arr)
    }
    return order.filter((g) => byGroup.has(g)).map((g) => ({ id: g, label: GROUP_LABEL[g](), rows: byGroup.get(g) ?? [] }))
  }, [filtered])

  return (
    <div className="panel-view active flex min-h-0 flex-1 flex-col" id="panelChat">
      <PanelHead
        title={m.tab_chat()}
        actions={
          <IconButton label={m.new_conversation()} className="h-6 w-6" onClick={() => { void newChat() }} id="btnNewChat">
            <Plus size={16} aria-hidden="true" />
          </IconButton>
        }
      />
      <div className="sidebar-search relative shrink-0 px-3 pb-2 pt-1">
        <div className="session-search-field relative flex w-full items-center">
          <Search size={14} className="pointer-events-none absolute left-2.5 text-muted" aria-hidden="true" />
          <input id="sessionSearch" type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={m.filter_conversations()} aria-label={m.filter_conversations()} autoComplete="off" data-1p-ignore data-lpignore="true" className="h-8 w-full rounded-md border border-border bg-input pl-8 pr-7 text-[13px] text-text placeholder:text-muted" />
          {filter && (
            <IconButton label={m.clear_conversation_filter()} className="absolute right-0.5 h-7 w-7" onClick={() => setFilter('')}>
              <X size={14} aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </div>
      <div className="session-list min-h-0 flex-1 overflow-y-auto px-2 pb-2" id="sessionList" role="list">
        {list.isPending && <div className="p-3 text-xs text-muted" role="status">{m.loading()}</div>}
        {list.isError && (
          <div className="p-3 text-xs text-error" role="alert">
            {m.error_generic()} <button type="button" className="underline" onClick={() => { void list.refetch() }}>{m.retry()}</button>
          </div>
        )}
        {list.isSuccess && filtered.length === 0 && <div className="p-3 text-xs text-muted">{filter ? m.no_matching_sessions() : m.no_sessions_yet()}</div>}
        {groups.map((g) => (
          <div key={g.id} className="session-group">
            <div className="session-group-label px-2 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wider text-muted">{g.label}</div>
            {g.rows.map((row) => {
              const active = row.session_id === activeId
              return (
                <Link
                  key={row.session_id}
                  to="/session/$sessionId"
                  params={{ sessionId: row.session_id }}
                  onClick={closeMobileSidebar}
                  role="listitem"
                  data-sid={row.session_id}
                  aria-current={active ? 'page' : undefined}
                  className={cn('session-item group relative mb-0.5 flex min-h-11 items-start gap-2 rounded-lg px-3 py-2.5 text-[13px] text-muted no-underline transition-colors hover:bg-hover', active && 'active bg-accent-bg text-accent')}
                >
                  <div className="min-w-0 flex-1">
                    <div className={cn('session-title truncate', active ? 'text-accent-text' : 'text-text')}>{row.title || m.untitled()}</div>
                    <div className="session-meta flex gap-1.5 truncate text-[11px] text-muted">
                      {row.is_streaming && <span className="text-accent-text">{m.status_streaming()}</span>}
                      {row.source_label && row.is_cli_session && <span>{row.source_label}</span>}
                      <span>{relativeTime(row.last_message_at ?? row.updated_at)}</span>
                      {row.message_count !== undefined && <span>· {m.session_meta_messages({ n: row.message_count })}</span>}
                    </div>
                  </div>
                  {row.attention && <span className="mt-1 h-2 w-2 shrink-0 rounded-full bg-warning" aria-label={m.session_attention_generic({ n: row.attention.count ?? 1 })} />}
                  <SessionContextMenu row={row} active={active} />
                </Link>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}
