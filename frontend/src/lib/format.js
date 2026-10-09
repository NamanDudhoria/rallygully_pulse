const inr = new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })

export const money = (v) => (v == null ? '—' : inr.format(v))
/** ₹ in Indian short units: 950, 12k, 1.2L, 3.4Cr. */
export function moneyShort(v) {
  if (v == null) return '—'
  const a = Math.abs(v)
  const sign = v < 0 ? '−' : ''
  const f = (x) => (Math.round(x * 10) / 10).toString()
  if (a >= 1e7) return `${sign}₹${f(a / 1e7)}Cr`
  if (a >= 1e5) return `${sign}₹${f(a / 1e5)}L`
  if (a >= 1e3) return `${sign}₹${f(a / 1e3)}k`
  return `${sign}₹${Math.round(a)}`
}
export const pct = (v, digits = 0) => (v == null ? '—' : `${(v * 100).toFixed(digits)}%`)
export const num = (v) => (v == null ? '—' : new Intl.NumberFormat('en-IN').format(Math.round(v * 10) / 10))
export const hours = (units) => (units == null ? '—' : `${num(units / 2)} h`)

export function hhmm(min) {
  if (min == null) return '—'
  const m = ((min % 1440) + 1440) % 1440
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
}

export function time12(min) {
  if (min == null) return '—'
  const m = ((min % 1440) + 1440) % 1440
  const h = Math.floor(m / 60)
  const mm = m % 60
  const suffix = h >= 12 ? 'PM' : 'AM'
  const h12 = h % 12 || 12
  return mm ? `${h12}:${String(mm).padStart(2, '0')} ${suffix}` : `${h12} ${suffix}`
}

export const range12 = (s, e) => `${time12(s)} – ${time12(e)}`

export function isoDate(d) {
  const x = d instanceof Date ? d : new Date(d)
  return `${x.getFullYear()}-${String(x.getMonth() + 1).padStart(2, '0')}-${String(x.getDate()).padStart(2, '0')}`
}

export function parseISO(s) {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

export function addDays(iso, n) {
  const d = parseISO(iso)
  d.setDate(d.getDate() + n)
  return isoDate(d)
}

export function longDate(iso) {
  return parseISO(iso).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'short', year: 'numeric' })
}

export function shortDate(iso) {
  return parseISO(iso).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })
}

export function dateTime(s) {
  if (!s) return '—'
  const d = new Date(s)
  return d.toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

// Venues run on IST; use the venue clock rather than the browser's timezone.
const istDate = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' })
export const todayISO = () => istDate.format(new Date())

export const KIND = {
  district: { label: 'District', color: 'var(--k-district)' },
  direct: { label: 'Direct', color: 'var(--k-direct)' },
  community: { label: 'Community', color: 'var(--k-community)' },
  academy: { label: 'Academy', color: 'var(--k-academy)' },
  corporate: { label: 'Corporate', color: 'var(--k-corporate)' },
  block: { label: 'Op. Block', color: 'var(--k-block)' },
}
export const KINDS = ['district', 'direct', 'community', 'academy', 'corporate']

export const METHOD = { cash: 'Cash', upi: 'UPI', card: 'Card', other: 'Other', online: 'District (online)' }

export function initials(name = '') {
  return name.split(' ').filter(Boolean).slice(0, 2).map((p) => p[0]).join('').toUpperCase() || '?'
}

export function phoneFmt(p) {
  return p && p.length === 10 ? `${p.slice(0, 5)} ${p.slice(5)}` : p || '—'
}
