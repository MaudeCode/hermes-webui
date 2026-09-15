import type { ReactNode } from 'react'
import type { SettingsSection as Section } from '../../contracts/url'
import { SECTION_LABEL } from './SettingsLayout'
import { AppearanceSection } from './AppearanceSection'
import { PreferencesSection } from './PreferencesSection'
import { ConversationSection } from './ConversationSection'
import { ProvidersSection } from './ProvidersSection'
import { PluginsSection } from './PluginsSection'
import { ExtensionsSection } from './ExtensionsSection'
import { SystemSection } from './SystemSection'
import { HelpSection } from './HelpSection'
import { useLocale } from '../../i18n/useLocale'
import { m } from '../../paraglide/messages.js'

const SECTIONS: Partial<Record<Section, () => ReactNode>> = {
  appearance: () => <AppearanceSection />,
  preferences: () => <PreferencesSection />,
  conversation: () => <ConversationSection />,
  providers: () => <ProvidersSection />,
  plugins: () => <PluginsSection />,
  extensions: () => <ExtensionsSection />,
  system: () => <SystemSection />,
  help: () => <HelpSection />,
}

export function registerSettingsSection(section: Section, render: () => ReactNode): void {
  SECTIONS[section] = render
}

export function SettingsSection({ section }: { section: Section }) {
  useLocale()
  const render = SECTIONS[section]
  return (
    <div className="settings-scroll">
      <div className="settings-main">
        <div className="settings-section-head">
          <div>
            <h1 className="settings-section-title">{SECTION_LABEL[section]()}</h1>
          </div>
        </div>
        {render ? render() : <p className="text-sm text-muted">{m.loading()}</p>}
      </div>
    </div>
  )
}
