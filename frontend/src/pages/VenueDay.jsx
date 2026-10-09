import { Fragment, useMemo, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  AlertTriangle, CalendarRange, CheckCircle2, ClipboardCheck, Gauge, IndianRupee, LayoutGrid, ListChecks, Lock,
  MapPin, Plus, Target, Users, Wallet, Zap, Repeat, Ban, UserX,
} from 'lucide-react'
import { post } from '../lib/api'
import { useApi, useLocalState } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { KIND, KINDS, METHOD, hours, money, moneyShort, num, pct, range12, time12, todayISO } from '../lib/format'
import { Badge, Delta, Empty, ErrorState, Kpi, KindLabel, PageSkeleton, Seg, Tabs } from '../components/ui'
import { ChannelMix, LineChart } from '../components/charts'
import DateNav from '../components/DateNav'
import AvailabilityGrid, { GridLegend } from '../components/AvailabilityGrid'
import { AllocationInfoDrawer, BlockDrawer, BookingDrawer, DirectBookingDrawer, GameDrawer } from '../components/drawers'
import { WINDOWS, attendBadge, closeBadge, payBadge } from '../components/badges'

export default function VenueDay() {
  const { id } = useParams()
  const [params, setParams] = useSearchParams()
  const date = params.get('date') || todayISO()
  const tab = params.get('tab') || 'summary'
  const setDate = (d) => setParams((p) => { const n = new URLSearchParams(p); d === todayISO() ? n.delete('date') : n.set('date', d); return n })
  const setTab = (t) => setParams((p) => { const n = new URLSearchParams(p); t === 'summary' ? n.delete('tab') : n.set('tab', t); return n })
  const day = useApi(`/api/venues/${id}/day?date=${date}`, { interval: 60000 })
  const grid = useApi(`/api/venues/${id}/grid?date=${date}`, { interval: 60000 })
  const [drawer, setDrawer] = useState(null)
  const refresh = () => { day.reload(); grid.reload() }

  if (day.error && !day.data) return <ErrorState error={day.error} onRetry={day.reload} />
  if (!day.data) return <PageSkeleton />
  const d = day.data
  const acts = d.activities
  const attention = needsAttention(d)
  const openItem = (kind, ref) => {
    if (kind === 'district' || kind === 'direct') setDrawer({ type: 'booking', id: ref })
    else if (kind === 'community') setDrawer({ type: 'game', id: ref })
  }

  return (
    <div className="stack rise">
      <div className="page-head">
        <div>
          <div className="crumbs"><Link to="/">Portfolio</Link> / <span>{d.venue.code}</span></div>
          <h1 style={{ marginTop: 4 }}>{d.venue.name}</h1>
          <p className="row" style={{ gap: 10 }}>
            <span>{d.hours ? `Open ${range12(d.hours.open_min, d.hours.close_min)}` : 'Closed on this date'}</span>
            {closeBadge(d.day_close.status)}
          </p>
        </div>
        <div className="row">
          <DateNav value={date} onChange={setDate} />
        </div>
      </div>

      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'summary', label: 'Daily performance', icon: Gauge },
        { value: 'grid', label: 'Availability', icon: LayoutGrid },
        { value: 'activity', label: 'Activity', icon: ListChecks, count: attention.length, alert: attention.length > 0 },
        { value: 'community', label: 'Community', icon: Users, count: acts.games.filter((g) => g.status !== 'cancelled').length },
        { value: 'close', label: 'Day close', icon: ClipboardCheck, count: d.day_close.missing.filter((x) => x.due !== false).length, alert: d.day_close.missing.some((x) => x.due !== false) },
      ]} />

      {tab === 'summary' && <Summary d={d} attention={attention} onOpen={openItem} goGrid={() => setTab('grid')} />}
      {tab === 'grid' && (
        <GridTab d={d} grid={grid.data} onFree={(f, m) => setDrawer({ type: 'direct', facilityId: f.id, start: m })}
          onBlock={() => setDrawer({ type: 'block' })}
          onOpen={(c) => {
            if (c.kind === 'block') setDrawer({ type: 'block', blockId: c.ref_id })
            else if (c.kind === 'district' || c.kind === 'direct') setDrawer({ type: 'booking', id: c.ref_id })
            else if (c.kind === 'community') setDrawer({ type: 'game', id: c.ref_id })
            else setDrawer({ type: 'info', cell: c })
          }}
          onAssign={(b) => setDrawer({ type: 'booking', id: b.id })} />
      )}
      {tab === 'activity' && <ActivityTab d={d} onOpen={openItem} attention={attention} />}
      {tab === 'community' && <CommunityTab d={d} onOpen={(g) => setDrawer({ type: 'game', id: g.id })} />}
      {tab === 'close' && <CloseTab d={d} date={date} onOpen={openItem} onClosed={refresh} />}

      {drawer?.type === 'booking' && <BookingDrawer id={drawer.id} onClose={() => setDrawer(null)} onChanged={refresh} />}
      {drawer?.type === 'game' && <GameDrawer id={drawer.id} onClose={() => setDrawer(null)} onChanged={refresh} />}
      {drawer?.type === 'direct' && <DirectBookingDrawer venue={d.venue} date={date} facilityId={drawer.facilityId} start={drawer.start} onClose={() => setDrawer(null)} onDone={refresh} />}
      {drawer?.type === 'block' && <BlockDrawer venue={d.venue} date={date} blockId={drawer.blockId} onClose={() => setDrawer(null)} onDone={refresh} />}
      {drawer?.type === 'info' && <AllocationInfoDrawer cell={drawer.cell} onClose={() => setDrawer(null)} />}
    </div>
  )
}

