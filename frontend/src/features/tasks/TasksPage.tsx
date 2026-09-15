import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from '@tanstack/react-form'
import { z } from 'zod'
import { Pause, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import type { z as Z } from 'zod'
import { CronJobSchema } from '../../contracts'
import { HubPage } from '../../shell/AppShell'
import { Button } from '../../ui/Button'
import { PanelHeadButton } from '../../shell/Sidebar'
import { Switch, FieldRow, TextInput } from '../../ui/Field'
import { Select } from '../../ui/Select'
import { ConfirmDialog, Dialog } from '../../ui/Dialog'
import { EmptyState, ErrorState, LoadingState, formatDate } from '../../ui/States'
import { showToast } from '../toast/toast'
import { cn } from '../../ui/cn'
import { useProfilesQuery } from '../../app/queries'

type CronJob = Z.infer<typeof CronJobSchema>

const JobForm = z.object({
  name: z.string().trim().max(200),
  schedule: z.string().trim().min(1),
  prompt: z.string(),
  script: z.string().trim(),
  no_agent: z.boolean(),
  deliver: z.string().trim(),
  repeat: z.string().trim(),
  profile: z.string().trim(),
  model: z.string().trim(),
  toast_notifications: z.boolean(),
})
type JobFormValues = z.infer<typeof JobForm>

function jobId(job: CronJob): string {
  return job.id ?? job.job_id ?? ''
}

function statusOf(job: CronJob): { label: string; tone: string } {
  if (job.running) return { label: m.cron_status_running(), tone: 'text-accent-text' }
  if (job.paused || job.enabled === false) return { label: m.cron_status_paused(), tone: 'text-muted' }
  if (job.status === 'error') return { label: m.cron_status_error(), tone: 'text-error' }
  if (!job.next_run) return { label: m.cron_status_needs_attention(), tone: 'text-warning' }
  return { label: m.cron_status_active(), tone: 'text-success' }
}

export function TasksPage() {
  const qc = useQueryClient()
  const [allProfiles, setAllProfiles] = useState(false)
  const crons = useQuery({ queryKey: [...keys.crons.all, allProfiles], queryFn: () => api.fetchCrons(allProfiles), staleTime: 15_000 })
  const [editing, setEditing] = useState<CronJob | null | 'new'>(null)
  const [confirmDelete, setConfirmDelete] = useState<CronJob | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const invalidate = () => qc.invalidateQueries({ queryKey: keys.crons.all })
  const action = useMutation({
    mutationFn: ({ action, body }: { action: Parameters<typeof api.cronAction>[0]; body: Record<string, unknown> }) => api.cronAction(action, body),
    onSuccess: (res, vars) => {
      if (res.error) showToast(res.error, 4000, 'error')
      else showToast(vars.action === 'run' ? m.cron_run_now() : m.saved())
      void invalidate()
    },
    onError: (e) => showToast(e instanceof Error ? e.message : String(e), 4000, 'error'),
  })
  const jobs = useMemo(() => crons.data?.jobs ?? [], [crons.data])
  const selectedJob = useMemo(() => jobs.find((j) => jobId(j) === selected) ?? null, [jobs, selected])

  return (
    <HubPage
      title={m.tab_tasks()}
      actions={
        <>
          <label className="flex items-center gap-1.5 text-xs text-muted"><Switch checked={allProfiles} onCheckedChange={(checked) => setAllProfiles(checked)} /> {m.all_profiles()}</label>
          <PanelHeadButton label={m.refresh()} onClick={() => { void crons.refetch() }}><RefreshCw size={16} aria-hidden="true" /></PanelHeadButton>
          <PanelHeadButton label={m.cron_new_job()} className="primary" onClick={() => setEditing('new')}><Plus size={16} aria-hidden="true" /></PanelHeadButton>
        </>
      }
    >
      {crons.data?.cron_unavailable && <div className="mb-3 rounded-lg border border-warning px-3 py-2 text-sm text-warning" role="status">{m.cron_gateway_unavailable()}</div>}
      {crons.isPending && <LoadingState />}
      {crons.isError && <ErrorState error={crons.error} onRetry={() => { void crons.refetch() }} />}
      {crons.isSuccess && jobs.length === 0 && <EmptyState>{m.cron_no_jobs()}</EmptyState>}
      <div className="cron-list flex flex-col gap-2" id="cronList">
        {jobs.map((job) => {
          const id = jobId(job)
          const st = statusOf(job)
          const open = selected === id
          return (
            <article key={id} className={cn('cron-card rounded-lg border border-border bg-surface p-3', open && 'border-accent-bg-strong')} data-job-id={id}>
              <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => setSelected(open ? null : id)} aria-expanded={open}>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-text">{job.name ? job.name : m.untitled()}</div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-muted">{typeof job.schedule === 'string' ? job.schedule : job.schedule_display ?? ''}</div>
                </div>
                <div className={cn('shrink-0 text-xs', st.tone)}>{st.label}</div>
              </button>
              {open && (
                <div className="mt-3 flex flex-col gap-2 border-t border-border-subtle pt-3 text-sm">
                  {job.prompt && <pre className="whitespace-pre-wrap font-sans text-[13px] text-text">{job.prompt}</pre>}
                  <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted">
                    {job.next_run !== undefined && job.next_run !== null && <span>{m.cron_next()}: {formatDate(job.next_run as number | string)}</span>}
                    {job.last_run !== undefined && job.last_run !== null && <span>{m.cron_last()}: {formatDate(job.last_run as number | string)}</span>}
                    {job.profile && <span>{m.tab_profiles()}: {job.profile}</span>}
                  </div>
                  <JobOutput jobId={id} />
                  <div className="flex flex-wrap gap-2 pt-1">
                    <Button size="sm" onClick={() => action.mutate({ action: 'run', body: { job_id: id } })}><Play size={12} aria-hidden="true" /> {m.cron_run_now()}</Button>
                    {job.paused || job.enabled === false
                      ? <Button size="sm" onClick={() => action.mutate({ action: 'resume', body: { job_id: id } })}><Play size={12} aria-hidden="true" /> {m.cron_resume()}</Button>
                      : <Button size="sm" onClick={() => action.mutate({ action: 'pause', body: { job_id: id } })}><Pause size={12} aria-hidden="true" /> {m.cron_pause()}</Button>}
                    <Button size="sm" onClick={() => setEditing(job)}>{m.edit()}</Button>
                    <Button size="sm" variant="ghost" className="text-error" onClick={() => setConfirmDelete(job)}><Trash2 size={12} aria-hidden="true" /> {m.delete()}</Button>
                  </div>
                </div>
              )}
            </article>
          )
        })}
      </div>
      {editing !== null && (
        <JobDialog
          job={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); void invalidate() }}
        />
      )}
      <ConfirmDialog
        open={confirmDelete !== null}
        onOpenChange={(o) => { if (!o) setConfirmDelete(null) }}
        title={m.cron_delete_confirm_title()}
        description={confirmDelete?.name ?? ''}
        confirmLabel={m.delete()}
        cancelLabel={m.cancel()}
        danger
        onConfirm={() => { if (confirmDelete) action.mutate({ action: 'delete', body: { job_id: jobId(confirmDelete) } }); if (selectedJob && confirmDelete && jobId(selectedJob) === jobId(confirmDelete)) setSelected(null) }}
      />
    </HubPage>
  )
}

