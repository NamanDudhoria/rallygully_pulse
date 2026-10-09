import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { ArrowDownRight, ArrowUpRight, Minus, X, Inbox } from 'lucide-react'
import { KIND } from '../lib/format'
import { WINDOW_LABEL } from './badges'

export function Delta({ win, invert = false, digits = 1, label }) {
  if (!win || !win.available) {
    const need = win ? `needs ${win.required}+ days` : 'no history'
    return <span className="delta na" title={win ? `${win.samples} of ${win.required} days of history` : undefined}>{label ? `${label}: ` : ''}{need}</span>
  }
  const p = win.pct_change
  if (p == null) return <span className="delta na">—</span>
  const flat = Math.abs(p) < 0.005
  const good = invert ? p < 0 : p > 0
  const cls = flat ? 'flat' : good ? 'up' : 'down'
  const Icon = flat ? Minus : p > 0 ? ArrowUpRight : ArrowDownRight
  return (
    <span className={`delta ${cls}`} title={`Baseline ${win.baseline?.toFixed?.(2)} over ${win.samples} day(s)`}>
      <Icon aria-hidden />{p > 0 ? '+' : ''}{(p * 100).toFixed(digits)}%
      <span className="sr-only">{good ? ' better' : ' worse'} than {label || 'baseline'}</span>
    </span>
  )
}

export function Kpi({ label, value, unit, cmp, windowKey = 'same_weekday', invert, foot, icon: Icon, compareLabel }) {
  const win = cmp?.windows?.[windowKey]
  return (
    <div className="card kpi">
      <div className="kpi-label">{Icon && <Icon size={14} aria-hidden />}{label}</div>
      <div className="kpi-value">{value}{unit && <small>{unit}</small>}</div>
      <div className="kpi-foot">
        {cmp && <Delta win={win} invert={invert} />}
        {cmp && <span>{compareLabel || WINDOW_LABEL[windowKey]}</span>}
        {foot}
      </div>
    </div>
  )
}


export function Badge({ tone, children, icon: Icon }) {
  return <span className={`badge ${tone || ''}`}>{Icon && <Icon aria-hidden />}{children}</span>
}

export function KindLabel({ kind, children }) {
  const k = KIND[kind] || { label: kind, color: 'var(--text-3)' }
  return (
    <span className="kind-label">
      <span className="kind-dot" style={{ background: k.color }} aria-hidden />
      {children || k.label}
    </span>
  )
}

export function Empty({ icon: Icon = Inbox, title, children }) {
  return (
    <div className="empty">
      <Icon aria-hidden />
      {title && <strong>{title}</strong>}
      {children && <div>{children}</div>}
    </div>
  )
}

export function Field({ label, help, error, children }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {error ? <span className="err" role="alert">{error}</span> : help ? <span className="help">{help}</span> : null}
    </label>
  )
}

export function Seg({ value, onChange, options, label }) {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={value === o.value} onClick={() => onChange(o.value)}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Tabs({ value, onChange, tabs }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.value} role="tab" className="tab" aria-selected={value === t.value} onClick={() => onChange(t.value)}>
          {t.icon && <t.icon aria-hidden />}
          {t.label}
          {t.count != null && t.count > 0 && <span className={`count ${t.alert ? 'alert' : ''}`}>{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function Skeleton({ h = 16, w = '100%', style }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} aria-hidden />
}

export function PageSkeleton() {
  return (
    <div className="stack" aria-busy="true" aria-label="Loading">
      <Skeleton h={28} w={260} />
      <div className="kpis">{Array.from({ length: 6 }, (_, i) => <Skeleton key={i} h={96} />)}</div>
      <Skeleton h={260} />
    </div>
  )
}

export function ErrorState({ error, onRetry }) {
  return (
    <div className="card card-pad">
      <div className="callout bad"><span>{error?.message || 'Something went wrong.'}</span></div>
      {onRetry && <div style={{ marginTop: 12 }}><button className="btn" onClick={onRetry}>Try again</button></div>}
    </div>
  )
}

/** Side drawer with enter/exit motion; Esc closes, focus moves inside on open. */
export function Drawer({ open, onClose, title, subtitle, children, footer, width }) {
  const [closing, setClosing] = useState(false)
  const ref = useRef(null)
  const close = () => setClosing(true)
  useEffect(() => {
    if (!open) return undefined
    const prev = document.activeElement
    ref.current?.focus()
    const onKey = (e) => { if (e.key === 'Escape') setClosing(true) }
    window.addEventListener('keydown', onKey)
    return () => { window.removeEventListener('keydown', onKey); prev?.focus?.() }
  }, [open])
  if (!open) return null
  const done = () => { if (closing) { setClosing(false); onClose() } }
  // Portal: ancestors with transforms (entrance animations) would otherwise trap position: fixed.
  return createPortal(
    <>
      <div className={`overlay ${closing ? 'closing' : ''}`} onClick={close} aria-hidden />
      <aside
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={typeof title === 'string' ? title : undefined}
        className={`drawer ${closing ? 'closing' : ''}`} style={width ? { width: `min(${width}px, 100vw)` } : undefined}
        onAnimationEnd={done}
      >
        <div className="drawer-head">
          <div>
            <h2>{title}</h2>
            {subtitle && <p className="muted" style={{ marginTop: 4, fontSize: 13 }}>{subtitle}</p>}
          </div>
          <button className="btn btn-ghost btn-icon btn-sm" onClick={close} aria-label="Close"><X /></button>
        </div>
        <div className="drawer-body">{typeof children === 'function' ? children(close) : children}</div>
        {footer && <div className="drawer-foot">{typeof footer === 'function' ? footer(close) : footer}</div>}
      </aside>
    </>,
    document.body,
  )
}

export function Meter({ value }) {
  return <div className="meter" role="presentation"><span style={{ width: `${Math.min(100, Math.max(0, (value || 0) * 100))}%` }} /></div>
}
