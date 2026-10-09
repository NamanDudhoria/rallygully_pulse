import { useState } from 'react'
import { Search } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { addDays, money, phoneFmt, range12, shortDate, todayISO } from '../lib/format'
import { Empty, ErrorState, KindLabel, Skeleton } from '../components/ui'
import { BookingDrawer } from '../components/drawers'
import { attendBadge, payBadge } from '../components/badges'

export default function Bookings() {
  const [f, setF] = useState({ venue_id: '', source: '', payment_status: '', q: '', from: addDays(todayISO(), -7), to: addDays(todayISO(), 7) })
  const [open, setOpen] = useState(null)
  const venues = useApi('/api/venues').data
  const qs = new URLSearchParams({ date_from: f.from, date_to: f.to, limit: '300' })
  ;['venue_id', 'source', 'payment_status', 'q'].forEach((k) => f[k] && qs.set(k, f[k]))
  const { data, error, reload } = useApi(`/api/bookings?${qs}`)
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e.target.value }))
  const vname = (id) => venues?.find((v) => v.id === id)?.code

  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Bookings</h1><p>District and Direct consumer bookings</p></div>
      </div>
      <div className="card card-pad">
        <div className="form-row" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))' }}>
          <label className="field"><span>Search</span>
            <div style={{ position: 'relative' }}>
              <Search size={15} style={{ position: 'absolute', left: 10, top: 11, color: 'var(--text-3)' }} aria-hidden />
              <input className="input" style={{ paddingLeft: 32 }} placeholder="Phone, name, District ID" value={f.q} onChange={set('q')} />
            </div>
          </label>
          {venues?.length > 1 && (
            <label className="field"><span>Venue</span>
              <select className="select" value={f.venue_id} onChange={set('venue_id')}>
                <option value="">All venues</option>
                {venues.map((v) => <option key={v.id} value={v.id}>{v.name.replace('RallyGully ', '')}</option>)}
              </select>
            </label>
          )}
          <label className="field"><span>Source</span>
            <select className="select" value={f.source} onChange={set('source')}>
              <option value="">All</option><option value="district">District</option><option value="direct">Direct</option>
            </select>
          </label>
          <label className="field"><span>Payment</span>
            <select className="select" value={f.payment_status} onChange={set('payment_status')}>
              <option value="">Any</option><option value="paid">Paid</option><option value="to_collect">To collect</option><option value="overdue">Overdue</option>
            </select>
          </label>
          <label className="field"><span>From</span><input className="input" type="date" value={f.from} onChange={set('from')} /></label>
          <label className="field"><span>To</span><input className="input" type="date" value={f.to} onChange={set('to')} /></label>
        </div>
      </div>
      <div className="card">
        <div className="card-body table-wrap">
          {error ? <ErrorState error={error} onRetry={reload} /> : !data ? <Skeleton h={300} /> : data.length === 0 ? <Empty title="No bookings match" /> : (
            <table className="table">
              <thead><tr><th>Date</th><th>Time</th><th>Source</th><th>Customer</th><th>Venue · court</th><th>Status</th><th className="num">Value</th></tr></thead>
              <tbody>
                {data.map((b) => (
                  <tr key={b.id} className="clickable" onClick={() => setOpen(b.id)}>
                    <td className="nowrap">{shortDate(b.date)}</td>
                    <td className="nowrap mono">{range12(b.start_min, b.end_min)}</td>
                    <td><KindLabel kind={b.source} />{b.external_ref && <div className="subtle mono" style={{ fontSize: 11.5 }}>{b.external_ref}</div>}</td>
                    <td><div style={{ fontWeight: 500 }}>{b.customer.name || '—'}</div><div className="subtle" style={{ fontSize: 12 }}>{phoneFmt(b.customer.phone)}</div></td>
                    <td>{vname(b.venue_id)} · {b.facility || <span className="subtle">unassigned</span>}</td>
                    <td><div className="row" style={{ gap: 4 }}>{attendBadge(b)}{b.status === 'confirmed' && b.attendance !== 'no_show' && payBadge(b.payment_status)}</div></td>
                    <td className="num">{money(b.booking_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      {open && <BookingDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}