function needsAttention(d) {
  const out = []
  const now = d.now_min
  for (const b of d.activities.bookings) {
    if (b.status !== 'confirmed') continue
    if (b.payment_status === 'overdue') out.push({ kind: b.source, ref: b.id, tone: 'bad', icon: AlertTriangle, text: `${b.customer.name || b.customer.phone} · ${time12(b.start_min)} — payment overdue`, sub: `${money(b.booking_value)} · collect or cancel` })
    else if (b.source === 'district' && !b.facility_id && b.attendance === 'pending' && (now == null || now >= b.start_min - 30)) out.push({ kind: b.source, ref: b.id, tone: 'info', icon: MapPin, text: `District ${b.external_ref} · ${time12(b.start_min)} — assign court on arrival`, sub: phoneLabel(b) })
    else if (b.attendance === 'pending' && b.can_no_show) out.push({ kind: b.source, ref: b.id, tone: 'warn', icon: UserX, text: `${b.customer.name || b.customer.phone} · ${time12(b.start_min)} — not checked in`, sub: 'Mark arrival or no-show' })
    else if (b.payment_status === 'to_collect' && b.attendance === 'attended') out.push({ kind: b.source, ref: b.id, tone: 'warn', icon: Wallet, text: `${b.customer.name || b.customer.phone} · ${time12(b.start_min)} — collect ${money(b.booking_value)}`, sub: `Window until ${time12(b.payment_window_end_min)}` })
  }
  for (const g of d.activities.games) {
    if (g.status === 'cancelled' || g.state === 'upcoming') continue
    const pending = g.participant_count - g.attended
    if (pending > 0 || g.attended > g.paid) out.push({ kind: 'community', ref: g.id, tone: 'warn', icon: Users, text: `${g.title} — ${pending > 0 ? `${pending} attendance to record` : `${g.attended - g.paid} payments to record`}`, sub: range12(g.start_min, g.end_min) })
  }
  return out
}

const phoneLabel = (b) => `${b.customer.phone} · ${money(b.booking_value)} · ${b.payment_status === 'paid' ? 'Prepaid' : 'Pay at venue'}`

function AttentionList({ items, onOpen }) {
  if (!items.length) return <Empty icon={CheckCircle2} title="All clear">Nothing needs action right now.</Empty>
  return (
    <div className="list">
      {items.map((x, i) => (
        <button key={i} className="list-item" onClick={() => onOpen(x.kind, x.ref)}>
          <span className={`insight-icon ${x.tone === 'bad' ? 'down' : ''}`} style={{ background: `var(--${x.tone}-soft)`, color: `var(--${x.tone})`, width: 30, height: 30 }}>
            <x.icon size={16} aria-hidden />
          </span>
          <span className="grow">
            <span className="title" style={{ display: 'block' }}>{x.text}</span>
            <span className="meta">{x.sub}</span>
          </span>
        </button>
      ))}
    </div>
  )
}

