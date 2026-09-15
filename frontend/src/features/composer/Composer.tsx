import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Mic, Paperclip, Square, ArrowUp, TerminalSquare, PanelRight, Zap } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import type { Session, Settings } from '../../contracts'
import type { LiveTurn } from '../../stream/reducer'
import { isTerminal } from '../../stream/reducer'
import { cancelTurn, startTurn } from '../../stream/connection'
import { useBootstrap } from '../../app/bootstrap'
import { IconButton } from '../../ui/Button'
import { cn } from '../../ui/cn'
import { showToast } from '../toast/toast'
import { AttachmentTray, type PendingFile } from './Attachments'
import { CommandPaletteList, useCommandPalette } from './CommandPalette'
import { parseCommand, type CommandSuggestion } from './commands'
import { ContextRing, ModelChip, ReasoningChip, ToolsetsChip, WorkspaceChip } from './chips'
import { clearDraft, readLocalDraft, useDraftPersistence } from './useDraft'
import { createRecognition, dictationSupported, classifyDictationError } from '../voice/dictation'
import { ProfileMenu } from '../../shell/ProfileMenu'
import { setTheme } from '../../app/appearance'
import { ThemeSchema } from '../../contracts/persisted'

export type BusyMode = 'steer' | 'queue' | 'interrupt'

export interface ComposerProps {
  sessionId: string | null
  session: Session | null
  live: LiveTurn | null
  settings: Settings | undefined
  onEnsureSession: () => Promise<Session>
  onLocalCommand: (name: string, args: string) => Promise<boolean>
  terminalOpen: boolean
  onToggleTerminal: () => void
  workspaceOpen: boolean
  onToggleWorkspace: () => void
  onModelChange: (model: string, provider: string | null) => void
  onWorkspaceChange: (path: string) => void
  onToolsetsChange: (toolsets: string[] | null) => void
  onReasoningChange: (level: string | null) => void
  reasoning: string | null
  yolo: boolean
  onToggleYolo: () => void
  queued: string[]
  onQueue: (text: string) => void
}

function fileKey(f: File): string {
  return `${f.name}:${f.size}:${f.lastModified}`
}

/**
 * The composer: textarea with send-key preference, attachments (click, drop,
 * paste), slash commands, busy modes (steer / queue / interrupt), dictation,
 * and the model, reasoning, toolsets, workspace and profile chips.
 */
