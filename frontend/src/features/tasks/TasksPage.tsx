import { useMemo, useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useForm } from '@tanstack/react-form'
import { Copy, Pause, Play, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import * as api from '../../api/endpoints'
import { keys } from '../../api/queryKeys'
import type { CronJob } from '../../contracts'
import { HubPage } from '../../shell/AppShell'
import { Button } from '../../ui/Button'
import { PanelHeadButton } from '../../shell/Sidebar'
import { Switch, FieldRow, TextInput } from '../../ui/Field'
import { Select } from '../../ui/Select'
import { ConfirmDialog, Dialog } from '../../ui/Dialog'
import { EmptyState, ErrorState, LoadingState, formatBytes, formatDate } from '../../ui/States'
import { showToast } from '../toast/toast'
import { cn } from '../../ui/cn'
import { useModelsQuery, useProfilesQuery } from '../../app/queries'
import { contextFromList, cronDiagnostics, cronState, jobId, modelOptionFor, modelOptionValue, needsAttention, runningIds, scheduleText, splitModelOption, usageStrip, type CronState } from './cronJob'

type CronAction = Parameters<typeof api.cronAction>[0]
interface Editor { mode: 'create' | 'edit' | 'duplicate'; job: CronJob | null }

// Canonical Hermes reasoning levels; cron.jobs validates the same grammar.
const REASONING_EFFORTS = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra']

function statusLabel(state: CronState): { label: string; tone: string } {
  switch (state) {
    case 'running': return { label: m.cron_status_running(), tone: 'text-accent-text' }
    case 'needs_attention': case 'schedule_error': return { label: m.cron_status_needs_attention(), tone: 'text-warning' }
    case 'paused': return { label: m.cron_status_paused(), tone: 'text-muted' }
    case 'off': return { label: m.cron_status_off(), tone: 'text-muted' }
    case 'error': return { label: m.cron_status_error(), tone: 'text-error' }
    default: return { label: m.cron_status_active(), tone: 'text-success' }
  }
}

export function TasksPage() {
  const qc = useQueryClient()
  const [allProfiles, setAllProfiles] = useState(false)
  const crons = useQuery({ queryKey: keys.crons.list(allProfiles), queryFn: () => api.fetchCrons(allProfiles), staleTime: 15_000 })
  const status = useQuery({ queryKey: keys.crons.status, queryFn: api.fetchCronStatus, refetchInterval: 10_000, staleTime: 5_000 })
  const [editor, setEditor] = useState<Editor | null>(null)
  const [confirmDelete, setConfirmDelete] = useState<CronJob | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const invalidate = () => qc.invalidateQueries({ queryKey: keys.crons.all })
  const action = useMutation({
    mutationFn: ({ action, body }: { action: CronAction; body: Record<string, unknown> }) => api.cronAction(action, body),
    onSuccess: (res, vars) => {
      if (res.error) showToast(res.error, 4000, 'error')
      else showToast(vars.action === 'run' ? m.cron_job_triggered() : vars.action === 'pause' ? m.cron_job_paused() : vars.action === 'resume' ? m.cron_job_resumed() : vars.action === 'delete' ? m.cron_job_deleted() : m.saved())
      void invalidate()
    },
    onError: (e) => showToast(e instanceof Error ? e.message : String(e), 4000, 'error'),
  })
  const jobs = useMemo(() => crons.data?.jobs ?? [], [crons.data])
  const running = useMemo(() => runningIds(status.data), [status.data])
  const run = (act: CronAction, job: CronJob) => action.mutate({ action: act, body: { job_id: jobId(job) } })

  return (
    <HubPage
      title={m.tab_tasks()}
      actions={
        <>
          <label className="flex items-center gap-1.5 text-xs text-muted"><Switch checked={allProfiles} onCheckedChange={(checked) => setAllProfiles(checked)} /> {m.all_profiles()}</label>
          <PanelHeadButton label={m.refresh()} onClick={() => { void crons.refetch() }}><RefreshCw size={16} aria-hidden="true" /></PanelHeadButton>
          <PanelHeadButton label={m.cron_new_job()} className="primary" onClick={() => setEditor({ mode: 'create', job: null })}><Plus size={16} aria-hidden="true" /></PanelHeadButton>
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
          const state = cronState(job, running.has(id))
          const st = statusLabel(state)
          const open = selected === id
          return (
            <article key={id} className={cn('cron-card rounded-lg border border-border bg-surface p-3', open && 'border-accent-bg-strong')} data-job-id={id} data-state={state}>
              <button type="button" className="flex w-full items-start justify-between gap-3 text-left" onClick={() => setSelected(open ? null : id)} aria-expanded={open}>
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium text-text">{job.name ? job.name : m.untitled()}</div>
                  <div className="mt-0.5 truncate font-mono text-[11px] text-muted">{scheduleText(job)}</div>
                </div>
                <div className={cn('shrink-0 text-xs', st.tone)}>{st.label}</div>
              </button>
              {open && (
                <JobDetail
                  job={job}
                  state={state}
                  jobs={jobs}
                  onAction={(act) => run(act, job)}
                  onEdit={() => setEditor({ mode: 'edit', job })}
                  onDuplicate={() => setEditor({ mode: 'duplicate', job })}
                  onDelete={() => setConfirmDelete(job)}
                />
              )}
            </article>
          )
        })}
      </div>
      {editor && (
        <JobDialog
          mode={editor.mode}
          job={editor.job}
          jobs={jobs}
          onClose={() => setEditor(null)}
          onSaved={(id) => { setEditor(null); if (id) setSelected(id); void invalidate() }}
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
        onConfirm={() => { if (confirmDelete) { run('delete', confirmDelete); if (selected === jobId(confirmDelete)) setSelected(null) } }}
      />
    </HubPage>
  )
}

function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex gap-3 text-xs">
      <dt className="w-32 shrink-0 text-muted">{label}</dt>
      <dd className="min-w-0 break-words text-text">{children}</dd>
    </div>
  )
}

