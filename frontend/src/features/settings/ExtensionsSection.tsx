import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import { EmptyState, ErrorState, LoadingState } from '../../ui/States'
import { useBootstrap } from '../../app/bootstrap'

/** Installed extensions and health from the server; gallery install and the sandboxed panel host arrive with the unified protocol (checkpoint 7). */
export function ExtensionsSection() {
  const bootstrap = useBootstrap()
  const status = useQuery({ queryKey: keys.extensions.status, queryFn: api.fetchExtensionsStatus, staleTime: 15_000 })
  if (status.isPending) return <LoadingState />
  if (status.isError) return <ErrorState error={status.error} onRetry={() => { void status.refetch() }} />
  const list = status.data.extensions ?? []
  return (
    <div className="flex flex-col gap-3" data-section="extensions">
      {!bootstrap.features.extensions && <p className="text-xs text-muted">{m.extensions_disabled_hint()}</p>}
      <h2 className="text-sm font-semibold text-text">{m.extensions_installed()}</h2>
      {list.length === 0 && <EmptyState>{m.extensions_none()}</EmptyState>}
      <ul className="flex flex-col divide-y divide-border-subtle">
        {list.map((e) => (
          <li key={e.id} className="flex items-center gap-3 py-2 text-sm">
            <div className="min-w-0 flex-1"><div className="font-medium text-text">{e.name ?? e.id}{e.version ? <span className="ml-1 text-[11px] text-muted">v{e.version}</span> : null}</div><div className="text-[11px] text-muted">{e.id}</div></div>
            <span className="text-[11px] text-muted">{e.enabled === false ? m.plugins_disabled() : m.plugins_enabled()}</span>
            <Link to="/ext/$extensionId" params={{ extensionId: e.id }} className="text-xs text-accent-text underline">{m.extensions_open()}</Link>
          </li>
        ))}
      </ul>
      {(status.data.warnings?.length ?? 0) > 0 && <ul className="text-xs text-warning">{status.data.warnings?.map((w, i) => <li key={i}>{typeof w === 'string' ? w : JSON.stringify(w)}</li>)}</ul>}
    </div>
  )
}
