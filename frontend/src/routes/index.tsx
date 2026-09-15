import { createFileRoute } from '@tanstack/react-router'
import { m } from '~/paraglide/messages.js'

export const Route = createFileRoute('/')({
  component: () => (
    <main className="flex h-full items-center justify-center bg-bg text-text">
      <h1 className="text-2xl font-semibold text-strong">{m.tab_chat()}</h1>
    </main>
  ),
})
