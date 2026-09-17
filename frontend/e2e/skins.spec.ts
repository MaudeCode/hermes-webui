import { expect, settle, test } from './fixtures'

/** Every built-in skin in both schemes, on the seeded transcript (sidebar, header, user and assistant rows, tool card, composer). */
const SKINS = ['default', 'ares', 'mono', 'graphite', 'codex', 'terracotta', 'github', 'slate', 'poseidon', 'sisyphus', 'charizard', 'sienna', 'catppuccin', 'hepburn', 'nous', 'geist-contrast', 'zeus', 'neon', 'neon-soft', 'neon-paint', 'verdigris']

test.describe('skins', () => {
  test.skip(({ isMobile }) => isMobile, 'skins do not vary by viewport')
  for (const theme of ['dark', 'light'] as const) {
    for (const skin of SKINS) {
      test(`${skin} ${theme}`, async ({ page }) => {
        await page.addInitScript(({ s, t }) => { localStorage.setItem('hermes-skin', s); localStorage.setItem('hermes-theme', t) }, { s: skin, t: theme })
        await page.goto('/session/0e2e0f1c7a5e')
        await settle(page)
        await expect(page.locator('.msg-row').first()).toBeVisible()
        await expect(page.locator('.tool-worklog-summary, .tool-card').first()).toBeVisible()
        // The transcript opens scrolled to the end; capture both ends so the tool card and the headings are covered too.
        await expect(page).toHaveScreenshot(`skin-${skin}-${theme}-bottom.png`, { fullPage: false })
        await page.locator('#messages').evaluate((el) => { el.scrollTop = 0 })
        await expect(page).toHaveScreenshot(`skin-${skin}-${theme}-top.png`, { fullPage: false })
      })
    }
  }
})
