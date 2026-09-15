import type { ReactNode } from 'react'
import { AppShell, HubPage } from '../../shell/AppShell'
import { PanelHead } from '../../shell/Sidebar'
import { NAV_ITEMS, type PanelId } from '../../shell/nav'
import { m } from '../../paraglide/messages.js'
import { useLocale } from '../../i18n/useLocale'

export type HubPanel = Exclude<PanelId, 'chat' | 'settings'>

const PAGES: Partial<Record<HubPanel, () => ReactNode>> = {}

/** Registry so each panel's page module can register itself (filled in checkpoint 5). */
export function registerHubPage(panel: HubPanel, render: () => ReactNode): void {
  PAGES[panel] = render
}

export function HubRoute({ panel, sidebar }: { panel: HubPanel; sidebar?: ReactNode }) {
  useLocale()
  const item = NAV_ITEMS.find((n) => n.id === panel)
  const title = item ? item.label() : panel
  const render = PAGES[panel]
  return (
    <AppShell sidebar={sidebar ?? <div className="panel-view active flex min-h-0 flex-1 flex-col"><PanelHead title={title} /></div>}>
      {render ? render() : (
        <HubPage title={title}>
          <p className="text-sm text-muted">{m.loading()}</p>
        </HubPage>
      )}
    </AppShell>
  )
}
