import type { ReactNode, SelectHTMLAttributes, InputHTMLAttributes } from 'react'
import { cn } from './cn'

export function FieldRow({ label, hint, htmlFor, children, inline }: { label: ReactNode; hint?: ReactNode; htmlFor?: string; children: ReactNode; inline?: boolean }) {
  return (
    <div className={cn('flex gap-3 py-2', inline ? 'items-center justify-between' : 'flex-col')}>
      <div className="min-w-0">
        <label htmlFor={htmlFor} className="text-sm text-text">{label}</label>
        {hint && <div className="mt-0.5 text-[11px] text-muted">{hint}</div>}
      </div>
      <div className={cn(inline ? 'shrink-0' : '')}>{children}</div>
    </div>
  )
}

export function NativeSelect({ className, ...props }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...props} className={cn('h-8 rounded-md border border-border bg-input px-2 text-sm text-text', className)} />
}

export function TextInput({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...props} className={cn('h-9 w-full rounded-md border border-border bg-input px-3 text-sm text-text placeholder:text-muted focus:border-accent', className)} />
}

export function Checkbox({ className, ...props }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="checkbox" {...props} className={cn('h-[15px] w-[15px] accent-accent', className)} />
}
