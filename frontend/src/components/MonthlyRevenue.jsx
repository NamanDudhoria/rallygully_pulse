import { useMemo } from 'react'
import { useApi } from '../lib/hooks'
import { money, moneyShort } from '../lib/format'
import { Badge } from './ui'
import { LineChart } from './charts'

const label = (k) => new Date(`${k}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' })
const prevYear = (k) => `${Number(k.slice(0, 4)) - 1}${k.slice(4)}`
const prevMonth = (k) => { const [y, m] = k.split('-').map(Number); return m === 1 ? `${y - 1}-12` : `${y}-${String(m - 1).padStart(2, '0')}` }

/** Change vs an earlier month counting only venues that traded in both, so new venues don't inflate it. */
function likeForLike(venues, month, before) {
  let cur = 0
  let prev = 0
  for (const v of venues) {
    if (v.months[month] != null && v.months[before] != null) { cur += v.months[month]; prev += v.months[before] }
  }
  return prev ? (cur - prev) / prev : null
}

/** Revenue by month: imported history, then live Pulse months. Month-exact, so no day-level guesses. */
export default function MonthlyRevenue({ venueId, title = 'Monthly revenue' }) {
  const { data } = useApi(`/api/analytics/monthly${venueId ? `?venue_id=${venueId}` : ''}`)
  const rows = useMemo(() => (data?.months || []).map((m) => ({
    ...m, label: label(m.month),
    mom: likeForLike(data.venues, m.month, prevMonth(m.month)),
    yoy: likeForLike(data.venues, m.month, prevYear(m.month)),
  })), [data])
  if (!rows.length) return null
  const recent = rows.slice(-6).reverse()
  const sign = (v) => (v == null ? '—' : `${v >= 0 ? '+' : '−'}${Math.abs(v * 100).toFixed(1)}%`)
  return (
    <div className="card">
      <div className="card-head"><h2>{title}</h2></div>
      <div className="card-body">
        <LineChart data={rows} x="label" xFormat={(v) => v} format={money} yFormat={moneyShort} ariaLabel={title}
          series={[{ key: 'revenue', label: 'Revenue', color: 'var(--brand)' }]} />
        <div className="table-wrap" style={{ marginTop: 12 }}>
          <table className="table" style={{ fontSize: 12.5 }}>
            <thead><tr><th>Month</th><th className="num">Revenue</th><th className="num">vs previous month</th><th className="num">vs same month last year</th></tr></thead>
            <tbody>
              {recent.map((m) => (
                <tr key={m.month}>
                  <td className="nowrap">{m.label}{m.partial && <> <Badge>month to date</Badge></>}</td>
                  <td className="num">{money(m.revenue)}</td>
                  <td className="num">{m.partial ? '—' : sign(m.mom)}</td>
                  <td className="num">{m.partial ? '—' : sign(m.yoy)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
