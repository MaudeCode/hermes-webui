import type { ReactNode } from 'react'
import { Link, useParams } from '@tanstack/react-router'
import { AppShell } from '../../shell/AppShell'
import { PanelHead } from '../../shell/Sidebar'
import { m } from '../../paraglide/messages.js'
import { cn } from '../../ui/cn'
import { SettingsSectionSchema, type SettingsSection as Section } from '../../contracts/url'
import { useLocale } from '../../i18n/useLocale'
import { closeMobileSidebar } from '../../shell/useShellState'

export const SECTION_LABEL: Record<Section, () => string> = {
  appearance: () => m.settings_section_appearance_title(),
  conversation: () => m.settings_section_conversation_title(),
  preferences: () => m.settings_section_preferences_title(),
  providers: () => m.settings_section_providers_title(),
  plugins: () => m.settings_section_plugins_title(),
  extensions: () => m.settings_section_extensions_title(),
  system: () => m.settings_section_system_title(),
  help: () => m.settings_section_help_title(),
}

function SectionMenu() {
  useLocale()
  const params: { section?: string } = useParams({ strict: false })
  return (
    <div className="panel-view active flex min-h-0 flex-1 flex-col" id="panelSettings">
      <PanelHead title={m.tab_settings()} />
      <nav className="side-menu min-h-0 flex-1 overflow-y-auto p-2" id="settingsMenu" aria-label={m.tab_settings()}>
        {SettingsSectionSchema.options.map((section) => {
          const active = params.section === section
          return (
            <Link key={section} to="/settings/$section" params={{ section }} onClick={closeMobileSidebar} aria-current={active ? 'page' : undefined} className={cn('side-menu-item mb-0.5 block rounded-lg px-3 py-2 text-[13px] text-text no-underline hover:bg-hover', active && 'active bg-accent-bg text-accent-text')} data-section={section}>
              {SECTION_LABEL[section]()}
            </Link>
          )
        })}
      </nav>
    </div>
  )
}

export function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell sidebar={<SectionMenu />}>
      <div className="main-view hub-page flex min-h-0 flex-1 flex-col bg-bg" id="mainSettings">{children}</div>
    </AppShell>
  )
}
