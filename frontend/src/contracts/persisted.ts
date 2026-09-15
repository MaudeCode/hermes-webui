import { z } from 'zod'

export const ThemeSchema = z.enum(['light', 'dark', 'system'])
export type Theme = z.infer<typeof ThemeSchema>
export const SkinSchema = z.enum(['codex', 'terracotta', 'default', 'ares', 'mono', 'graphite', 'github', 'slate', 'poseidon', 'sisyphus', 'charizard', 'sienna', 'catppuccin', 'hepburn', 'nous', 'geist-contrast', 'neon', 'neon-soft', 'neon-paint', 'zeus', 'verdigris'])
export type Skin = z.infer<typeof SkinSchema>
export const FontSizeSchema = z.enum(['default', 'small', 'large', 'xlarge'])
export type FontSize = z.infer<typeof FontSizeSchema>

/** Sidebar/rail tab order and hidden tabs (`hermes-webui-tab-order`, `hermes-webui-hidden-tabs`). */
export const TabIdListSchema = z.array(z.string().trim().min(1).max(64)).max(32)

/** Per-session composer draft mirror kept locally before the server draft round-trips. */
export const LocalDraftSchema = z.object({ text: z.string().max(200_000), updatedAt: z.number() })

/** Sidebar collapsed groups (`hermes-webui-collapsed-groups`). */
export const CollapsedGroupsSchema = z.array(z.string().max(128)).max(200)

/** Last visible session id (`hermes-webui-session`). Plain string, validated as a session id. */
export const SessionIdSchema = z.string().regex(/^[A-Za-z0-9_.:-]{1,128}$/)

export const NumberPrefSchema = z.number()
export const BoolPrefSchema = z.boolean()
