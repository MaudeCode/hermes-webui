import { Moon, Monitor, Sun } from 'lucide-react'
import { m } from '../../paraglide/messages.js'
import { FieldRow, NativeSelect, Checkbox } from '../../ui/Field'
import { setFontSize, setFullWidthChat, setLanguage, setRtl, setSkin, setTheme, useAppearance } from '../../app/appearance'
import { SKINS } from '../../theme/boot'
import { FontSizeSchema, SkinSchema } from '../../contracts/persisted'
import { LOCALE_INFO } from '../../i18n/locales'
import { useLocale } from '../../i18n/useLocale'
import { useSaveSettings, useSettingsQuery } from '../../app/queries'
import { useExtensionSkins } from '../../extensions/registry'
import { cn } from '../../ui/cn'

/** Swatch colours per skin, carried forward from the legacy picker catalogue. */
const SKIN_SWATCHES: Record<string, { label: string; colors: string[] }> = {
  default: { label: 'Default', colors: ['#FFD700', '#FFBF00', '#CD7F32'] },
  ares: { label: 'Ares', colors: ['#FF4444', '#CC3333', '#992222'] },
  mono: { label: 'Mono', colors: ['#CCCCCC', '#999999', '#666666'] },
  graphite: { label: 'Graphite', colors: ['#FFFFFF', '#D6D6D6', '#242424'] },
  github: { label: 'GitHub', colors: ['#0969DA', '#1F883D', '#242424'] },
  codex: { label: 'Codex', colors: ['#72B39A', '#242624', '#ECEBE4'] },
  terracotta: { label: 'Terracotta', colors: ['#D97757', '#F0EEE6', '#141413'] },
  slate: { label: 'Slate', colors: ['#334155', '#475569', '#64748b'] },
  poseidon: { label: 'Poseidon', colors: ['#0EA5E9', '#0284C7', '#0369A1'] },
  sisyphus: { label: 'Sisyphus', colors: ['#A78BFA', '#8B5CF6', '#7C3AED'] },
  charizard: { label: 'Charizard', colors: ['#FB923C', '#F97316', '#EA580C'] },
  sienna: { label: 'Sienna', colors: ['#D97757', '#C06A49', '#9A523A'] },
  catppuccin: { label: 'Catppuccin', colors: ['#CBA6F7', '#B4BEFE', '#8839EF'] },
  hepburn: { label: 'Hepburn', colors: ['#c6246a', '#ec5597', '#f2abca'] },
  nous: { label: 'Nous', colors: ['#4682B4', '#3A6E9A', '#2C5F88'] },
  neon: { label: 'Neon', colors: ['#B347FF', '#C76BFF', '#00DDFF'] },
  'neon-soft': { label: 'Neon Soft', colors: ['#B347FF', '#C76BFF', '#00DDFF'] },
  'neon-paint': { label: 'Neon Paint', colors: ['#FF2D95', '#00E5FF', '#FFB800'] },
  'geist-contrast': { label: 'Geist Contrast', colors: ['#000000', '#ffffff', '#FFF175'] },
  zeus: { label: 'Zeus', colors: ['#FFD700', '#FFBF00', '#1A1A00'] },
  verdigris: { label: 'Verdigris', colors: ['#C89A5A', '#0F1714', '#22342C'] },
}

const PICK_PREVIEW = 'flex w-full h-10 rounded-[6px] mb-1.5 items-center justify-center'

function PickButton({ active, onClick, className, children, label, ...rest }: { active: boolean; onClick: () => void; className: string; children: React.ReactNode; label: string } & Record<`data-${string}`, string>) {
  // Skin tiles are denser than the theme and font-size tiles (legacy inline styles).
  const skin = className === 'skin-pick-btn'
  return (
    <button type="button" className={cn(className, 'flex flex-col items-center text-center cursor-pointer bg-transparent border border-border2 rounded-[10px] transition-all duration-150', skin ? 'py-2 px-1' : 'py-2.5 px-2', active && 'active')} aria-pressed={active} onClick={onClick} {...rest}>
      {children}
      <span className={cn('font-medium text-text', skin ? 'text-[11px]' : 'text-[12px]')}>{label}</span>
    </button>
  )
}