function JobDetail({ job, state, jobs, onAction, onEdit, onDuplicate, onDelete }: {
  job: CronJob; state: CronState; jobs: CronJob[]; onAction: (action: CronAction) => void; onEdit: () => void; onDuplicate: () => void; onDelete: () => void
}) {
  const id = jobId(job)
  const readOnly = !!job.read_only
  const attention = needsAttention(state)
  const resumable = attention || state === 'paused' || state === 'off'
  const isScript = !!job.no_agent
  const ownerProfile = (job.owner_profile ?? job.profile ?? '').trim()
  const profileLabel = (job.profile ?? '').trim() || m.cron_profile_server_default()
  const model = job.provider && job.model ? `${job.provider}/${job.model}` : job.model ?? job.provider ?? (isScript ? '' : m.cron_model_use_default())
  const contextFrom = contextFromList(job).map((ref) => jobs.find((j) => jobId(j) === ref)?.name || ref)
  const copyDiagnostics = () => {
    void navigator.clipboard.writeText(cronDiagnostics(job)).then(() => showToast(m.cron_diagnostics_copied()), () => showToast(m.copy_failed(), 3000, 'error'))
  }
  return (
    <div className="mt-3 flex flex-col gap-3 border-t border-border-subtle pt-3 text-sm" data-testid="cron-detail">
      {readOnly ? (
        <div className="text-xs text-muted" role="note">{m.cron_read_only_profile({ profile: ownerProfile })}</div>
      ) : (
        <div className="flex flex-wrap gap-2" role="group" aria-label={job.name ?? id}>
          <Button size="sm" onClick={() => onAction('run')}><Play size={12} aria-hidden="true" /> {m.cron_run_now()}</Button>
          {resumable
            ? <Button size="sm" onClick={() => onAction('resume')}><Play size={12} aria-hidden="true" /> {m.cron_resume()}</Button>
            : <Button size="sm" onClick={() => onAction('pause')}><Pause size={12} aria-hidden="true" /> {m.cron_pause()}</Button>}
          <Button size="sm" onClick={onEdit}>{m.edit()}</Button>
          <Button size="sm" onClick={onDuplicate}><Copy size={12} aria-hidden="true" /> {m.cron_duplicate()}</Button>
          <Button size="sm" variant="ghost" className="text-error" onClick={onDelete}><Trash2 size={12} aria-hidden="true" /> {m.delete()}</Button>
        </div>
      )}
      {!readOnly && attention && (
        <div className="rounded-lg border border-warning px-3 py-2" role="alert">
          <div className="text-sm font-medium text-warning">{m.cron_status_needs_attention()}</div>
          <p className="mt-1 text-xs text-text">{m.cron_attention_desc()}</p>
          {/croniter/i.test(job.last_error ?? '') && <p className="mt-1 text-xs text-text">{m.cron_attention_croniter_hint()}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => onAction('resume')}>{m.cron_attention_resume()}</Button>
            <Button size="sm" onClick={() => onAction('run')}>{m.cron_attention_run_once()}</Button>
            <Button size="sm" onClick={copyDiagnostics}>{m.cron_attention_copy_diagnostics()}</Button>
          </div>
        </div>
      )}
      <dl className="flex flex-col gap-1">
        <Row label={m.cron_status_label()}><span className={statusLabel(state).tone}>{statusLabel(state).label}</span></Row>
        {job.paused_reason && <Row label={m.cron_paused_reason_label()}>{job.paused_reason}</Row>}
        {job.last_error && <Row label={m.cron_last_error_label()}><span className="text-error">{job.last_error}</span></Row>}
        {job.last_delivery_error && <Row label={m.cron_delivery_error_label()}><span className="text-error">{job.last_delivery_error}</span></Row>}
        <Row label={m.cron_schedule_preset_label()}><code>{scheduleText(job)}</code></Row>
        <Row label={m.cron_next()}>{job.next_run_at ? formatDate(job.next_run_at) : m.not_available()}</Row>
        <Row label={m.cron_last()}>{job.last_run_at ? formatDate(job.last_run_at) : m.never()}</Row>
        <Row label={m.cron_deliver_label()}>{job.deliver || 'local'}</Row>
        <Row label={m.cron_mode_label()}>{isScript ? m.cron_mode_script() : m.cron_mode_agent()}{model && <> · <code>{model}</code></>}</Row>
        <Row label={m.cron_profile_label()}>{profileLabel}</Row>
        {ownerProfile && (readOnly || ownerProfile !== (job.profile ?? '').trim()) && <Row label={m.cron_owner_profile_label()}>{ownerProfile}</Row>}
        {!isScript && <Row label={m.cron_skills_label()}>{job.skills?.length ? job.skills.join(', ') : '—'}</Row>}
        {job.workdir && <Row label={m.cron_workdir_label()}><code>{job.workdir}</code></Row>}
        {job.monitor && <Row label={m.cron_monitor_label()}><code>{job.monitor}</code></Row>}
        {job.continuity && <Row label={m.cron_continuity_label()}>{m.cron_toast_notifications_enabled()}</Row>}
        {contextFrom.length > 0 && <Row label={m.cron_context_from_label()}>{contextFrom.join(', ')}</Row>}
        {job.reasoning_effort && <Row label={m.cron_reasoning_effort_label()}>{job.reasoning_effort}</Row>}
        <Row label={m.cron_toast_notifications_label()}>{job.toast_notifications === false ? m.cron_toast_notifications_disabled() : m.cron_toast_notifications_enabled()}</Row>
      </dl>
      {isScript
        ? <div><div className="text-xs text-muted">{m.cron_script_card_title()}</div><code className="text-[13px]">{job.script || '—'}</code><div className="mt-1 text-[11px] text-muted">{m.cron_script_path_hint()}</div></div>
        : job.prompt && <div><div className="text-xs text-muted">{m.cron_prompt_label()}</div><pre className="max-h-48 overflow-auto whitespace-pre-wrap font-sans text-[13px] text-text">{job.prompt}</pre></div>}
      {!readOnly && <RunHistory jobId={id} isScript={isScript} />}
    </div>
  )
}

