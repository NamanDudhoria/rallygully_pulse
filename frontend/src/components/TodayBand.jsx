import { IndianRupee } from 'lucide-react'
import { hours, money, num, pct } from '../lib/format'
import { Delta } from './ui'
import { WINDOW_LABEL } from './badges'

/** The day at a glance: revenue first, then the aggregates ops reads every few minutes. */
export default function TodayBand({ full: fullIn, now: nowIn, cmp, win, live, date, extra = [] }) {
  const full = fullIn || {}
  const now = nowIn || full
  const a = full.activities || {}
  const cells = [
    { label: 'Collected', value: money(full.collected), sub: full.refunds > 0 ? `${money(full.refunds)} refunded` : 'no refunds' },
    { label: 'To collect', value: money(full.outstanding), sub: full.overdue ? `${full.overdue} overdue` : 'none overdue', tone: full.overdue ? 'bad' : undefined },
    { label: live ? 'Bookings today' : 'Bookings', value: num((a.direct || 0) + (a.district || 0)), sub: `${a.direct || 0} direct · ${a.district || 0} District` },
    { label: 'Community · Academy', value: `${a.community || 0} · ${a.academy || 0}`, sub: `${a.corporate || 0} corporate event${a.corporate === 1 ? '' : 's'}` },
    { label: live ? 'Occupancy so far' : 'Occupancy', value: pct(now.occupancy, 1), sub: `${hours(now.occupied_units)} of ${hours(now.sellable_units)}` },
    { label: 'Customers', value: num(full.unique_customers), sub: `${num(full.returning_customers)} returning` },
    ...extra,
  ]
  return (
    <section className="card today-band" aria-label={live ? 'Today at a glance' : 'Day at a glance'}>
      <div className="today-hero">
        <div className="kpi-label"><IndianRupee size={14} aria-hidden />{live ? "Today's revenue" : `Revenue · ${date}`}</div>
        <div className="today-value">{money(full.net_revenue)}</div>
        <div className="kpi-foot">
          <Delta win={cmp?.net_revenue?.windows?.[win]} />
          <span>{WINDOW_LABEL[win]}{live ? ', same time of day' : ''}</span>
        </div>
        <div className="subtle" style={{ fontSize: 12 }}>{money(full.booking_value)} booked value</div>
      </div>
      <div className="today-cells">
        {cells.map((c) => (
          <div key={c.label} className="today-cell">
            <div className="kpi-label">{c.label}</div>
            <div className="today-cell-value" style={c.tone ? { color: `var(--${c.tone})` } : undefined}>{c.value}</div>
            <div className="subtle" style={{ fontSize: 12 }}>{c.sub}</div>
          </div>
        ))}
      </div>
    </section>
  )
}
