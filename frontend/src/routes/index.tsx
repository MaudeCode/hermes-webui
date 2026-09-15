import { createFileRoute } from '@tanstack/react-router'
import { m } from '~/paraglide/messages.js'
import { useBootstrap } from '../app/bootstrap'

export const Route = createFileRoute('/')({
  component: IndexPage,
})

function IndexPage() {
  const bootstrap = useBootstrap()
  return (
    <main className="flex h-full flex-col items-center justify-center gap-2 bg-bg text-text">
      <h1 className="text-2xl font-semibold text-strong">{bootstrap.bot_name}</h1>
      <p className="text-sm text-muted">{m.tab_chat()}</p>
    </main>
  )
}
