import type { ReactNode } from 'react'
import { Link, useParams } from '@tanstack/react-router'
import { AppShell } from '../../shell/AppShell'
import { PanelHead } from '../../shell/Sidebar'
import { m } from '../../paraglide/messages.js'
import { cn } from '../../ui/cn'
import { SettingsSectionSchema, type SettingsSection as Section } from '../../contracts/url'
import { useLocale } from '../../i18n/useLocale'
import { closeMobileSidebar } from '../../shell/useShellState'
import { MessageSquare, SunMedium, SlidersHorizontal, Key, Star, Puzzle, Server, CircleHelp, type LucideIcon } from 'lucide-react'

const SECTION_ICON: Record<Section, LucideIcon> = { conversation: MessageSquare, appearance: SunMedium, preferences: SlidersHorizontal, providers: Key, plugins: Star, extensions: Puzzle, system: Server, help: CircleHelp }

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
    <div className="panel-view active" id="panelSettings">
      <PanelHead title={m.tab_settings()} />
      <nav className="side-menu" id="settingsMenu" aria-label={m.tab_settings()}>
        <div className="settings-menu-items">
          {SettingsSectionSchema.options.map((section) => {
            const active = params.section === section
            const Icon = SECTION_ICON[section]
            return (
              <Link key={section} to="/settings/$section" params={{ section }} onClick={closeMobileSidebar} aria-current={active ? 'page' : undefined} className={cn('side-menu-item', active && 'active')} data-settings-section={section}>
                <Icon size={16} strokeWidth={1.5} aria-hidden="true" />
                <span>{SECTION_LABEL[section]()}</span>
              </Link>
            )
          })}
        </div>
      </nav>
    </div>
  )
}

export function SettingsLayout({ children }: { children: ReactNode }) {
  return (
    <AppShell sidebar={<SectionMenu />} showing="settings">
      <div className="main-view active" id="mainSettings">{children}</div>
    </AppShell>
  )
}
