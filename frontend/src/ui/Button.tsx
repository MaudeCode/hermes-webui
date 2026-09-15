import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { cn } from './cn'

type Variant = 'default' | 'primary' | 'ghost' | 'danger' | 'link'
type Size = 'sm' | 'md'

const VARIANT: Record<Variant, string> = {
  default: 'border border-border bg-surface text-text hover:bg-hover',
  primary: 'border border-transparent bg-accent text-white hover:bg-accent-hover',
  ghost: 'border border-transparent bg-transparent text-muted hover:bg-hover hover:text-text',
  danger: 'border border-transparent bg-error text-white',
  link: 'border-0 bg-transparent p-0 text-accent-text underline',
}
const SIZE: Record<Size, string> = { sm: 'px-2.5 py-1 text-xs', md: 'px-3 py-1.5 text-sm' }

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant
  size?: Size
}

export function Button({ variant = 'default', size = 'md', className, type = 'button', ...props }: ButtonProps) {
  return <button type={type} {...props} className={cn('inline-flex items-center justify-center gap-1.5 rounded-md font-medium leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-50', VARIANT[variant], variant !== 'link' && SIZE[size], className)} />
}

export interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string
  children: ReactNode
  active?: boolean
}

/** Square icon button with a mandatory accessible name. */
export function IconButton({ label, children, className, active, type = 'button', ...props }: IconButtonProps) {
  return (
    <button type={type} aria-label={label} title={props.title ?? label} {...props} className={cn('inline-flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg border-0 bg-transparent text-muted transition-colors hover:bg-hover hover:text-text', active && 'bg-accent-bg text-accent-text', className)}>
      {children}
    </button>
  )
}
