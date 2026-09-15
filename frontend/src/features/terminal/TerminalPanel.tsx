import { useEffect, useRef, useState } from 'react'
import { Terminal } from '@xterm/xterm'
import { FitAddon } from '@xterm/addon-fit'
import { WebLinksAddon } from '@xterm/addon-web-links'
import '@xterm/xterm/css/xterm.css'
import { m } from '../../paraglide/messages.js'
import { post, get } from '../../api/client'
import { z } from 'zod'
import { Button } from '../../ui/Button'
import { showToast } from '../toast/toast'

const StartSchema = z.looseObject({ ok: z.boolean().optional(), running: z.boolean().optional(), error: z.string().optional(), message: z.string().optional() })
const OutputSchema = z.looseObject({ output: z.string().optional(), data: z.string().optional(), exit_code: z.number().nullable().optional(), closed: z.boolean().optional(), error: z.string().optional() })
const OkSchema = z.looseObject({ ok: z.boolean().optional(), closed: z.boolean().optional(), error: z.string().optional() })

/**
 * Workspace terminal on bundled xterm (no CDN). Protocol: POST start, POST input,
 * GET output (polled), POST resize, POST close, all keyed by session id.
 */
export function TerminalPanel({ sessionId, workspace, onClose }: { sessionId: string; workspace: string | undefined; onClose: () => void }) {
  const host = useRef<HTMLDivElement>(null)
  const term = useRef<Terminal | null>(null)
  const fit = useRef<FitAddon | null>(null)
  const [status, setStatus] = useState<'starting' | 'running' | 'closed' | 'error'>('starting')
  const [height, setHeight] = useState(260)
  const cursor = useRef(0)

  useEffect(() => {
    const el = host.current
    if (!el) return
    const t = new Terminal({ cursorBlink: true, fontFamily: 'var(--font-mono)', fontSize: 12.5, theme: { background: 'transparent' }, allowTransparency: true, convertEol: true })
    const f = new FitAddon()
    t.loadAddon(f)
    t.loadAddon(new WebLinksAddon())
    t.open(el)
    f.fit()
    term.current = t
    fit.current = f
    let stopped = false
    let timer: number | null = null
    const poll = async () => {
      if (stopped) return
      try {
        const out = await get(`api/terminal/output?session_id=${encodeURIComponent(sessionId)}&offset=${cursor.current}`, OutputSchema, { retries: 0, dedupe: false, timeoutMs: 15_000 })
        const chunk = out.output ?? out.data ?? ''
        if (chunk) { t.write(chunk); cursor.current += chunk.length }
        if (out.closed || (out.exit_code !== undefined && out.exit_code !== null)) { setStatus('closed'); return }
      } catch {
        /* transient; keep polling */
      }
      timer = window.setTimeout(() => { void poll() }, 250)
    }
    void (async () => {
      try {
        const res = await post('api/terminal/start', { session_id: sessionId, workspace, cols: t.cols, rows: t.rows }, StartSchema, { retries: 0, timeoutMs: 20_000 })
        if (res.error) { setStatus('error'); t.writeln(res.error); return }
        setStatus('running')
        void poll()
      } catch (e) {
        setStatus('error')
        t.writeln(e instanceof Error ? e.message : String(e))
      }
    })()
    const data = t.onData((d) => { void post('api/terminal/input', { session_id: sessionId, data: d }, OkSchema, { retries: 0, timeoutMs: 10_000 }).catch(() => undefined) })
    const resize = t.onResize(({ cols, rows }) => { void post('api/terminal/resize', { session_id: sessionId, cols, rows }, OkSchema, { retries: 0 }).catch(() => undefined) })
    const ro = new ResizeObserver(() => { try { f.fit() } catch { /* not attached */ } })
    ro.observe(el)
    return () => {
      stopped = true
      if (timer) window.clearTimeout(timer)
      data.dispose(); resize.dispose(); ro.disconnect(); t.dispose()
      term.current = null
    }
  }, [sessionId, workspace])

  const close = async () => {
    await post('api/terminal/close', { session_id: sessionId }, OkSchema, { retries: 0 }).catch(() => undefined)
    onClose()
  }
  const restart = async () => {
    try {
      await post('api/terminal/start', { session_id: sessionId, workspace, restart: true, cols: term.current?.cols, rows: term.current?.rows }, StartSchema, { retries: 0 })
      term.current?.clear()
      cursor.current = 0
      setStatus('running')
    } catch (e) {
      showToast(e instanceof Error ? e.message : String(e), 4000, 'error')
    }
  }
  const startResize = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = height
    const move = (ev: PointerEvent) => setHeight(Math.min(600, Math.max(120, startH - (ev.clientY - startY))))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  return (
    <div className="composer-terminal-panel mx-auto mb-2 w-full max-w-[var(--msg-max)] rounded-xl border border-border bg-surface" id="composerTerminalPanel" data-status={status}>
      <div className="composer-terminal-resize-handle h-1.5 cursor-row-resize rounded-t-xl hover:bg-accent-bg-strong" role="separator" aria-orientation="horizontal" aria-label="Resize terminal" tabIndex={0} onPointerDown={startResize} />
      <div className="composer-terminal-header flex items-center justify-between gap-2 border-b border-border px-3 py-1.5 text-xs">
        <div className="composer-terminal-title flex items-center gap-2 text-text"><span>{m.terminal_title()}</span><span className="text-muted">·</span><span className="truncate font-mono text-muted" id="terminalWorkspaceLabel">{workspace ?? ''}</span>{status === 'error' && <span className="text-error">{m.terminal_unavailable()}</span>}</div>
        <div className="composer-terminal-actions flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={() => term.current?.clear()}>{m.terminal_clear()}</Button>
          <Button size="sm" variant="ghost" onClick={() => { const sel = term.current?.getSelection() ?? ''; void navigator.clipboard.writeText(sel).then(() => showToast(m.copied())) }}>{m.terminal_copy_output()}</Button>
          <Button size="sm" variant="ghost" onClick={() => { void restart() }}>{m.terminal_restart()}</Button>
          <Button size="sm" variant="ghost" onClick={() => { void close() }}>{m.terminal_close()}</Button>
        </div>
      </div>
      <div ref={host} className="composer-terminal-surface px-2 py-1" style={{ height }} aria-label="Workspace terminal" role="application" />
    </div>
  )
}
