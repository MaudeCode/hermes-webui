/**
 * The single Markdown rendering adapter. Streamdown owns parsing and
 * sanitization; Hermes structures are never passed through here as HTML.
 * Full plugin wiring (Shiki, Mermaid, KaTeX, CJK) lands in checkpoint 6.
 */
import { memo } from 'react'
import { Streamdown } from 'streamdown'

export const Markdown = memo(function Markdown({ text, streaming = false, className }: { text: string; streaming?: boolean; className?: string }) {
  return (
    <Streamdown mode={streaming ? 'streaming' : 'static'} parseIncompleteMarkdown={streaming} className={className ?? 'hermes-prose'}>
      {text}
    </Streamdown>
  )
})
