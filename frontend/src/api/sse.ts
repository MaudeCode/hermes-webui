/**
 * The only module that constructs `EventSource`. Frames are parsed and
 * validated with the chat SSE union before anything else sees them.
 */
import { parseChatEvent, type ChatEvent, SessionListEventSchema, type SessionListEvent, CHAT_EVENT_NAMES } from '../contracts/sse'
import { resolveApiUrl } from './client'

export interface SseHandle {
  close(): void
  readonly readyState: () => number
}

export interface ChatStreamCallbacks {
  onEvent: (event: ChatEvent, lastEventId: string) => void
  onOpen?: () => void
  onError?: (readyState: number) => void
  onUnknown?: (name: string, data: string) => void
}

export function openChatStream(streamId: string, replay: { afterSeq: number; afterEventId: string } | null, cb: ChatStreamCallbacks): SseHandle {
  const params = new URLSearchParams({ stream_id: streamId })
  if (replay) {
    params.set('replay', '1')
    params.set('after_seq', String(replay.afterSeq))
    params.set('after_event_id', replay.afterEventId)
  }
  const source = new EventSource(resolveApiUrl(`api/chat/stream?${params.toString()}`).href, { withCredentials: true })
  const listener = (name: string) => (ev: Event) => {
    const me = ev as MessageEvent<string>
    const parsed = parseChatEvent(name, typeof me.data === 'string' ? me.data : '')
    if (parsed) cb.onEvent(parsed, me.lastEventId)
    else cb.onUnknown?.(name, me.data)
  }
  for (const name of CHAT_EVENT_NAMES) source.addEventListener(name, listener(name))
  source.onopen = () => cb.onOpen?.()
  source.onerror = () => cb.onError?.(source.readyState)
  return { close: () => source.close(), readyState: () => source.readyState }
}

export function openSessionListStream(cb: { onEvent: (event: SessionListEvent) => void; onError?: (readyState: number) => void; onOpen?: () => void }): SseHandle {
  const source = new EventSource(resolveApiUrl('api/sessions/events').href, { withCredentials: true })
  for (const name of ['initial', 'sessions_changed', 'gateway_status', 'hello'] as const) {
    source.addEventListener(name, (ev: Event) => {
      const me = ev as MessageEvent<string>
      let data: unknown = {}
      try {
        data = me.data ? JSON.parse(me.data) : {}
      } catch {
        return
      }
      const parsed = SessionListEventSchema.safeParse({ event: name, data })
      if (parsed.success) cb.onEvent(parsed.data)
    })
  }
  source.onopen = () => cb.onOpen?.()
  source.onerror = () => cb.onError?.(source.readyState)
  return { close: () => source.close(), readyState: () => source.readyState }
}

export const SSE_CONNECTING = 0
export const SSE_OPEN = 1
export const SSE_CLOSED = 2
