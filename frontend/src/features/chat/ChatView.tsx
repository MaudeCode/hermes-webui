import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { m } from '../../paraglide/messages.js'
import { cn } from '../../ui/cn'
import { ChevronLeft } from 'lucide-react'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import { useBootstrap } from '../../app/bootstrap'
import { useSettingsQuery } from '../../app/queries'
import type { Session } from '../../contracts'
import { configureStream, cancelTurn, startTurn } from '../../stream/connection'
import { dispatch } from '../../stream/store'
import { isTerminal } from '../../stream/reducer'
import { useTranscript, type VisibleMessage } from './useTranscript'
import { Transcript } from './Transcript'
import { Composer } from '../composer/Composer'
import { ApprovalCard } from './ApprovalCard'
import { ClarifyCard } from './ClarifyCard'
import { TerminalPanel } from '../terminal/TerminalPanel'
import { WorkspacePanel } from '../workspace/WorkspacePanel'
import { RuntimeNoticeStack } from '../notices/RuntimeNoticeStack'
import { showToast } from '../toast/toast'
import { isApiError } from '../../contracts/common'
import { ErrorState, formatDate } from '../../ui/States'
import { readPersisted, removePersisted } from '../../lib/persisted'
import type { ActivityMode } from './blocks/Worklog'
import { createSessionNow } from '../sessions/useNewChat'
import { useSessionSearch } from './useSessionSearch'

