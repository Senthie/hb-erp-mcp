import { useCallback, useEffect, useState } from 'react'
import type {
  ButtonHTMLAttributes,
  InputHTMLAttributes,
  ReactNode,
  SelectHTMLAttributes,
  TextareaHTMLAttributes,
} from 'react'
import { createPortal } from 'react-dom'

export function cn(...parts: Array<string | false | null | undefined>): string {
  return parts.filter(Boolean).join(' ')
}

const CONTROL =
  'w-full rounded-xl border border-white/10 bg-slate-950/60 px-3.5 py-2.5 text-sm ' +
  'text-slate-100 placeholder:text-slate-500 outline-none transition ' +
  'focus:border-sky-400/70 focus:ring-2 focus:ring-sky-400/20 disabled:opacity-60'

export function Card({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <section
      className={cn(
        'rounded-3xl border border-white/10 bg-slate-900/70 p-6 shadow-2xl',
        'shadow-slate-950/60 backdrop-blur-xl sm:p-8',
        className,
      )}
    >
      {children}
    </section>
  )
}

interface FieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string
  hint?: string
}

export function Field({ label, hint, required, className, ...rest }: FieldProps) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center gap-1 text-sm font-medium text-slate-300">
        {label}
        {required && <span className="text-rose-400">*</span>}
      </span>
      <input className={cn(CONTROL, className)} required={required} {...rest} />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

interface TextAreaProps extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label: string
  hint?: string
}

export function TextArea({ label, hint, className, ...rest }: TextAreaProps) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-300">{label}</span>
      <textarea className={cn(CONTROL, 'min-h-24 resize-y', className)} {...rest} />
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string
  options: Array<{ value: string; label: string }>
  hint?: string
}

export function Select({ label, options, hint, className, ...rest }: SelectProps) {
  return (
    <label className="block">
      <span className="mb-1.5 block text-sm font-medium text-slate-300">{label}</span>
      <select className={cn(CONTROL, 'appearance-none', className)} {...rest}>
        {options.map((item) => (
          <option key={item.value} value={item.value} className="bg-slate-900">
            {item.label}
          </option>
        ))}
      </select>
      {hint && <span className="mt-1 block text-xs text-slate-500">{hint}</span>}
    </label>
  )
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'ghost'
  busy?: boolean
}

export function Button({
  variant = 'primary',
  busy = false,
  className,
  children,
  disabled,
  ...rest
}: ButtonProps) {
  const base =
    'inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2.5 text-sm ' +
    'font-semibold transition focus:outline-none focus-visible:ring-2 ' +
    'focus-visible:ring-sky-400/40 disabled:cursor-not-allowed disabled:opacity-60'
  const styles =
    variant === 'primary'
      ? 'bg-sky-500 text-slate-950 hover:bg-sky-400 active:bg-sky-300'
      : 'border border-white/10 bg-white/5 text-slate-200 hover:bg-white/10'
  return (
    <button className={cn(base, styles, className)} disabled={disabled || busy} {...rest}>
      {busy && (
        <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />
      )}
      {children}
    </button>
  )
}

export function Alert({
  kind = 'error',
  children,
}: {
  kind?: 'error' | 'success' | 'info'
  children: ReactNode
}) {
  const styles = {
    error: 'border-rose-500/30 bg-rose-500/10 text-rose-200',
    success: 'border-emerald-500/30 bg-emerald-500/10 text-emerald-200',
    info: 'border-sky-500/30 bg-sky-500/10 text-sky-100',
  }[kind]
  return (
    <div className={cn('rounded-xl border px-3.5 py-3 text-sm leading-relaxed', styles)} role="status">
      {children}
    </div>
  )
}

export function Badge({ children }: { children: ReactNode }) {
  return (
    <span className="inline-flex items-center rounded-full border border-white/10 bg-white/5 px-2.5 py-0.5 font-mono text-xs text-slate-300">
      {children}
    </span>
  )
}

export function CopyButton({ value, label = '复制' }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false)

  useEffect(() => {
    if (!copied) return
    const timer = window.setTimeout(() => setCopied(false), 2000)
    return () => window.clearTimeout(timer)
  }, [copied])

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }, [value])

  return (
    <Button type="button" variant="ghost" onClick={copy} className="shrink-0 py-1.5 text-xs">
      {copied ? '已复制' : label}
    </Button>
  )
}

interface ModalProps {
  open: boolean
  title: string
  description?: string
  onClose: () => void
  /** false 时 Esc / 点遮罩都不关闭（例如「只显示一次」的结果，必须显式点完成） */
  dismissible?: boolean
  size?: 'md' | 'lg'
  children: ReactNode
}

export function Modal({
  open,
  title,
  description,
  onClose,
  dismissible = true,
  size = 'md',
  children,
}: ModalProps) {
  useEffect(() => {
    if (!open) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && dismissible) onClose()
    }
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    window.addEventListener('keydown', onKeyDown)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [open, dismissible, onClose])

  if (!open) return null

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/70 p-4 backdrop-blur-sm sm:items-center"
      onMouseDown={(event) => {
        if (dismissible && event.target === event.currentTarget) onClose()
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={cn(
          'max-h-[calc(100vh-2rem)] w-full overflow-y-auto rounded-2xl border border-white/10',
          'bg-slate-900 p-6 shadow-2xl shadow-slate-950/60',
          size === 'lg' ? 'max-w-2xl' : 'max-w-md',
        )}
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-semibold text-white">{title}</h3>
            {description && <p className="mt-1 text-xs text-slate-400">{description}</p>}
          </div>
          {dismissible && (
            <button
              type="button"
              onClick={onClose}
              aria-label="关闭"
              className="rounded-lg px-2 py-1 text-slate-400 transition hover:bg-white/10 hover:text-white"
            >
              ✕
            </button>
          )}
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
