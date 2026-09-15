import { m } from '../../paraglide/messages.js'
import { FieldRow, NativeSelect, Checkbox } from '../../ui/Field'
import { setFontSize, setFullWidthChat, setLanguage, setRtl, setSkin, setTheme, useAppearance } from '../../app/appearance'
import { SKINS } from '../../theme/boot'
import { FontSizeSchema, ThemeSchema, SkinSchema } from '../../contracts/persisted'
import { LOCALE_INFO } from '../../i18n/locales'
import { useLocale } from '../../i18n/useLocale'
import { useSaveSettings, useSettingsQuery } from '../../app/queries'

const SKIN_LABEL: Record<string, string> = { default: 'Default (Gold)', ares: 'Ares (Red)', mono: 'Mono (Gray)', graphite: 'Graphite', github: 'GitHub', slate: 'Slate', poseidon: 'Poseidon', sisyphus: 'Sisyphus', charizard: 'Charizard', sienna: 'Sienna', catppuccin: 'Catppuccin', hepburn: 'Hepburn', nous: 'Nous', 'geist-contrast': 'Geist Contrast', neon: 'Neon', 'neon-soft': 'Neon Soft', 'neon-paint': 'Neon Paint', zeus: 'Zeus', verdigris: 'Verdigris', codex: 'Codex', terracotta: 'Terracotta' }

export function AppearanceSection() {
  const appearance = useAppearance()
  const locale = useLocale()
  const settings = useSettingsQuery()
  const save = useSaveSettings()
  return (
    <div className="settings-section flex flex-col divide-y divide-border-subtle" data-section="appearance">
      <FieldRow label={m.settings_label_theme()} htmlFor="settingsTheme" inline>
        <NativeSelect id="settingsTheme" value={appearance.theme} onChange={(e) => { const v = ThemeSchema.safeParse(e.target.value); if (v.success) setTheme(v.data) }}>
          <option value="light">{m.theme_light()}</option>
          <option value="dark">{m.theme_dark()}</option>
          <option value="system">{m.theme_system()}</option>
        </NativeSelect>
      </FieldRow>
      <FieldRow label={m.settings_label_skin()} htmlFor="settingsSkin" inline>
        <NativeSelect id="settingsSkin" value={appearance.skin} onChange={(e) => { const v = SkinSchema.safeParse(e.target.value); if (v.success) setSkin(v.data) }}>
          {SKINS.map((s) => <option key={s} value={s}>{SKIN_LABEL[s] ?? s}</option>)}
        </NativeSelect>
      </FieldRow>
      <FieldRow label={m.settings_label_font_size()} htmlFor="settingsFontSize" inline>
        <NativeSelect id="settingsFontSize" value={appearance.fontSize} onChange={(e) => { const v = FontSizeSchema.safeParse(e.target.value); if (v.success) { setFontSize(v.data); save.mutate({ font_size: v.data }) } }}>
          <option value="small">{m.font_size_small()}</option>
          <option value="default">{m.font_size_default()}</option>
          <option value="large">{m.font_size_large()}</option>
          <option value="xlarge">{m.font_size_xlarge()}</option>
        </NativeSelect>
      </FieldRow>
      <FieldRow label={m.settings_label_full_width_chat()} htmlFor="settingsFullWidth" inline>
        <Checkbox id="settingsFullWidth" checked={appearance.fullWidth} onChange={(e) => { setFullWidthChat(e.target.checked); save.mutate({ full_width_chat: e.target.checked }) }} />
      </FieldRow>
      <FieldRow label={m.settings_label_rtl()} htmlFor="settingsRtl" inline>
        <Checkbox id="settingsRtl" checked={appearance.rtl} onChange={(e) => setRtl(e.target.checked)} />
      </FieldRow>
      <FieldRow label={m.settings_label_language()} htmlFor="settingsLanguage" inline>
        <NativeSelect id="settingsLanguage" value={locale} onChange={(e) => { setLanguage(e.target.value); save.mutate({ language: e.target.value }) }}>
          {LOCALE_INFO.map((l) => <option key={l.code} value={l.code}>{l.label}</option>)}
        </NativeSelect>
      </FieldRow>
      {settings.data?.webui_version && <div className="pt-3 text-[11px] text-muted">v{settings.data.webui_version}</div>}
    </div>
  )
}