function RunHistory({ jobId: id, isScript }: { jobId: string; isScript: boolean }) {
  const history = useQuery({ queryKey: keys.crons.history(id), queryFn: () => api.fetchCronHistory(id), staleTime: 15_000 })
  const [openRun, setOpenRun] = useState<string | null>(null)
  const title = isScript ? m.cron_script_output() : m.cron_last_output()
  if (history.isPending) return <LoadingState />
  if (history.isError) return <ErrorState error={history.error} onRetry={() => { void history.refetch() }} />
  const runs = history.data.runs
  const total = history.data.total ?? runs.length
  return (
    <section aria-label={title}>
      <div className="text-xs text-muted">{title} {runs.length > 0 && `(${total > runs.length ? m.cron_runs_showing({ total, shown: runs.length }) : m.cron_runs_count({ total })})`}</div>
      {runs.length === 0 && <div className="mt-1 text-xs text-muted">{m.cron_no_runs_yet()}</div>}
      <ul className="mt-1 flex flex-col divide-y divide-border-subtle">
        {runs.map((run) => {
          const open = openRun === run.filename
          const usage = isScript ? '' : usageStrip(run.usage)
          return (
            <li key={run.filename}>
              <button type="button" className="flex w-full items-center justify-between gap-3 py-1.5 text-left text-xs hover:text-text" aria-expanded={open} onClick={() => setOpenRun(open ? null : run.filename)} title={run.filename}>
                <span className="text-text">{formatDate(run.modified)}</span>
                <span className="truncate text-muted">{usage}</span>
                <span className="shrink-0 text-muted">{formatBytes(run.size)}</span>
              </button>
              {open && <RunBody jobId={id} filename={run.filename} />}
            </li>
          )
        })}
      </ul>
    </section>
  )
}