function Summary({ d, attention, onOpen, goGrid }) {
  const [win, setWin] = useLocalState('pulse.window', 'same_weekday')
  const live = d.comparison_mode === 'intraday'
  const m = (live && d.metrics_now) || d.metrics
  const full = d.metrics
  const c = d.comparisons
  return (
    <>
      <div className="spread">
        <Seg value={win} onChange={setWin} options={WINDOWS} label="Comparison baseline" />
        <span className="subtle" style={{ fontSize: 12.5 }}>
          {live ? `Live: compared up to ${time12(d.now_min)} on comparable days` : 'Full-day comparison'}
        </span>
      </div>
      <div className="kpis">
        <Kpi label="Net revenue" icon={IndianRupee} value={money(m.net_revenue)} cmp={c.net_revenue} windowKey={win} />
        <Kpi label="Occupancy" icon={Gauge} value={pct(m.occupancy, 1)} cmp={c.occupancy} windowKey={win}
          foot={<span>· {hours(m.occupied_units)} / {hours(m.sellable_units)}</span>} />
        <Kpi label="Activity" icon={Zap} value={num(m.total_activity)} cmp={c.total_activity} windowKey={win} />
        <Kpi label="Revenue opportunity" icon={Target} value={money(m.opportunity)} cmp={c.opportunity} windowKey={win} invert
          foot={<span>· {hours(m.unused_units)} unused</span>} />
        <Kpi label="Outstanding" icon={Wallet} value={money(m.outstanding)}
          foot={<span>{m.overdue ? <b style={{ color: 'var(--bad)' }}>{m.overdue} overdue</b> : 'none overdue'} · {money(m.collected)} collected</span>} />
        <Kpi label="Customers" icon={Repeat} value={num(m.unique_customers)}
          foot={<span>{m.returning_customers} returning · venue repeat rate {pct(d.repeat.rate)} (90d)</span>} />
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head"><h2>Needs attention</h2><button className="btn btn-sm" onClick={goGrid}><LayoutGrid /> Availability</button></div>
          <div className="card-body scroll-y"><AttentionList items={attention} onOpen={onOpen} /></div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Inventory</h2><span className="hint">{live ? 'whole day' : '30-min units'}</span></div>
          <div className="card-body">
            <InventoryBar m={full} />
          </div>
        </div>
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head"><h2>{live ? 'Intraday progression' : 'Last 30 days'}</h2><span className="hint">{live ? 'cumulative net revenue' : 'net revenue'}</span></div>
          <div className="card-body">
            {d.intraday && live ? (
              <LineChart data={d.intraday.points} x="label" xFormat={(v) => v} format={money} yFormat={moneyShort}
                ariaLabel="Cumulative revenue today versus same weekday average"
                series={[
                  { key: 'net_revenue', label: 'Today', color: 'var(--brand)' },
                  { key: 'baseline_net_revenue', label: `Same weekday avg (${d.intraday.baseline_samples} wks)`, color: 'var(--text-3)', dashed: true },
                ]} />
            ) : (
              <LineChart data={d.trend} format={money} yFormat={moneyShort} ariaLabel="Net revenue, last 30 days"
                series={[{ key: 'net_revenue', label: 'Net revenue', color: 'var(--brand)' }]} />
            )}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Revenue</h2><span className="hint">{live ? "today's book, incl. prepaid" : 'full day'}</span></div>
          <div className="card-body">
            <dl className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
              <dt>Booking value</dt><dd className="right">{money(full.booking_value)}</dd>
              <dt>Collected</dt><dd className="right">{money(full.collected)}</dd>
              {full.academy_accrued > 0 && <><dt>Academy (accrued, month-end)</dt><dd className="right">{money(full.academy_accrued)}</dd></>}
              <dt>Refunds</dt><dd className="right">−{money(full.refunds)}</dd>
              <dt style={{ color: 'var(--text)', fontWeight: 600 }}>Net revenue</dt><dd className="right" style={{ fontWeight: 600 }}>{money(full.net_revenue)}</dd>
            </dl>
            <div className="divider" />
            <div className="section-title">Collected by method</div>
            <dl className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
              {Object.entries(full.collected_by_method).filter(([, v]) => v > 0).map(([k, v]) => (
                <Fragment key={k}><dt>{METHOD[k] || k}</dt><dd className="right">{money(v)}</dd></Fragment>
              ))}
            </dl>
          </div>
        </div>
      </div>

      <div className="grid g-2">
        <div className="card">
          <div className="card-head"><h2>Activity by channel</h2><span className="hint">net revenue</span></div>
          <div className="card-body">
            <ChannelMix values={m.net_by_kind} title="Net revenue by channel" />
            <div className="divider" />
            <div className="row" style={{ gap: 14, fontSize: 13 }}>
              {KINDS.map((k) => <span key={k}><KindLabel kind={k} />: <b>{m.activities[k]}</b></span>)}
              <span><KindLabel kind="block" />: <b>{m.activities.block}</b></span>
            </div>
            <p className="subtle" style={{ fontSize: 12.5, marginTop: 8 }}>{m.no_shows} no-shows · {m.cancellations} cancellations</p>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Historical comparison</h2><span className="hint">{live ? 'same time of day' : 'full day'}</span></div>
          <div className="card-body table-wrap">
            <ComparisonTable c={c} />
          </div>
        </div>
      </div>
    </>
  )
}

