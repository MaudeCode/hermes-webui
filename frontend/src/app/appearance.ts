/**
 * Appearance actions: theme, skin, font size, full-width chat, RTL, language.
 * Each applies immediately, persists the legacy localStorage key, and (where
 * the legacy app did) mirrors to server settings through the caller.
 */
import { readPersisted, writePersisted } from '../lib/persisted'
import { FontSizeSchema, type FontSize, type Skin, type Theme } from '../contracts/persisted'
import { applyAppearance, resolveAppearance } from '../theme/boot'
import { applyLocale } from '../i18n/runtime'
import { useSyncExternalStore } from 'react'

const listeners = new Set<() => void>()
let version = 0
const bump = () => { version += 1; for (const l of listeners) l() }

export interface AppearanceState { theme: Theme; skin: Skin; fontSize: FontSize; fullWidth: boolean; rtl: boolean }

export function readAppearance(): AppearanceState {
  const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches
  const r = resolveAppearance(readPersisted('hermes-theme'), readPersisted('hermes-skin'), prefersDark)
  const fs = FontSizeSchema.safeParse(readPersisted('hermes-font-size'))
  return { theme: r.theme, skin: r.skin, fontSize: fs.success ? fs.data : 'default', fullWidth: readPersisted('hermes-full-width-chat') === 'true', rtl: readPersisted('hermes-rtl') === 'true' }
}

export function setTheme(theme: Theme): void {
  writePersisted('hermes-theme', theme)
  applyAppearance(resolveAppearance(theme, readPersisted('hermes-skin'), window.matchMedia('(prefers-color-scheme: dark)').matches))
  bump()
}
export function setSkin(skin: Skin): void {
  writePersisted('hermes-skin', skin)
  applyAppearance(resolveAppearance(readPersisted('hermes-theme'), skin, window.matchMedia('(prefers-color-scheme: dark)').matches))
  bump()
}
export function setFontSize(size: FontSize): void {
  writePersisted('hermes-font-size', size)
  if (size === 'default') delete document.documentElement.dataset.fontSize
  else document.documentElement.dataset.fontSize = size
  bump()
}
export function setFullWidthChat(on: boolean): void {
  writePersisted('hermes-full-width-chat', on ? 'true' : 'false')
  if (on) document.documentElement.dataset.chatWidth = 'full'
  else delete document.documentElement.dataset.chatWidth
  bump()
}
export function setRtl(on: boolean): void {
  writePersisted('hermes-rtl', on ? 'true' : 'false')
  document.documentElement.dir = on ? 'rtl' : 'ltr'
  bump()
}
export function setLanguage(code: string): void {
  applyLocale(code)
  bump()
}

export function useAppearance(): AppearanceState {
  useSyncExternalStore((l) => { listeners.add(l); return () => listeners.delete(l) }, () => version, () => version)
  return readAppearance()
}