export function Composer(props: ComposerProps) {
  const { sessionId, session, live, settings, onEnsureSession, onLocalCommand, terminalOpen, onToggleTerminal, workspaceOpen, onToggleWorkspace, onModelChange, onWorkspaceChange, onToolsetsChange, onReasoningChange, reasoning, yolo, onToggleYolo, queued, onQueue } = props
  const bootstrap = useBootstrap()
  const qc = useQueryClient()
  const [text, setText] = useState(() => (sessionId ? readLocalDraft(sessionId) : ''))
  const [files, setFiles] = useState<PendingFile[]>([])
  const [sending, setSending] = useState(false)
  const [dictating, setDictating] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const textarea = useRef<HTMLTextAreaElement>(null)
  const recognition = useRef<ReturnType<typeof createRecognition>>(null)
  const busy = !!live && !isTerminal(live.status)
  const busyMode: BusyMode = (settings?.default_message_mode as BusyMode | undefined) ?? 'steer'
  const sendKey = settings?.send_key ?? 'enter'
  const palette = useCommandPalette(text)
  useDraftPersistence(sessionId, text)

  useEffect(() => { setText(sessionId ? readLocalDraft(sessionId) : ''); setFiles([]) }, [sessionId])

  // Autosize.
  useEffect(() => {
    const el = textarea.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 320)}px`
  }, [text])

  const maxBytes = bootstrap.max_upload_bytes
  const addFiles = useCallback((incoming: FileList | File[]) => {
    const list = Array.from(incoming)
    if (list.length === 0) return
    for (const f of list) {
      if (f.size > maxBytes) { showToast(m.composer_too_large({ name: f.name, max: Math.round(maxBytes / 1024 / 1024) }), 4000, 'error'); continue }
      const key = fileKey(f)
      setFiles((prev) => (prev.some((p) => p.key === key) ? prev : [...prev, { key, file: f, status: 'uploading' }]))
      void (async () => {
        try {
          const target = session ?? (await onEnsureSession())
          const upload = await api.uploadFile(target.session_id, f)
          setFiles((prev) => prev.map((p) => (p.key === key ? { ...p, status: 'done', upload } : p)))
        } catch (e) {
          setFiles((prev) => prev.map((p) => (p.key === key ? { ...p, status: 'error', error: e instanceof Error ? e.message : String(e) } : p)))
          showToast(m.composer_upload_failed({ name: f.name }), 4000, 'error')
        }
      })()
    }
  }, [maxBytes, session, onEnsureSession])

  const removeFile = (key: string) => {
    const f = files.find((p) => p.key === key)
    setFiles((prev) => prev.filter((p) => p.key !== key))
    if (f?.upload?.rollback_token && session) void api.rollbackUpload(session.session_id, [f.upload.rollback_token]).catch(() => undefined)
  }

  const steer = useMutation({ mutationFn: (message: string) => api.steerChat({ session_id: sessionId ?? '', message, mode: 'steer' }) })

  const send = useCallback(async () => {
    const value = text.trim()
    if (sending) return
    if (!value && files.length === 0) return
    if (files.some((f) => f.status === 'uploading')) { showToast(m.loading()); return }
    const cmd = parseCommand(value)
    if (cmd) {
      if (['stop', 'new', 'clear', 'terminal', 'title', 'retry', 'undo', 'compress', 'compact', 'usage', 'theme', 'yolo', 'branch', 'voice', 'reasoning', 'model', 'workspace', 'help', 'status', 'personality', 'goal', 'skills', 'use'].includes(cmd.name)) {
        if (cmd.name === 'theme') { const v = ThemeSchema.safeParse(cmd.args); if (v.success) setTheme(v.data); setText(''); return }
        const handled = await onLocalCommand(cmd.name, cmd.args)
        if (handled) { setText(''); return }
      }
      if (cmd.name === 'queue' && busy) { onQueue(cmd.args); setText(''); return }
      if (cmd.name === 'steer' && busy && sessionId) { await steer.mutateAsync(cmd.args); setText(''); return }
      if (cmd.name === 'interrupt' && busy && sessionId) { await cancelTurn(sessionId); onQueue(cmd.args); setText(''); return }
    }
    if (busy && sessionId) {
      if (busyMode === 'queue') { onQueue(value); setText(''); return }
      if (busyMode === 'steer') { await steer.mutateAsync(value); setText(''); showToast(m.composer_steer_hint(), 1500); return }
      await cancelTurn(sessionId)
      onQueue(value)
      setText('')
      return
    }
    setSending(true)
    try {
      const target = session ?? (await onEnsureSession())
      const attachments = files.flatMap((f) => (f.status === 'done' && f.upload ? [f.upload] : []))
      await startTurn({ sessionId: target.session_id, message: value, request: { model: target.model ?? undefined, model_provider: target.model_provider ?? undefined, workspace: target.workspace, profile: bootstrap.profile?.name ?? 'default', ...(attachments.length ? { attachments } : {}) } })
      setText('')
      setFiles([])
      clearDraft(target.session_id)
      void qc.invalidateQueries({ queryKey: keys.sessions.all })
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 5000, 'error')
    } finally {
      setSending(false)
      textarea.current?.focus()
    }
  }, [text, files, sending, session, onEnsureSession, busy, busyMode, sessionId, steer, onQueue, onLocalCommand, bootstrap.profile, qc])

  const applySuggestion = (s: CommandSuggestion) => { setText(`/${s.name} `); textarea.current?.focus() }
  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (palette.handleKey(e, applySuggestion)) return
    if (e.key !== 'Enter') return
    const isNumpad = e.code === 'NumpadEnter'
    const mobile = window.matchMedia('(max-width: 640px)').matches
    if (sendKey === 'ctrl+enter' || mobile) {
      if (isNumpad || e.ctrlKey || e.metaKey) { e.preventDefault(); void send() }
      return
    }
    if (!e.shiftKey) { e.preventDefault(); void send() }
  }
  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(e.clipboardData.items)
    const images = items.filter((i) => i.kind === 'file').map((i) => i.getAsFile()).filter((f): f is File => !!f)
    if (images.length) { e.preventDefault(); addFiles(images); return }
    const pasted = e.clipboardData.getData('text/plain')
    if (settings?.large_text_paste_as_attachment !== false && pasted.length > 8000) {
      e.preventDefault()
      addFiles([new File([pasted], `pasted-${Date.now()}.txt`, { type: 'text/plain' })])
      showToast(m.text_pasted() + `pasted-${Date.now()}.txt`, 2500)
    }
  }
  const onDrop = (e: DragEvent<HTMLDivElement>) => { e.preventDefault(); setDragOver(false); addFiles(e.dataTransfer.files) }

  const toggleDictation = () => {
    if (!dictationSupported()) { showToast(m.composer_dictation_unsupported(), 3000, 'error'); return }
    if (dictating) { recognition.current?.stop(); return }
    const r = createRecognition(document.documentElement.lang || 'en-US')
    if (!r) return
    recognition.current = r
    const base = text
    r.onresult = (ev) => {
      let transcript = ''
      for (const result of Array.from(ev.results)) transcript += result[0]?.transcript ?? ''
      setText(settings?.dictation_append === false ? transcript : `${base}${base && !base.endsWith(' ') ? ' ' : ''}${transcript}`)
    }
    r.onerror = (ev) => { const kind = classifyDictationError(ev.error); showToast(kind === 'denied' ? m.mic_denied() : kind === 'no_speech' ? m.mic_no_speech() : kind === 'network' ? m.mic_network() : m.mic_error() + ev.error, 3000, 'error'); setDictating(false) }
    r.onend = () => { setDictating(false); recognition.current = null }
    r.start()
    setDictating(true)
  }

  const hide = (k: string) => !!(settings as Record<string, unknown> | undefined)?.[k]
  const placeholder = busy ? (busyMode === 'queue' ? m.composer_placeholder_busy_queue() : busyMode === 'interrupt' ? m.composer_placeholder_busy_interrupt() : m.composer_placeholder_busy_steer()) : m.composer_placeholder()
  const contextUsed = session?.last_prompt_tokens ?? null
  const contextTotal = session?.context_length ?? null
  const canSend = (text.trim() !== '' || files.some((f) => f.status === 'done')) && !sending
  const reasoningLevels = useMemo(() => undefined, [])

  return (
    <div className="composer-wrap shrink-0 bg-bg px-5 pb-4 pt-3 max-[768px]:px-3 max-[768px]:pb-3" id="composerWrap" style={{ paddingBottom: 'max(16px, env(safe-area-inset-bottom, 0px))' }}>
      {queued.length > 0 && (
        <div className="queue-card mx-auto mb-2 w-full max-w-[var(--msg-max)] rounded-lg border border-border bg-surface px-3 py-2 text-xs text-muted" role="region" aria-label={m.queued_count({ n: queued.length })} aria-live="polite">
          <div className="mb-1 font-medium text-text">{m.queued_count({ n: queued.length })}</div>
          <ul className="flex flex-col gap-1">{queued.map((q, i) => <li key={i} className="truncate">{q}</li>)}</ul>
        </div>
      )}
      <div
        className={cn('composer-box relative mx-auto flex w-full max-w-[var(--msg-max)] flex-col rounded-2xl border border-border2 bg-input transition-colors focus-within:border-accent', dragOver && 'border-accent bg-accent-bg')}
        id="composerBox"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {palette.open && <CommandPaletteList items={palette.items} active={palette.active} listId={palette.listId} onPick={applySuggestion} onHover={palette.setActive} />}
        {dragOver && <div className="drop-hint pointer-events-none absolute inset-0 z-10 flex items-center justify-center rounded-2xl bg-accent-bg text-sm text-accent-text" aria-hidden="true">{m.drop_files_to_attach()}</div>}
        <AttachmentTray files={files} onRemove={removeFile} />
        {dictating && <div className="mic-status flex items-center gap-2 px-3.5 pt-2 text-xs text-accent-text" role="status"><span className="h-2 w-2 animate-pulse rounded-full bg-error" aria-hidden="true" /> {m.voice_listening()}</div>}
        <textarea
          ref={textarea}
          id="msg"
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          onPaste={onPaste}
          placeholder={placeholder}
          aria-label={m.composer_placeholder()}
          aria-autocomplete={palette.open ? 'list' : undefined}
          aria-controls={palette.open ? palette.listId : undefined}
          aria-activedescendant={palette.activeId}
          role={palette.open ? 'combobox' : undefined}
          aria-expanded={palette.open ? true : undefined}
          className="max-h-80 min-h-11 resize-none bg-transparent px-3.5 pb-1.5 pt-3 text-base text-text outline-none placeholder:text-muted max-[768px]:text-[14.5px]"
        />
        <div className="composer-footer flex items-center justify-between gap-2 px-2 pb-2 pt-1">
          <div className="composer-left flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none]">
            {!hide('hide_composer_attach') && (
              <>
                <input type="file" id="fileInput" multiple className="sr-only" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} accept="image/*,text/*,application/pdf,application/json,.csv,.md,.docx,.xlsx,.pptx" />
                <IconButton label={m.composer_control_attach()} onClick={() => document.getElementById('fileInput')?.click()}><Paperclip size={16} aria-hidden="true" /></IconButton>
              </>
            )}
            {!hide('hide_composer_mic') && dictationSupported() && <IconButton label={dictating ? m.voice_dictate_active() : m.voice_dictate()} active={dictating} onClick={toggleDictation}><Mic size={16} aria-hidden="true" /></IconButton>}
            <IconButton label={m.composer_terminal_toggle()} active={terminalOpen} onClick={onToggleTerminal}><TerminalSquare size={16} aria-hidden="true" /></IconButton>
            <IconButton label={m.composer_files_toggle()} active={workspaceOpen} onClick={onToggleWorkspace} aria-pressed={workspaceOpen}><PanelRight size={16} aria-hidden="true" /></IconButton>
            <span className="composer-divider mx-1 h-5 w-px bg-border" aria-hidden="true" />
            {yolo && !hide('hide_composer_yolo') && <button type="button" onClick={onToggleYolo} className="yolo-pill inline-flex h-7 items-center gap-1 rounded-full border border-warning px-2 text-[11px] font-semibold text-warning" title={m.yolo_pill_title_active()}><Zap size={12} aria-hidden="true" /> {m.yolo_pill_label()}</button>}
            {!hide('hide_composer_profile') && <ProfileMenu />}
            {!hide('hide_composer_workspace') && <WorkspaceChip value={session?.workspace ?? settings?.default_workspace} onChange={onWorkspaceChange} />}
            {!hide('hide_composer_model') && <ModelChip value={session?.model ?? null} defaultModel={settings?.default_model} onChange={onModelChange} />}
            {!hide('hide_composer_reasoning') && <ReasoningChip value={reasoning} levels={reasoningLevels} onChange={onReasoningChange} />}
            {!hide('hide_composer_toolsets') && <ToolsetsChip value={session?.enabled_toolsets ?? null} onChange={onToolsetsChange} />}
          </div>
          <div className="composer-right flex shrink-0 items-center gap-2">
            {!hide('hide_composer_context') && <ContextRing used={contextUsed} total={contextTotal} threshold={session?.threshold_tokens} />}
            {busy ? (
              <button type="button" onClick={() => { if (sessionId) void cancelTurn(sessionId) }} className="send-btn stop flex h-[34px] w-[34px] items-center justify-center rounded-full bg-error text-white shadow-md max-[768px]:h-11 max-[768px]:w-11" aria-label={m.composer_stop()} title={m.composer_stop()} id="btnStop">
                <Square size={14} aria-hidden="true" />
              </button>
            ) : (
              <button type="button" onClick={() => { void send() }} disabled={!canSend} className="send-btn flex h-[34px] w-[34px] items-center justify-center rounded-full bg-accent text-white shadow-md transition-transform disabled:opacity-40 max-[768px]:h-11 max-[768px]:w-11" aria-label={m.composer_send()} title={m.composer_send()} id="btnSend">
                <ArrowUp size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