function JobOutput({ jobId }: { jobId: string }) {
  const out = useQuery({ queryKey: keys.crons.output(jobId), queryFn: () => api.fetchCronOutput(jobId), staleTime: 30_000 })
  if (out.isPending) return <LoadingState />
  if (out.isError) return null
  if (!out.data.output) return null
  return (
    <details className="rounded-md border border-border-subtle bg-code-bg p-2">
      <summary className="cursor-pointer text-xs text-muted">{m.cron_last_output()}</summary>
      <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap font-mono text-[12px] text-pre-text">{out.data.output}</pre>
    </details>
  )
}

function JobDialog({ job, onClose, onSaved }: { job: CronJob | null; onClose: () => void; onSaved: () => void }) {
  const profiles = useProfilesQuery()
  const [error, setError] = useState<string | null>(null)
  const form = useForm({
    defaultValues: {
      name: job?.name ?? '',
      schedule: typeof job?.schedule === 'string' ? job.schedule : '',
      prompt: job?.prompt ?? '',
      script: (job as { script?: string } | null)?.script ?? '',
      no_agent: !!(job as { no_agent?: boolean } | null)?.no_agent,
      deliver: (job as { deliver?: string } | null)?.deliver ?? '',
      repeat: (job as { repeat?: number | string } | null)?.repeat === undefined || (job as { repeat?: number | string } | null)?.repeat === null ? '' : String((job as { repeat?: number | string }).repeat),
      profile: job?.profile ?? '',
      model: job?.model ?? '',
      toast_notifications: (job as { toast_notifications?: boolean } | null)?.toast_notifications !== false,
    } satisfies JobFormValues,
    onSubmit: async ({ value }) => {
      setError(null)
      const parsed = JobForm.safeParse(value)
      if (!parsed.success) { setError(m.cron_schedule_required()); return }
      if (parsed.data.no_agent && !parsed.data.script) { setError(m.cron_no_agent_script_required()); return }
      if (parsed.data.repeat && !/^\d+$/.test(parsed.data.repeat)) { setError(m.cron_repeat_invalid()); return }
      const body: Record<string, unknown> = {
        name: parsed.data.name, schedule: parsed.data.schedule, prompt: parsed.data.prompt, script: parsed.data.script || undefined,
        no_agent: parsed.data.no_agent, deliver: parsed.data.deliver || undefined, repeat: parsed.data.repeat ? Number(parsed.data.repeat) : undefined,
        profile: parsed.data.profile || undefined, model: parsed.data.model || undefined, toast_notifications: parsed.data.toast_notifications,
      }
      try {
        const res = job ? await api.cronAction('update', { ...body, job_id: jobId(job) }) : await api.cronAction('create', body)
        if (res.error) { setError(res.error); return }
        showToast(m.saved())
        onSaved()
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
  })
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }} title={job ? m.cron_edit_job() : m.cron_new_job()} className="w-[min(92vw,640px)]">
      <form onSubmit={(e) => { e.preventDefault(); void form.handleSubmit() }} className="flex flex-col gap-1">
        <form.Field name="name">{(f) => <FieldRow label={m.cron_job_name_placeholder()} htmlFor="cronName"><TextInput id="cronName" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} /></FieldRow>}</form.Field>
        <form.Field name="schedule">{(f) => <FieldRow label={m.cron_schedule_placeholder()} htmlFor="cronSchedule" hint="*/30 * * * * · every 2h · daily at 09:00"><TextInput id="cronSchedule" required value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} /></FieldRow>}</form.Field>
        <form.Field name="no_agent">{(f) => <FieldRow label={m.cron_no_agent_label()} hint={m.cron_no_agent_hint()} htmlFor="cronNoAgent" inline><Switch id="cronNoAgent" checked={f.state.value} onCheckedChange={(checked) => f.handleChange(checked)} /></FieldRow>}</form.Field>
        <form.Field name="prompt">{(f) => <FieldRow label={m.cron_prompt_placeholder()} htmlFor="cronPrompt"><textarea id="cronPrompt" rows={4} value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} className="w-full rounded-md border border-border bg-input px-3 py-2 text-sm text-text" /></FieldRow>}</form.Field>
        <form.Field name="script">{(f) => <FieldRow label={m.cron_script_path_label()} hint={m.cron_script_path_hint()} htmlFor="cronScript"><TextInput id="cronScript" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_script_path_placeholder()} /></FieldRow>}</form.Field>
        <form.Field name="deliver">{(f) => <FieldRow label={m.cron_deliver_label()} htmlFor="cronDeliver"><TextInput id="cronDeliver" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} /></FieldRow>}</form.Field>
        <div className="grid grid-cols-2 gap-3">
          <form.Field name="repeat">{(f) => <FieldRow label={m.cron_repeat_label()} htmlFor="cronRepeat"><TextInput id="cronRepeat" inputMode="numeric" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_repeat_placeholder()} /></FieldRow>}</form.Field>
          <form.Field name="profile">{(f) => (
            <FieldRow label={m.tab_profiles()} htmlFor="cronProfile">
              <Select id="cronProfile" value={f.state.value} onValueChange={(v) => f.handleChange(v)} className="w-full">
                <option value="">{m.cron_profile_default()}</option>
                {(profiles.data?.profiles ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
              </Select>
            </FieldRow>
          )}</form.Field>
        </div>
        <form.Field name="model">{(f) => <FieldRow label={m.settings_label_model()} htmlFor="cronModel"><TextInput id="cronModel" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.model_custom_placeholder()} /></FieldRow>}</form.Field>
        <form.Field name="toast_notifications">{(f) => <FieldRow label={m.cron_toast_label()} htmlFor="cronToast" inline><Switch id="cronToast" checked={f.state.value} onCheckedChange={(checked) => f.handleChange(checked)} /></FieldRow>}</form.Field>
        {error && <div role="alert" className="text-sm text-error">{error}</div>}
        <div className="mt-3 flex justify-end gap-2">
          <Button onClick={onClose}>{m.cancel()}</Button>
          <Button variant="primary" type="submit">{m.save()}</Button>
        </div>
      </form>
    </Dialog>
  )
}
