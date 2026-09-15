import { m } from '../../paraglide/messages.js'
import { useBootstrap } from '../../app/bootstrap'

/** Placeholder until the chat stream reducer, transcript and composer land (checkpoint 6). */
export function ChatView({ sessionId }: { sessionId: string | null }) {
  const bootstrap = useBootstrap()
  return (
    <div id="mainChat" className="main-view composer-hero flex min-h-0 flex-1 flex-col bg-bg">
      <div className="messages flex min-h-0 flex-1 flex-col items-center justify-end px-5" id="messages">
        <div className="empty-state flex flex-col items-center px-5 pb-4 pt-6 text-muted" id="emptyState">
          <h2 className="empty-hero-title text-center text-[26px] font-semibold leading-tight tracking-tight text-text" id="emptyHeroTitle">{bootstrap.bot_name}</h2>
          {sessionId && <p className="mt-2 text-xs">{sessionId}</p>}
        </div>
      </div>
      <div className="composer-wrap shrink-0 bg-bg px-5 pb-4 pt-3" id="composerWrap">
        <div className="composer-box mx-auto flex max-w-[var(--msg-max,820px)] flex-col rounded-2xl border border-border2 bg-input" id="composerBox">
          <textarea id="msg" rows={1} placeholder={m.composer_placeholder()} className="min-h-11 resize-none bg-transparent px-3.5 pb-1.5 pt-3 text-base text-text outline-none placeholder:text-muted" aria-label={m.composer_placeholder()} />
        </div>
      </div>
    </div>
  )
}