export function ChatView({ sessionId }: { sessionId: string | null }) {
  const bootstrap = useBootstrap()
  const qc = useQueryClient()
  const navigate = useNavigate()
  const settings = useSettingsQuery()
  const { query, session, rows, live, truncated, loadOlder, loadingOlder, refresh } = useTranscript(sessionId)
  const [terminalOpen, setTerminalOpen] = useState(false)
  const [workspaceOpen, setWorkspaceOpen] = useState(() => readPersisted('hermes-webui-workspace-panel') === 'open')
  const [queued, setQueued] = useState<string[]>([])
  const [reasoning, setReasoning] = useState<string | null>(null)
  const [yolo, setYolo] = useState(false)
  useSessionSearch(sessionId)

  useEffect(() => {
    configureStream({ queryClient: qc, onCompressed: (sid, next) => { if (sid === sessionId) void navigate({ to: '/session/$sessionId', params: { sessionId: next } }) } })
  }, [qc, navigate, sessionId])

  useEffect(() => {
    if (!sessionId) return
    api.fetchSessionYolo(sessionId).then((r) => setYolo(r.yolo_enabled)).catch(() => setYolo(false))
  }, [sessionId])
  const yoloOn = sessionId ? yolo : false

  // Queue drain: when the live turn settles, send the next queued message.
  useEffect(() => {
    if (!sessionId || !session || !live || !isTerminal(live.status)) return
    const next = queued[0]
    if (!next) return
    // Drain asynchronously: the store change that settled the turn is the trigger, not a render.
    queueMicrotask(() => setQueued((q) => (q[0] === next ? q.slice(1) : q)))
    void startTurn({ sessionId, message: next, request: { model: session.model ?? undefined, workspace: session.workspace, profile: bootstrap.profile?.name ?? 'default' } }).catch((e: unknown) => showToast(e instanceof Error ? e.message : String(e), 4000, 'error'))
  }, [live?.status, sessionId, session, queued, live, bootstrap.profile])

  const ensureSession = useCallback(async (): Promise<Session> => {
    if (session) return session
    const created = await createSessionNow({ ...(settings.data?.default_workspace ? { workspace: settings.data.default_workspace } : {}), profile: bootstrap.profile?.name ?? 'default' })
    qc.setQueryData(keys.sessions.detail(created.session_id), { session: created })
    await navigate({ to: '/session/$sessionId', params: { sessionId: created.session_id }, replace: true })
    return created
  }, [session, settings.data, bootstrap.profile, qc, navigate])

  const patchSession = useMutation({ mutationFn: (body: { model?: string; model_provider?: string | null; workspace?: string }) => api.updateSession(sessionId ?? '', body) })
  const updateSession = useCallback(async (body: { model?: string; model_provider?: string | null; workspace?: string }) => {
    if (!sessionId) return
    try {
      await patchSession.mutateAsync(body)
      await refresh()
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 4000, 'error')
    }
  }, [sessionId, patchSession, refresh])

  const onModelChange = useCallback((model: string, provider: string | null) => { void updateSession({ model, model_provider: provider }) }, [updateSession])
  const onWorkspaceChange = useCallback((path: string) => { void updateSession({ workspace: path }) }, [updateSession])
  const onToolsetsChange = useCallback((toolsets: string[] | null) => { if (!sessionId) return; void api.setSessionToolsets(sessionId, toolsets).then(() => refresh()).catch((e: unknown) => showToast(e instanceof Error ? e.message : String(e), 4000, 'error')) }, [sessionId, refresh])
  const onToggleYolo = useCallback(() => { if (!sessionId) return; void api.setSessionYolo(sessionId, !yolo).then((r) => setYolo(!!r.yolo_enabled)).catch((e: unknown) => showToast(e instanceof Error ? e.message : String(e), 4000, 'error')) }, [sessionId, yolo])

  const onRegenerate = useCallback(async () => {
    if (!sessionId) return
    const r = await api.retrySession(sessionId)
    const streamId = 'stream_id' in r ? r.stream_id : undefined
    const turnId = 'turn_id' in r ? r.turn_id : undefined
    if (typeof streamId === 'string' && streamId) dispatch({ type: 'start', sessionId, streamId, turnId: typeof turnId === 'string' ? turnId : null, userMessageId: null, userText: '', now: Date.now() })
    await refresh()
  }, [sessionId, refresh])

  const onLocalCommand = useCallback(async (name: string, args: string): Promise<boolean> => {
    switch (name) {
      case 'stop': if (sessionId) await cancelTurn(sessionId); return true
      case 'new': removePersisted('hermes-webui-session'); await navigate({ to: '/', search: { action: 'new-chat' } }); return true
      case 'clear': if (sessionId) { await api.clearSession(sessionId); await refresh() } return true
      case 'terminal': setTerminalOpen((t) => !t); return true
      case 'title': if (sessionId && args) { await api.renameSession(sessionId, args); await refresh(); void qc.invalidateQueries({ queryKey: keys.sessions.all }) } return true
      case 'retry': if (sessionId) { await onRegenerate() } return true
      case 'undo': if (sessionId) { await api.undoSession(sessionId); await refresh() } return true
      case 'compress': case 'compact': if (sessionId) { await api.compressSession(sessionId); showToast(m.live_compressing()) } return true
      case 'usage': if (sessionId) { const u = await api.fetchSessionUsage(sessionId); showToast(`${(u.input_tokens ?? 0).toLocaleString()} in · ${(u.output_tokens ?? 0).toLocaleString()} out${u.estimated_cost ? ` · $${u.estimated_cost.toFixed(4)}` : ''}`, 4000) } return true
      case 'yolo': onToggleYolo(); return true
      case 'branch': if (sessionId) { const r = await api.branchSession(sessionId, rows.length); await navigate({ to: '/session/$sessionId', params: { sessionId: r.session.session_id } }) } return true
      case 'reasoning': setReasoning(args || null); return true
      case 'model': if (args) onModelChange(args, null); return true
      case 'workspace': if (args) onWorkspaceChange(args); return true
      case 'personality': if (sessionId) { await api.setPersonality(sessionId, args || null); await refresh() } return true
      case 'goal': if (sessionId) { const r = await api.goalCommand(sessionId, args ? 'set' : 'status', args || undefined); showToast(r.message ?? (r.goal?.text ?? m.done())) } return true
      case 'status': if (sessionId) { const s = await api.fetchSessionStatus(sessionId); showToast(`${s.model ?? ''} · ${s.message_count ?? 0} msgs · ${s.agent_running ? m.status_streaming() : m.done()}`, 4000) } return true
      case 'help': await navigate({ to: '/settings/$section', params: { section: 'help' } }); return true
      case 'skills': await navigate({ to: '/skills' }); return true
      case 'use': return false
      case 'voice': showToast(m.voice_error(), 3000); return true
      default: return false
    }
  }, [sessionId, navigate, refresh, qc, onToggleYolo, rows.length, onModelChange, onWorkspaceChange, onRegenerate])

  const onEdit = useCallback(async (row: VisibleMessage, text: string) => {
    if (!sessionId) return
    const keep = row.index
    await api.truncateSession(sessionId, keep)
    await refresh()
    const el = document.getElementById('msg') as HTMLTextAreaElement | null
    if (el) { el.value = text; el.dispatchEvent(new Event('input', { bubbles: true })); el.focus() }
  }, [sessionId, refresh])
  const onBranch = useCallback(async (row: VisibleMessage) => {
    if (!sessionId) return
    const r = await api.branchSession(sessionId, row.index + 1)
    await navigate({ to: '/session/$sessionId', params: { sessionId: r.session.session_id } })
  }, [sessionId, navigate])

  const mode = (settings.data?.chat_activity_display_mode as ActivityMode | undefined) ?? 'compact_worklog'
  const assistantName = bootstrap.profile && !bootstrap.profile.is_default ? bootstrap.profile.name.charAt(0).toUpperCase() + bootstrap.profile.name.slice(1) : bootstrap.bot_name
  const title = session?.title ?? ''
  const workspace = session?.workspace ?? settings.data?.default_workspace
  const meta = useMemo(() => [session?.model, session?.message_count !== undefined ? m.session_meta_messages({ n: session.message_count }) : null, session?.updated_at ? formatDate(session.updated_at) : null].filter(Boolean).join(' · '), [session])

  const notFound = query.isError && isApiError(query.error) && query.error.status === 404
  const otherProfile = query.isError && isApiError(query.error) && query.error.status === 409

  const emptyState = (
    <div className="empty-state" id="emptyState">
      <h2 className="empty-hero-title ready" id="emptyHeroTitle">{workspace ? m.empty_hero_title_workspace({ a0: workspace.split('/').filter(Boolean).pop() ?? workspace }) : m.empty_hero_title()}</h2>
    </div>
  )
  const wsLabel = workspace ? (workspace.split('/').filter(Boolean).pop() ?? workspace) : ''
  const openChip = (id: string) => { const el = document.getElementById(id); if (el instanceof HTMLElement) el.click() }

  return (
    <>
      <div id="mainChat" className={cn('main-view active', rows.length === 0 && !live && 'composer-hero')}>
        <div className="chat-header">
          <div className="chat-header-text">
            <h1 className="chat-header-title" id="topbarTitle">{session ? (title || m.untitled()) : bootstrap.bot_name}</h1>
            {session && meta && <div className="chat-header-meta" id="topbarMeta">{meta}</div>}
            <div className="chat-context">
              <button type="button" className="chat-context-item chat-context-profile" onClick={() => openChip('profileChip')}>{bootstrap.profile?.name ?? 'default'}</button>
              {(session?.model ?? settings.data?.default_model) && <button type="button" className="chat-context-item chat-context-model" onClick={() => openChip('composerModelChip')}>{session?.model ?? settings.data?.default_model}</button>}
              {reasoning && <button type="button" className="chat-context-item chat-context-effort" onClick={() => openChip('composerReasoningChip')}>{reasoning}</button>}
              {wsLabel && <button type="button" className="chat-context-item chat-context-workspace" onClick={() => openChip('composerWorkspaceChip')}>{wsLabel}</button>}
            </div>
          </div>
        </div>
        <RuntimeNoticeStack live={live} onRetry={() => { void onRegenerate() }} />
        {query.isPending && sessionId && <div className="p-4 text-sm text-muted" role="status">{m.transcript_loading()}</div>}
        {notFound && <div className="p-4"><ErrorState error={new Error(m.transcript_not_found())} onRetry={() => { void navigate({ to: '/', search: { action: 'new-chat' } }) }} /></div>}
        {otherProfile && <div className="p-4"><ErrorState error={new Error(m.transcript_other_profile({ profile: ((query.error as { body?: { profile?: string } }).body?.profile ?? '') }))} /></div>}
        {query.isError && !notFound && !otherProfile && <div className="p-4"><ErrorState error={query.error} onRetry={() => { void refresh() }} /></div>}
        {(!sessionId || query.isSuccess) && (
          <Transcript
            rows={rows}
            live={live}
            assistantName={assistantName}
            mode={mode}
            renderUserMarkdown={!!settings.data?.render_user_markdown}
            autoFollow={settings.data?.auto_scroll_follow !== false}
            workspace={workspace}
            actions={{ onEdit: (row, text) => { void onEdit(row, text) }, onBranch: (row) => { void onBranch(row) }, onRegenerate: () => { void onRegenerate() } }}
            tts={!!(settings.data as Record<string, unknown> | undefined)?.tts_enabled}
            truncated={truncated}
            onLoadOlder={() => { void loadOlder() }}
            loadingOlder={loadingOlder}
            emptyState={emptyState}
            showJumpButtons={(settings.data as Record<string, unknown> | undefined)?.session_jump_buttons !== false}
          />
        )}
        <div className="composer-flyout">
          {sessionId && live?.approval && <ApprovalCard sessionId={sessionId} pending={live.approval} onResolved={() => dispatch({ type: 'clear_approval', sessionId })} />}
          {sessionId && live?.clarify && <ClarifyCard sessionId={sessionId} pending={live.clarify} onResolved={() => dispatch({ type: 'clear_clarify', sessionId })} />}
          {terminalOpen && sessionId && <TerminalPanel sessionId={sessionId} workspace={workspace} onClose={() => setTerminalOpen(false)} />}
        </div>
        <Composer
          sessionId={sessionId}
          session={session}
          live={live}
          settings={settings.data}
          onEnsureSession={ensureSession}
          onLocalCommand={onLocalCommand}
          terminalOpen={terminalOpen}
          onToggleTerminal={() => setTerminalOpen((t) => !t)}
          workspaceOpen={workspaceOpen}
          onToggleWorkspace={() => setWorkspaceOpen((w) => !w)}
          onModelChange={onModelChange}
          onWorkspaceChange={onWorkspaceChange}
          onToolsetsChange={onToolsetsChange}
          onReasoningChange={setReasoning}
          reasoning={reasoning}
          yolo={yoloOn}
          onToggleYolo={onToggleYolo}
          queued={queued}
          onQueue={(t) => setQueued((q) => [...q, t])}
        />
        <span className="sr-only" aria-live="polite" id="a11yAnnouncer">{live?.status === 'done' ? m.done() : ''}</span>
      </div>
      {workspaceOpen && workspace && sessionId && <WorkspacePanel key={workspace} workspace={workspace} sessionId={sessionId} onClose={() => setWorkspaceOpen(false)} />}
      {!workspaceOpen && sessionId && workspace && (
        <button type="button" className="workspace-panel-edge-toggle has-tooltip has-tooltip--left" id="btnWorkspacePanelEdgeToggle" data-tooltip={m.composer_files_toggle()} aria-label={m.composer_files_toggle()} onClick={() => setWorkspaceOpen(true)}>
          <ChevronLeft size={14} aria-hidden="true" />
        </button>
      )}
    </>
  )
}