function InventoryBar({ m }) {
  const parts = [
    { label: 'Occupied', v: m.occupied_units, color: 'var(--brand)' },
    { label: 'Booked, not yet used', v: m.reserved_units, color: 'color-mix(in srgb, var(--brand) 40%, var(--surface))' },
    { label: 'Unused', v: m.unused_units, color: 'var(--surface-sunk)' },
    { label: 'Blocked', v: m.blocked_units, color: 'var(--k-block)' },
  ]
  const total = m.operational_units || 1
  return (
    <div className="mix">
      <div className="mix-bar" style={{ height: 16 }} role="img" aria-label={parts.map((p) => `${p.label} ${hours(p.v)}`).join(', ')}>
        {parts.map((p) => p.v > 0 && <span key={p.label} style={{ width: `${(p.v / total) * 100}%`, background: p.color, border: p.label === 'Unused' ? '1px solid var(--border)' : 0 }} />)}
      </div>
      <div className="mix-rows">
        {parts.map((p) => (
          <div key={p.label} className="mix-row">
            <span className="kind-label"><span className="kind-dot" style={{ background: p.color, border: '1px solid var(--border)' }} />{p.label}</span>
            <span className="v">{hours(p.v)}</span>
            <span className="p">{pct(p.v / total)}</span>
          </div>
        ))}
        <div className="mix-row" style={{ borderTop: '1px solid var(--border)', paddingTop: 6 }}>
          <span>Operational · Sellable</span><span className="v">{hours(m.operational_units)} · {hours(m.sellable_units)}</span><span className="p" />
        </div>
      </div>
    </div>
  )
}

const CMP_ROWS = [
  ['net_revenue', 'Net revenue', money, false],
  ['occupancy', 'Occupancy', (v) => pct(v, 1), false],
  ['utilization', 'Utilization', (v) => pct(v, 1), false],
  ['total_activity', 'Activity', num, false],
  ['opportunity', 'Opportunity', money, true],
  ['unique_customers', 'Customers', num, false],
]

