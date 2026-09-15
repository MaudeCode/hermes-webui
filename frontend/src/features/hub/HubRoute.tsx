import type { ReactNode } from 'react'
import { AppShell } from '../../shell/AppShell'
import { PanelHead } from '../../shell/Sidebar'
import { NAV_ITEMS, type PanelId } from '../../shell/nav'
import { useLocale } from '../../i18n/useLocale'
import { TasksPage } from '../tasks/TasksPage'
import { KanbanPage } from '../kanban/KanbanPage'
import { SkillsPage } from '../skills/SkillsPage'
import { MemoryPage } from '../memory/MemoryPage'
import { WorkspacesPage } from '../workspaces/WorkspacesPage'
import { ProfilesPage } from '../profiles/ProfilesPage'
import { TodosPage } from '../todos/TodosPage'
import { InsightsPage } from '../insights/InsightsPage'
import { LogsPage } from '../logs/LogsPage'
import { SessionListPanel } from '../sessions/SessionListPanel'

export type HubPanel = Exclude<PanelId, 'chat' | 'settings'>

const PAGES: Record<HubPanel, () => ReactNode> = {
  tasks: () => <TasksPage />,
  kanban: () => <KanbanPage />,
  skills: () => <SkillsPage />,
  memory: () => <MemoryPage />,
  workspaces: () => <WorkspacesPage />,
  profiles: () => <ProfilesPage />,
  todos: () => <TodosPage />,
  insights: () => <InsightsPage />,
  logs: () => <LogsPage />,
}

/** Hub layout: the collection is the main view; the sidebar keeps the conversation list so chat stays one click away. */
export function HubRoute({ panel }: { panel: HubPanel }) {
  useLocale()
  const item = NAV_ITEMS.find((n) => n.id === panel)
  const title = item ? item.label() : panel
  return (
    <AppShell sidebar={<SessionListPanel />} subtitle={title}>
      <div className="hidden"><PanelHead title={title} /></div>
      {PAGES[panel]()}
    </AppShell>
  )
}
