import { AppShell, HubPage } from '../../shell/AppShell'
import { PanelHead } from '../../shell/Sidebar'
import { m } from '../../paraglide/messages.js'

/** Sandboxed extension panel host (implemented in checkpoint 7). */
export function ExtensionRoute({ extensionId }: { extensionId: string }) {
  return (
    <AppShell sidebar={<div className="panel-view active flex min-h-0 flex-1 flex-col"><PanelHead title={extensionId} /></div>}>
      <HubPage title={extensionId}>
        <p className="text-sm text-muted">{m.loading()}</p>
      </HubPage>
    </AppShell>
  )
}
