import { useCallback, useEffect, useMemo, useRef, useState, type ClipboardEvent, type DragEvent, type KeyboardEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Mic, Paperclip, Square, ArrowUp, TerminalSquare, PanelRight, SlidersHorizontal } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import type { Session, Settings } from '../../contracts'
import type { LiveTurn } from '../../stream/reducer'
import { isTerminal } from '../../stream/reducer'
import { cancelTurn, startTurn } from '../../stream/connection'
import { useBootstrap } from '../../app/bootstrap'
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
  const [configOpen, setConfigOpen] = useState(false)
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
    <div className="composer-wrap" id="composerWrap">
      {queued.length > 0 && (
        <div className="queue-card" role="region" aria-label={m.queued_count({ n: queued.length })} aria-live="polite">
          <div className="queue-card-title">{m.queued_count({ n: queued.length })}</div>
          <ul className="queue-card-list">{queued.map((q, i) => <li key={i}>{q}</li>)}</ul>
        </div>
      )}
      <div
        className={cn('composer-box', dragOver && 'drag-over')}
        id="composerBox"
        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        {palette.open && <CommandPaletteList items={palette.items} active={palette.active} listId={palette.listId} onPick={applySuggestion} onHover={palette.setActive} />}
        {dragOver && <div className="drop-hint active" id="dropHint" aria-hidden="true">{m.drop_files_to_attach()}</div>}
        <AttachmentTray files={files} onRemove={removeFile} />
        {dictating && <div className="mic-status active" id="micStatus" role="status"><span className="mic-dot" aria-hidden="true" /> {m.voice_listening()}</div>}
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
        />
        <div className={cn('composer-footer cf-burger', !text && files.length === 0 && !busy && 'cf-collapsed')}>
          <div className="composer-left">
            {!hide('hide_composer_attach') && (
              <>
                <input type="file" id="fileInput" multiple className="file-input-visually-hidden" onChange={(e) => { if (e.target.files) addFiles(e.target.files); e.target.value = '' }} accept="image/*,text/*,application/pdf,application/json,.csv,.md,.docx,.xlsx,.pptx" />
                <button type="button" className="icon-btn has-tooltip" id="btnAttach" data-tooltip={m.composer_control_attach()} aria-label={m.composer_control_attach()} onClick={() => document.getElementById('fileInput')?.click()}><Paperclip size={16} aria-hidden="true" /></button>
              </>
            )}
            {!hide('hide_composer_mic') && dictationSupported() && <button type="button" className={cn('icon-btn mic-btn has-tooltip', dictating && 'active')} id="btnMic" data-tooltip={dictating ? m.voice_dictate_active() : m.voice_dictate()} aria-label={dictating ? m.voice_dictate_active() : m.voice_dictate()} aria-pressed={dictating} onClick={toggleDictation}><Mic size={16} aria-hidden="true" /></button>}
            <button type="button" className={cn('icon-btn has-tooltip', terminalOpen && 'active')} id="btnTerminal" data-tooltip={m.composer_terminal_toggle()} aria-label={m.composer_terminal_toggle()} aria-pressed={terminalOpen} onClick={onToggleTerminal}><TerminalSquare size={16} aria-hidden="true" /></button>
            <div className="composer-divider" aria-hidden="true" />
            {yolo && !hide('hide_composer_yolo') && <button type="button" onClick={onToggleYolo} className="yolo-pill" id="yoloPill" title={m.yolo_pill_title_active()}><span className="yolo-pill-icon" aria-hidden="true">⚡</span><span className="yolo-pill-label">{m.yolo_pill_label()}</span></button>}
            <div className="composer-ws-wrap">
              <div className="composer-workspace-group ws-chip" id="composerWorkspaceGroup" role="group">
                <button className={cn('composer-workspace-files-btn', workspaceOpen && 'active')} id="btnWorkspacePanelToggle" type="button" title={m.composer_files_toggle()} aria-label={m.composer_files_toggle()} aria-pressed={workspaceOpen} onClick={onToggleWorkspace}><span className="composer-workspace-icon" aria-hidden="true"><PanelRight size={14} /></span></button>
              </div>
            </div>
            <button className="icon-btn composer-mobile-config-btn has-tooltip" id="composerMobileConfigBtn" type="button" data-tooltip={m.composer_config_title()} aria-label={m.composer_config_title()} aria-expanded={configOpen} aria-controls="composerMobileConfigPanel" onClick={() => setConfigOpen((o) => !o)}>
              <SlidersHorizontal size={16} aria-hidden="true" />
            </button>
            <div className={cn('composer-mobile-config-panel', configOpen && 'open')} id="composerMobileConfigPanel" role="group" aria-label={m.composer_config_title()}>
              {!hide('hide_composer_profile') && <div id="profileChipWrap" className="composer-profile-wrap"><ProfileMenu /></div>}
              {!hide('hide_composer_workspace') && <div className="composer-config-row"><WorkspaceChip value={session?.workspace ?? settings?.default_workspace} onChange={onWorkspaceChange} /></div>}
              {!hide('hide_composer_model') && <div className="composer-config-row composer-model-wrap"><ModelChip value={session?.model ?? null} defaultModel={settings?.default_model} onChange={onModelChange} /></div>}
              {!hide('hide_composer_reasoning') && <div className="composer-config-row composer-reasoning-wrap" id="composerReasoningWrap"><ReasoningChip value={reasoning} levels={reasoningLevels} onChange={onReasoningChange} /></div>}
              {!hide('hide_composer_toolsets') && <div className="composer-config-row composer-toolsets-wrap" id="composerToolsetsWrap"><ToolsetsChip value={session?.enabled_toolsets ?? null} onChange={onToolsetsChange} /></div>}
            </div>
          </div>
          <div className="composer-right">
            {!hide('hide_composer_context') && <ContextRing used={contextUsed} total={contextTotal} threshold={session?.threshold_tokens} />}
            {busy ? (
              <button type="button" onClick={() => { if (sessionId) void cancelTurn(sessionId) }} className="send-btn stop has-tooltip has-tooltip--left" id="btnStop" data-tooltip={m.composer_stop()} aria-label={m.composer_stop()} title={m.composer_stop()}>
                <Square size={14} aria-hidden="true" />
              </button>
            ) : (
              <button type="button" onClick={() => { void send() }} disabled={!canSend} className="send-btn has-tooltip has-tooltip--left" id="btnSend" data-tooltip={m.composer_send()} aria-label={m.composer_send()} title={m.composer_send()}>
                <ArrowUp size={16} aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