function RunBody({ jobId: id, filename }: { jobId: string; filename: string }) {
  const run = useQuery({ queryKey: keys.crons.run(id, filename), queryFn: () => api.fetchCronRun(id, filename), staleTime: Infinity })
  if (run.isPending) return <LoadingState />
  if (run.isError || run.data.error) return <div className="pb-2 text-xs text-error" role="alert">{m.cron_run_load_failed()} {run.isError ? (run.error instanceof Error ? run.error.message : String(run.error)) : run.data.error}</div>
  const usage = usageStrip(run.data.usage)
  return (
    <div className="pb-2">
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded-md border border-border-subtle bg-code-bg p-2 font-mono text-[12px] text-pre-text">{run.data.content ?? run.data.snippet ?? ''}</pre>
      {usage && <div className="mt-1 text-[11px] text-muted">{usage}</div>}
    </div>
  )
}

interface FormValues {
  name: string; schedule: string; prompt: string; script: string; no_agent: boolean; deliver: string; repeat: string; profile: string
  model: string; toast_notifications: boolean; skills: string; monitor: string; continuity: boolean; context_from: string[]; reasoning_effort: string
}

function JobDialog({ mode, job, jobs, onClose, onSaved }: { mode: Editor['mode']; job: CronJob | null; jobs: CronJob[]; onClose: () => void; onSaved: (id?: string) => void }) {
  const isEdit = mode === 'edit'
  const profiles = useProfilesQuery()
  const models = useModelsQuery()
  const delivery = useQuery({ queryKey: keys.crons.deliveryOptions, queryFn: api.fetchCronDeliveryOptions, staleTime: 60_000 })
  const skills = useQuery({ queryKey: keys.skills.all, queryFn: () => api.fetchSkills(), staleTime: 60_000, enabled: !isEdit })
  const [error, setError] = useState<string | null>(null)
  const sourceId = job ? jobId(job) : ''
  const chainable = jobs.filter((j) => !j.read_only && jobId(j) && jobId(j) !== sourceId)
  const providerOf = (id: string): string | null => { for (const g of models.data?.groups ?? []) if (g.models.some((mm) => mm.id === id)) return g.provider_id ?? g.provider; return null }
  const knownModels = useMemo(() => new Set((models.data?.groups ?? []).flatMap((g) => g.models.map((mm) => mm.id))), [models.data])
  const copyName = (name: string) => {
    const taken = new Set(jobs.map((j) => j.name))
    let candidate = `${name} ${m.cron_copy_suffix()}`
    for (let n = 2; taken.has(candidate); n++) candidate = `${name} ${m.cron_copy_suffix_n({ n })}`
    return candidate
  }
  const repeatTimes = job?.repeat && typeof job.repeat === 'object' ? job.repeat.times : typeof job?.repeat === 'number' ? job.repeat : null
  const form = useForm({
    defaultValues: {
      name: mode === 'duplicate' ? copyName(job?.name ?? '') : job?.name ?? '',
      schedule: job ? scheduleText(job) : '',
      prompt: job?.prompt ?? '',
      script: job?.script ?? '',
      no_agent: !!job?.no_agent,
      deliver: job?.deliver ?? 'local',
      repeat: mode === 'duplicate' && repeatTimes != null ? String(repeatTimes) : '',
      profile: job?.profile ?? '',
      model: modelOptionValue(job?.model, job?.provider),
      toast_notifications: job?.toast_notifications !== false,
      skills: job?.skills?.join(', ') ?? '',
      monitor: job?.monitor ?? '',
      continuity: !!job?.continuity,
      context_from: job ? contextFromList(job) : [],
      reasoning_effort: job?.reasoning_effort ?? '',
    } satisfies FormValues,
    onSubmit: async ({ value }) => {
      setError(null)
      const v = { ...value, name: value.name.trim(), schedule: value.schedule.trim(), prompt: value.prompt.trim(), script: value.script.trim(), monitor: value.monitor.trim(), repeat: value.repeat.trim() }
      if (!v.schedule) { setError(m.cron_schedule_required_example()); return }
      if (!v.no_agent && !v.prompt) { setError(m.cron_prompt_required()); return }
      if (v.no_agent && !v.script) { setError(m.cron_no_agent_script_required()); return }
      if (v.no_agent && v.monitor) { setError(m.cron_monitor_no_agent_conflict()); return }
      if (v.repeat && !(/^\d+$/.test(v.repeat) && Number(v.repeat) >= 1)) { setError(m.cron_repeat_invalid()); return }
      const { model, provider } = splitModelOption(v.model, providerOf)
      try {
        let res
        if (isEdit && job) {
          // Every field is sent so the agent's documented clearing semantics stay
          // reachable ('' clears script/monitor, [] clears context_from, false
          // turns continuity off, null clears the model pin). `repeat` and
          // `skills` are create-only in the store.
          const body: Record<string, unknown> = {
            job_id: sourceId, schedule: v.schedule, deliver: v.deliver || 'local', profile: v.profile, toast_notifications: v.toast_notifications,
            script: v.script, no_agent: v.no_agent, monitor: v.monitor, continuity: v.continuity, context_from: v.context_from,
            reasoning_effort: v.reasoning_effort, model, provider,
          }
          if (!v.no_agent) body.prompt = v.prompt
          if (v.name) body.name = v.name
          res = await api.cronAction('update', body)
        } else {
          // Omitted when unset so agent-side defaults still apply.
          const body: Record<string, unknown> = { schedule: v.schedule, prompt: v.prompt, deliver: v.deliver || 'local', profile: v.profile, toast_notifications: v.toast_notifications }
          if (v.name) body.name = v.name
          const skillList = v.skills.split(',').map((s) => s.trim()).filter(Boolean)
          if (skillList.length) body.skills = skillList
          if (v.script) body.script = v.script
          if (v.no_agent) body.no_agent = true
          if (v.monitor) body.monitor = v.monitor
          if (v.continuity) body.continuity = true
          if (v.context_from.length) body.context_from = v.context_from
          if (v.reasoning_effort) body.reasoning_effort = v.reasoning_effort
          if (v.repeat) body.repeat = Number(v.repeat)
          if (model) { body.model = model; body.provider = provider }
          res = await api.cronAction('create', body)
        }
        if (res.error) { setError(res.error); return }
        showToast(isEdit ? m.cron_job_updated() : m.cron_job_created())
        onSaved(res.job ? jobId(res.job) : res.job_id)
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
      }
    },
  })
  const title = isEdit ? m.cron_edit_job() : mode === 'duplicate' ? m.cron_duplicate_job() : m.cron_new_job()
  const platforms = (delivery.data?.platforms ?? []) as { value?: string; label?: string }[]
  const textarea = 'w-full rounded-md border border-border bg-input px-3 py-2 text-sm text-text'
  return (
    <Dialog open onOpenChange={(o) => { if (!o) onClose() }} title={title} className="w-[min(92vw,640px)] max-h-[92vh] overflow-y-auto">
      <form onSubmit={(e) => { e.preventDefault(); void form.handleSubmit() }} className="flex flex-col gap-1" aria-label={title}>
        <form.Field name="name">{(f) => <FieldRow label={m.cron_name_label()} htmlFor="cronName"><TextInput id="cronName" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_name_placeholder()} /></FieldRow>}</form.Field>
        <form.Field name="schedule">{(f) => <FieldRow label={m.cron_schedule_preset_label()} htmlFor="cronSchedule" hint={m.cron_schedule_hint()}><TextInput id="cronSchedule" required value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder="0 9 * * *" /></FieldRow>}</form.Field>
        <form.Field name="no_agent">{(f) => <FieldRow label={m.cron_no_agent_label()} hint={m.cron_no_agent_hint()} htmlFor="cronNoAgent" inline><Switch id="cronNoAgent" checked={f.state.value} onCheckedChange={(checked) => f.handleChange(checked)} /></FieldRow>}</form.Field>
        <form.Subscribe selector={(s) => s.values.no_agent}>{(noAgent) => (
          <>
            {!noAgent && <form.Field name="prompt">{(f) => <FieldRow label={m.cron_prompt_label()} htmlFor="cronPrompt"><textarea id="cronPrompt" rows={4} value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} className={textarea} /></FieldRow>}</form.Field>}
            <form.Field name="script">{(f) => <FieldRow label={m.cron_script_path_label()} hint={noAgent ? m.cron_script_path_hint() : m.cron_script_context_hint()} htmlFor="cronScript"><TextInput id="cronScript" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_script_path_placeholder()} /></FieldRow>}</form.Field>
            <form.Field name="deliver">{(f) => (
              <FieldRow label={m.cron_deliver_label()} htmlFor="cronDeliver">
                <Select id="cronDeliver" value={f.state.value} onValueChange={(v) => f.handleChange(v)} className="w-full">
                  {platforms.length === 0 && <option value="local">{m.cron_deliver_local()}</option>}
                  {platforms.map((p) => <option key={p.value} value={p.value ?? ''}>{p.label ?? p.value}</option>)}
                  {f.state.value && !platforms.some((p) => p.value === f.state.value) && f.state.value !== 'local' && <option value={f.state.value}>{f.state.value}</option>}
                </Select>
              </FieldRow>
            )}</form.Field>
            <div className="grid grid-cols-2 gap-3 max-[480px]:grid-cols-1">
              <form.Field name="profile">{(f) => (
                <FieldRow label={m.cron_profile_label()} htmlFor="cronProfile">
                  <Select id="cronProfile" value={f.state.value} onValueChange={(v) => f.handleChange(v)} className="w-full">
                    <option value="">{m.cron_profile_default()}</option>
                    {(profiles.data?.profiles ?? []).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
                  </Select>
                </FieldRow>
              )}</form.Field>
              <form.Field name="model">{(f) => (
                <FieldRow label={m.cron_model_label()} hint={noAgent ? m.cron_model_no_agent_hint() : undefined} htmlFor="cronModel">
                  <Select id="cronModel" value={modelOptionFor(f.state.value, knownModels)} onValueChange={(v) => f.handleChange(v)} className="w-full" disabled={noAgent}>
                    <option value="">{m.cron_model_use_default()}</option>
                    {(models.data?.groups ?? []).map((g) => (
                      <optgroup key={g.provider} label={g.provider}>
                        {g.models.map((mm) => <option key={mm.id} value={mm.id}>{mm.label ?? mm.id}</option>)}
                      </optgroup>
                    ))}
                    {f.state.value && !knownModels.has(modelOptionFor(f.state.value, knownModels)) && <option value={f.state.value}>{f.state.value}</option>}
                  </Select>
                </FieldRow>
              )}</form.Field>
            </div>
            {!noAgent && (isEdit
              ? <FieldRow label={m.cron_skills_label()} hint={m.cron_skills_edit_hint()}><div className="text-sm text-muted">{job?.skills?.length ? job.skills.join(', ') : '—'}</div></FieldRow>
              : <form.Field name="skills">{(f) => (
                <FieldRow label={m.cron_skills_label()} htmlFor="cronSkills">
                  <TextInput id="cronSkills" list="cronSkillNames" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_skills_placeholder()} />
                  <datalist id="cronSkillNames">{(skills.data?.skills ?? []).map((s) => <option key={s.name} value={s.name} />)}</datalist>
                </FieldRow>
              )}</form.Field>)}
            <details className="mt-2" open={!!(job && (job.monitor || job.continuity || job.reasoning_effort || contextFromList(job).length))}>
              <summary className="cursor-pointer text-sm text-text">{m.cron_advanced_label()}</summary>
              <form.Field name="monitor">{(f) => <FieldRow label={m.cron_monitor_label()} hint={noAgent ? m.cron_monitor_no_agent_hint() : m.cron_monitor_hint()} htmlFor="cronMonitor"><TextInput id="cronMonitor" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_monitor_placeholder()} disabled={noAgent} /></FieldRow>}</form.Field>
              <form.Field name="continuity">{(f) => <FieldRow label={m.cron_continuity_label()} hint={m.cron_continuity_hint()} htmlFor="cronContinuity" inline><Switch id="cronContinuity" checked={f.state.value} onCheckedChange={(checked) => f.handleChange(checked)} disabled={noAgent} /></FieldRow>}</form.Field>
              <form.Field name="context_from">{(f) => (
                <FieldRow label={m.cron_context_from_label()} hint={chainable.length ? m.cron_context_from_hint() : m.cron_context_from_empty_hint()}>
                  <div className="flex flex-col gap-1" role="group" aria-label={m.cron_context_from_label()}>
                    {chainable.map((j) => {
                      const cid = jobId(j)
                      const checked = f.state.value.includes(cid)
                      return (
                        <label key={cid} className="flex items-center gap-2 text-sm text-text">
                          <input type="checkbox" checked={checked} disabled={noAgent} onChange={() => f.handleChange(checked ? f.state.value.filter((x) => x !== cid) : [...f.state.value, cid])} />
                          <span className="truncate">{j.name || cid}</span>
                        </label>
                      )
                    })}
                  </div>
                </FieldRow>
              )}</form.Field>
              <form.Field name="reasoning_effort">{(f) => (
                <FieldRow label={m.cron_reasoning_effort_label()} hint={noAgent ? m.cron_reasoning_effort_no_agent_hint() : m.cron_reasoning_effort_hint()} htmlFor="cronEffort">
                  <Select id="cronEffort" value={f.state.value} onValueChange={(v) => f.handleChange(v)} className="w-full" disabled={noAgent}>
                    <option value="">{m.cron_reasoning_effort_default()}</option>
                    {REASONING_EFFORTS.map((level) => <option key={level} value={level}>{level}</option>)}
                  </Select>
                </FieldRow>
              )}</form.Field>
              {!isEdit && <form.Field name="repeat">{(f) => <FieldRow label={m.cron_repeat_label()} hint={m.cron_repeat_hint()} htmlFor="cronRepeat"><TextInput id="cronRepeat" inputMode="numeric" value={f.state.value} onChange={(e) => f.handleChange(e.target.value)} placeholder={m.cron_repeat_placeholder()} /></FieldRow>}</form.Field>}
            </details>
          </>
        )}</form.Subscribe>
        <form.Field name="toast_notifications">{(f) => <FieldRow label={m.cron_toast_notifications_label()} hint={m.cron_toast_notifications_hint()} htmlFor="cronToast" inline><Switch id="cronToast" checked={f.state.value} onCheckedChange={(checked) => f.handleChange(checked)} /></FieldRow>}</form.Field>
        {error && <div role="alert" className="text-sm text-error">{error}</div>}
        <div className="mt-3 flex justify-end gap-2">
          <Button onClick={onClose}>{m.cancel()}</Button>
          <Button variant="primary" type="submit">{m.save()}</Button>
        </div>
      </form>
    </Dialog>
  )
}