export function AppearanceSection() {
  const appearance = useAppearance()
  const locale = useLocale()
  const settings = useSettingsQuery()
  const save = useSaveSettings()
  const extSkins = useExtensionSkins()
  const pickSkin = (key: string) => { const v = SkinSchema.safeParse(key); if (v.success) setSkin(v.data); else if (extSkins.some((s) => s.key === key)) setSkin(key) }
  return (
    <div className="settings-section" data-section="appearance">
      <div className="settings-field">
        <label>{m.settings_label_theme()}</label>
        <div id="themePickerGrid" className="grid gap-2 mt-1 grid-cols-3">
          <PickButton className="theme-pick-btn" data-theme-val="light" active={appearance.theme === 'light'} onClick={() => setTheme('light')} label={m.theme_light()}>
            <div className={PICK_PREVIEW} style={{ background: '#fff', border: '1px solid rgba(0,0,0,.12)' }}><Sun size={16} color="#999" aria-hidden="true" /></div>
          </PickButton>
          <PickButton className="theme-pick-btn" data-theme-val="dark" active={appearance.theme === 'dark'} onClick={() => setTheme('dark')} label={m.theme_dark()}>
            <div className={PICK_PREVIEW} style={{ background: '#1a1a2e', border: '1px solid rgba(255,255,255,.1)' }}><Moon size={16} color="#666" aria-hidden="true" /></div>
          </PickButton>
          <PickButton className="theme-pick-btn" data-theme-val="system" active={appearance.theme === 'system'} onClick={() => setTheme('system')} label={m.theme_system()}>
            <div className={PICK_PREVIEW} style={{ background: 'linear-gradient(to right,#fff,#1a1a2e)', border: '1px solid rgba(0,0,0,.12)' }}><Monitor size={16} color="#888" aria-hidden="true" /></div>
          </PickButton>
        </div>
      </div>
      <div className="settings-field">
        <label>{m.settings_label_skin()}</label>
        <div id="skinPickerGrid" className="grid gap-1.5 mt-1 grid-cols-4">
          {SKINS.map((key) => {
            const sw = SKIN_SWATCHES[key] ?? { label: key, colors: [] }
            return (
              <PickButton key={key} className="skin-pick-btn" data-skin-val={key} active={appearance.skin === key} onClick={() => pickSkin(key)} label={sw.label}>
                <div className="flex gap-[3px] justify-center mb-1">{sw.colors.map((c, i) => <span key={i} className="inline-block size-2.5 rounded-full" style={{ background: c }} />)}</div>
              </PickButton>
            )
          })}
          {extSkins.map((s) => (
            <PickButton key={s.key} className="skin-pick-btn" data-skin-val={s.key} active={appearance.skin === s.key} onClick={() => pickSkin(s.key)} label={`${s.name} (${m.extensions_skin_from({ name: s.extensionId })})`}>
              <div className="flex gap-[3px] justify-center mb-1">{(s.colors ?? []).slice(0, 3).map((c, i) => <span key={i} className="inline-block size-2.5 rounded-full" style={{ background: c }} />)}</div>
            </PickButton>
          ))}
        </div>
      </div>
      <div className="settings-field">
        <label>{m.settings_label_font_size()}</label>
        <div id="fontSizePickerGrid" className="grid gap-2 mt-1 grid-cols-[repeat(auto-fit,minmax(96px,1fr))]">
          {([['small', 10, m.font_size_small()], ['default', 13, m.font_size_default()], ['large', 17, m.font_size_large()], ['xlarge', 20, m.font_size_xlarge()]] as const).map(([size, px, label]) => (
            <PickButton key={size} className="font-size-pick-btn" data-font-size-val={size} active={appearance.fontSize === size} onClick={() => { const v = FontSizeSchema.safeParse(size); if (v.success) { setFontSize(v.data); save.mutate({ font_size: v.data }) } }} label={label}>
              <div className={PICK_PREVIEW + ' bg-surface border border-border'}><span className="font-semibold text-muted" style={{ fontSize: px }}>Aa</span></div>
            </PickButton>
          ))}
        </div>
      </div>
      <div className="settings-field">
        <FieldRow label={m.settings_label_full_width_chat()} htmlFor="settingsFullWidth" inline>
          <Checkbox id="settingsFullWidth" checked={appearance.fullWidth} onChange={(e) => { setFullWidthChat(e.target.checked); save.mutate({ full_width_chat: e.target.checked }) }} />
        </FieldRow>
      </div>
      <div className="settings-field">
        <FieldRow label={m.settings_label_rtl()} htmlFor="settingsRtl" inline>
          <Checkbox id="settingsRtl" checked={appearance.rtl} onChange={(e) => setRtl(e.target.checked)} />
        </FieldRow>
      </div>
      <div className="settings-field">
        <FieldRow label={m.settings_label_language()} htmlFor="settingsLanguage" inline>
          <NativeSelect id="settingsLanguage" value={locale} onChange={(e) => { setLanguage(e.target.value); save.mutate({ language: e.target.value }) }}>
            {LOCALE_INFO.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
          </NativeSelect>
        </FieldRow>
      </div>
      {settings.data?.webui_version && <div className="settings-version-badge inline-flex items-center px-2 py-[3px] rounded-(--r-sm) bg-surface-subtle text-muted text-[11px] font-semibold font-mono shrink-0 self-start tracking-[.02em] border border-border max-[769px]:whitespace-nowrap max-[769px]:max-w-full max-[769px]:overflow-hidden max-[769px]:text-ellipsis" data-testid="webui-version">v{settings.data.webui_version}</div>}
    </div>
  )
}
