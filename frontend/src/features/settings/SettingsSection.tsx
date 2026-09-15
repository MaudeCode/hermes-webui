import type { ReactNode } from 'react'
import type { SettingsSection as Section } from '../../contracts/url'
import { SECTION_LABEL } from './SettingsLayout'
import { AppearanceSection } from './AppearanceSection'
import { useLocale } from '../../i18n/useLocale'
import { m } from '../../paraglide/messages.js'

const SECTIONS: Partial<Record<Section, () => ReactNode>> = { appearance: () => <AppearanceSection /> }

export function registerSettingsSection(section: Section, render: () => ReactNode): void {
  SECTIONS[section] = render
}

export function SettingsSection({ section }: { section: Section }) {
  useLocale()
  const render = SECTIONS[section]
  return (
    <>
      <header className="main-view-header flex min-h-12 items-center border-b border-border px-5 py-2.5 max-[768px]:px-3.5">
        <h1 className="main-view-title text-[17px] font-semibold text-strong">{SECTION_LABEL[section]()}</h1>
      </header>
      <div className="main-view-body min-h-0 flex-1 overflow-y-auto px-5 py-4 max-[768px]:px-3.5">
        <div className="mx-auto max-w-[720px]">{render ? render() : <p className="text-sm text-muted">{m.loading()}</p>}</div>
      </div>
    </>
  )
}