function ComparisonTable({ c }) {
  return (
    <table className="table">
      <thead>
        <tr><th>Metric</th><th className="num">Now</th>{WINDOWS.map((w) => <th key={w.value} className="num">{w.label}</th>)}</tr>
      </thead>
      <tbody>
        {CMP_ROWS.map(([k, label, f, inv]) => (
          <tr key={k}>
            <td>{label}</td>
            <td className="num" style={{ fontWeight: 600 }}>{f(c[k]?.current)}</td>
            {WINDOWS.map((w) => {
              const x = c[k]?.windows?.[w.value]
              return (
                <td key={w.value} className="num" title={x?.available ? `Baseline ${f(x.baseline)} · ${x.samples} day(s)` : `${x?.samples ?? 0} of ${x?.required} days`}>
                  {x?.available ? <><div className="subtle" style={{ fontSize: 12 }}>{f(x.baseline)}</div><Delta win={x} invert={inv} /></> : <span className="subtle">—</span>}
                </td>
              )
            })}
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function GridTab({ d, grid, onFree, onOpen, onBlock, onAssign }) {
  const { isHQ } = useAuth()
  if (!grid) return <PageSkeleton />
  const past = d.date < todayISO()
  return (
    <div className="stack">
      <div className="spread">
        <GridLegend />
        <div className="row">
          <button className="btn" onClick={onBlock}><Ban /> Block facility</button>
          {d.hours && <button className="btn btn-primary" disabled={past && !isHQ}
            onClick={() => onFree(grid.facilities[0], d.now_min != null ? Math.max(d.hours.open_min, Math.ceil(d.now_min / 30) * 30) : d.hours.open_min)}><Plus /> Direct booking</button>}
        </div>
      </div>
      {grid.unassigned_district.length > 0 && (
        <div className="card card-pad">
          <div className="spread" style={{ marginBottom: 8 }}>
            <h3><KindLabel kind="district">District bookings awaiting court</KindLabel></h3>
            <span className="subtle" style={{ fontSize: 12.5 }}>Each holds one court of venue capacity until assigned</span>
          </div>
          <div className="row">
            {grid.unassigned_district.map((b) => (
              <button key={b.id} className="btn btn-sm" onClick={() => onAssign(b)}>
                <MapPin /> {time12(b.start_min)} · {b.customer.phone.slice(-4).padStart(10, '•')} · {b.payment_status === 'paid' ? 'Prepaid' : 'Pay at venue'}
              </button>
            ))}
          </div>
        </div>
      )}
      <AvailabilityGrid grid={grid} onFree={onFree} onOpen={onOpen} readOnly={past && !isHQ} />
      <p className="subtle" style={{ fontSize: 12.5 }}>Tap any free slot to create a Direct booking. Availability is derived from operating hours and live allocations.</p>
    </div>
  )
}

function ActivityTab({ d, onOpen, attention }) {
  const [filter, setFilter] = useState('all')
  const rows = useMemo(() => {
    const a = d.activities
    const list = [
      ...a.bookings.map((b) => ({ kind: b.source, start: b.start_min, ref: b.id, title: b.customer.name || b.customer.phone, sub: `${b.facility || 'Court not assigned'}${b.external_ref ? ` · ${b.external_ref}` : ''}`, amount: b.booking_value, badges: <>{attendBadge(b)}{b.status === 'confirmed' && b.attendance !== 'no_show' && payBadge(b.payment_status)}</>, time: range12(b.start_min, b.end_min) })),
      ...a.games.map((g) => ({ kind: 'community', start: g.start_min, ref: g.id, title: g.title, sub: `${g.facilities.join(' + ')} · ${g.participant_count}/${g.capacity} players`, amount: g.collected, badges: <Badge tone={g.status === 'cancelled' ? 'bad' : undefined}>{g.state}</Badge>, time: range12(g.start_min, g.end_min) })),
      ...a.academy.map((o) => ({ kind: 'academy', start: o.start_min, title: o.name, sub: o.facilities.join(' + '), amount: o.value, badges: <Badge tone={o.status === 'cancelled' ? 'bad' : undefined}>{o.status}</Badge>, time: range12(o.start_min, o.end_min) })),
      ...a.events.map((e) => ({ kind: 'corporate', start: e.start_min, title: e.company, sub: `${e.facilities.join(' + ')} · ${e.contact_name}`, amount: e.amount, badges: payBadge(e.payment_status), time: range12(e.start_min, e.end_min) })),
      ...a.blocks.map((b) => ({ kind: 'block', start: b.start_min, title: b.reason.replace('_', ' '), sub: `${b.facility}${b.note ? ` · ${b.note}` : ''}`, badges: <Badge>{b.released_at ? 'released' : 'active'}</Badge>, time: range12(b.start_min, b.end_min) })),
    ]
    return list.filter((r) => filter === 'all' || r.kind === filter).sort((x, y) => x.start - y.start)
  }, [d, filter])
  return (
    <div className="grid g-main">
      <div className="card">
        <div className="card-head">
          <h2>All activity</h2>
          <select className="select" style={{ width: 170, minHeight: 32 }} value={filter} onChange={(e) => setFilter(e.target.value)} aria-label="Filter">
            <option value="all">All types</option>
            {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
          </select>
        </div>
        <div className="card-body table-wrap">
          {rows.length === 0 ? <Empty icon={CalendarRange} title="No activity" /> : (
            <table className="table">
              <thead><tr><th>Time</th><th>Type</th><th>Who / what</th><th>Status</th><th className="num">Amount</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className={r.ref ? 'clickable' : ''} onClick={() => r.ref && onOpen(r.kind, r.ref)}>
                    <td className="nowrap mono">{r.time}</td>
                    <td><KindLabel kind={r.kind} /></td>
                    <td><div style={{ fontWeight: 500 }}>{r.title}</div><div className="subtle" style={{ fontSize: 12 }}>{r.sub}</div></td>
                    <td><div className="row" style={{ gap: 4 }}>{r.badges}</div></td>
                    <td className="num">{r.amount != null ? money(r.amount) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h2>Needs attention</h2></div>
        <div className="card-body"><AttentionList items={attention} onOpen={onOpen} /></div>
      </div>
    </div>
  )
}

function CommunityTab({ d, onOpen }) {
  const games = d.activities.games
  if (!games.length) return <div className="card"><Empty icon={Users} title="No Community Games today">Games are lodged by HQ and appear here with their courts reserved.</Empty></div>
  return (
    <div className="grid g-2">
      {games.map((g) => (
        <button key={g.id} className="card card-pad" style={{ textAlign: 'left', cursor: 'pointer' }} onClick={() => onOpen(g)}>
          <div className="spread"><KindLabel kind="community">{g.title}</KindLabel><Badge tone={g.state === 'live' ? 'good' : g.state === 'cancelled' ? 'bad' : undefined}>{g.state}</Badge></div>
          <p className="muted" style={{ marginTop: 6 }}>{g.facilities.join(' + ')} · {range12(g.start_min, g.end_min)} · {g.sport}</p>
          <div className="row" style={{ marginTop: 12 }}>
            <Badge>{g.participant_count}/{g.capacity} players</Badge>
            <Badge tone="good">{g.attended} attended</Badge>
            <Badge tone="brand">{money(g.collected)} collected</Badge>
            <Badge>{money(g.per_person)}/person</Badge>
          </div>
        </button>
      ))}
    </div>
  )
}

function CloseTab({ d, date, onOpen, onClosed }) {
  const [run, busy] = useAction()
  const st = d.day_close
  const done = st.status === 'closed' || st.status === 'closed_late'
  return (
    <div className="grid g-main">
      <div className="card">
        <div className="card-head"><h2>Day close · {date}</h2>{closeBadge(st.status)}</div>
        <div className="card-body stack">
          {!st.operating && <div className="callout info">The venue is not operating on this date.</div>}
          {done && <div className="callout good"><CheckCircle2 />Closed {st.closed_at ? `at ${new Date(st.closed_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}` : ''}. HQ can still correct records; analytics update automatically.</div>}
          {st.status === 'missed' && <div className="callout bad"><AlertTriangle />Missed the {st.deadline} deadline — Operations was notified. Resolve the items and close late.</div>}
          {!done && st.operating && (
            st.missing.length ? (
              <>
                <p className="muted">Resolve these before closing (deadline {time12(23 * 60 + 30)}):</p>
                <div className="list">
                  {[...st.missing].sort((a, b) => (b.due !== false) - (a.due !== false)).map((it, i) => (
                    <button key={i} className="list-item" onClick={() => it.game_id ? onOpen('community', it.game_id) : onOpen('district', it.ref_id)}>
                      <AlertTriangle size={16} color={it.due === false ? 'var(--text-3)' : 'var(--warn)'} aria-hidden />
                      <span className="grow title" style={{ fontSize: 13.5, color: it.due === false ? 'var(--text-3)' : undefined }}>{it.message}{it.due === false ? ' · upcoming' : ''}</span>
                    </button>
                  ))}
                </div>
              </>
            ) : <div className="callout good"><CheckCircle2 />All bookings, payments and Community attendance are resolved.</div>
          )}
          {!done && st.operating && (
            <div>
              <button className="btn btn-primary" disabled={!st.can_close || busy}
                onClick={async () => { if (await run(() => post(`/api/venues/${d.venue.id}/close?date=${date}`), 'Day closed')) onClosed() }}>
                <Lock /> Close {date === todayISO() ? 'today' : 'this day'}
              </button>
            </div>
          )}
        </div>
      </div>
      <div className="card">
        <div className="card-head"><h2>What close checks</h2></div>
        <div className="card-body">
          <ul className="muted" style={{ margin: 0, paddingLeft: 18, display: 'grid', gap: 6, fontSize: 13.5 }}>
            <li>Every booking has a final state (attended, no-show or cancelled)</li>
            <li>Every played booking is paid, or overdue ones are cancelled</li>
            <li>Community participants have attendance and payment</li>
            <li>Walk-ins were recorded as Direct bookings</li>
          </ul>
          {st.missed_items && <><div className="divider" /><div className="section-title">Logged at escalation</div>{st.missed_items.map((x, i) => <p key={i} className="subtle" style={{ fontSize: 12.5 }}>• {x.message}</p>)}</>}
        </div>
      </div>
    </div>
  )
}

