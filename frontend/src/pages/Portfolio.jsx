import { useMemo, useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import { AlertTriangle, IndianRupee, Gauge, Layers, Repeat, Target, Zap } from 'lucide-react'
import { useApi, useLocalState } from '../lib/hooks'
import { hours, money, moneyShort, num, pct, todayISO, time12 } from '../lib/format'
import { Delta, ErrorState, Kpi, Meter, PageSkeleton, Seg } from '../components/ui'
import { WINDOWS, WINDOW_LABEL, closeBadge } from '../components/badges'
import { ChannelMix, LineChart } from '../components/charts'
import DateNav from '../components/DateNav'
import Insights from '../components/Insights'

export default function Portfolio() {
  const [params, setParams] = useSearchParams()
  const date = params.get('date') || todayISO()
  const [win, setWin] = useLocalState('pulse.window', 'same_weekday')
  const [trendMetric, setTrendMetric] = useState('net_revenue')
  const { data, error, loading, reload } = useApi(`/api/analytics/portfolio?date=${date}`, { interval: 60000 })
  const nav = useNavigate()

  const trend = useMemo(() => data?.trend || [], [data])
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <PageSkeleton />
  const live = data.comparison_mode === 'intraday'
  // Live day: tiles show 'so far' values so they match their same-time-of-day baselines.
  const m = (live && data.metrics_now) || data.metrics || {}
  const c = data.comparisons

  return (
    <div className="stack rise" aria-busy={loading}>
      <div className="page-head">
        <div>
          <h1>Portfolio</h1>
          <p>
            {data.venues.length} venues ·{' '}
            {live
              ? <>Live — compared with history up to the same time of day ({time12(minutesOf(data.cutoff_label))})</>
              : 'Full-day results'}
          </p>
        </div>
        <div className="row">
          <DateNav value={date} onChange={(d) => setParams(d === todayISO() ? {} : { date: d })} maxToday />
        </div>
      </div>

      <TodayBand data={data} win={win} date={date} />

      <div className="spread">
        <Seg value={win} onChange={setWin} options={WINDOWS} label="Comparison baseline" />
        <span className="subtle" style={{ fontSize: 12.5 }}>{data.history_days} days of history in Pulse</span>
      </div>

      <div className="kpis">
        <Kpi label="Net revenue" icon={IndianRupee} value={money(m.net_revenue)} cmp={c.net_revenue} windowKey={win}
          foot={m.refunds > 0 && <span>· {money(m.refunds)} refunded</span>} />
        <Kpi label="Occupancy" icon={Gauge} value={pct(m.occupancy, 1)} cmp={c.occupancy} windowKey={win}
          foot={<span>· {hours(m.occupied_units)} of {hours(m.sellable_units)}</span>} />
        <Kpi label="Total activity" icon={Zap} value={num(m.total_activity)} cmp={c.total_activity} windowKey={win}
          foot={<span>· {m.no_shows} no-shows</span>} />
        <Kpi label="Sellable inventory" icon={Layers} value={hours(m.sellable_units)}
          foot={<span>{hours(m.blocked_units)} blocked of {hours(m.operational_units)}{live ? ' so far' : ''}</span>} />
        <Kpi label="Revenue opportunity" icon={Target} value={money(m.opportunity)} cmp={c.opportunity} windowKey={win} invert
          foot={<span>· {hours(m.unused_units)} unused</span>} />
        <Kpi label="Repeat customer rate" icon={Repeat} value={pct(data.repeat.rate, 1)}
          foot={<span>{num(data.repeat.returning)} of {num(data.repeat.customers)} customers · 90 days</span>} />
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head">
            <h2>{live ? 'Today so far vs typical' : 'Trend · last 90 days'}</h2>
            {!live && (
              <Seg value={trendMetric} onChange={setTrendMetric} label="Trend metric" options={[
                { value: 'net_revenue', label: 'Revenue' }, { value: 'occupancy', label: 'Occupancy' },
                { value: 'total_activity', label: 'Activity' }]} />
            )}
          </div>
          <div className="card-body">
            {live && data.intraday ? (
              <LineChart
                data={data.intraday.points} x="label" xFormat={(v) => v} format={money} yFormat={moneyShort}
                ariaLabel="Cumulative net revenue today versus same-weekday average"
                series={[
                  { key: 'net_revenue', label: 'Today', color: 'var(--brand)' },
                  { key: 'baseline_net_revenue', label: `Same weekday avg (${data.intraday.baseline_samples} wks)`, color: 'var(--text-3)', dashed: true },
                ]}
              />
            ) : (
              <LineChart
                data={trend} ariaLabel={`Portfolio ${trendMetric} over 90 days`}
                format={trendMetric === 'occupancy' ? (v) => pct(v, 1) : trendMetric === 'net_revenue' ? money : num}
                yFormat={trendMetric === 'occupancy' ? (v) => pct(v) : trendMetric === 'net_revenue' ? moneyShort : num}
                series={[{ key: trendMetric, label: { net_revenue: 'Net revenue', occupancy: 'Occupancy', total_activity: 'Activity' }[trendMetric], color: 'var(--brand)' }]}
              />
            )}
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Statistical insights</h2><span className="hint">vs each venue&apos;s own history</span></div>
          <div className="card-body scroll-y"><Insights items={data.insights} historyDays={data.history_days} /></div>
        </div>
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head"><h2>Venues</h2><span className="hint">{WINDOW_LABEL[win === 'same_weekday' || win === 'prev_day' ? win : 'same_weekday']}</span></div>
          <div className="card-body table-wrap" style={{ paddingTop: 8 }}>
            <table className="table">
              <thead>
                <tr>
                  <th>Venue</th><th className="num">Net revenue</th><th className="num">Occupancy</th>
                  <th className="num">Activity</th><th className="num">Opportunity</th><th>Day close</th>
                </tr>
              </thead>
              <tbody>
                {data.venues.map((r) => (
                  <tr key={r.venue.id} className="clickable" onClick={() => nav(`/venues/${r.venue.id}${date !== todayISO() ? `?date=${date}` : ''}`)}>
                    <td>
                      <Link to={`/venues/${r.venue.id}`} onClick={(e) => e.stopPropagation()} style={{ fontWeight: 600, textDecoration: 'none' }}>
                        {r.venue.name.replace('RallyGully ', '')}
                      </Link>
                      <div className="subtle" style={{ fontSize: 12 }}>
                        {r.hours ? `${time12(r.hours.open_min)} – ${time12(r.hours.close_min)}` : 'Closed today'}
                      </div>
                    </td>
                    <td className="num">
                      <div style={{ fontWeight: 600 }}>{money(r.metrics.net_revenue)}</div>
                      <Delta win={r.comparisons.net_revenue.windows[win] || r.comparisons.net_revenue.windows.same_weekday} />
                    </td>
                    <td className="num" style={{ minWidth: 120 }}>
                      <div style={{ fontWeight: 600 }}>{pct(r.metrics.occupancy)}</div>
                      <Meter value={r.metrics.occupancy} />
                    </td>
                    <td className="num">{num(r.metrics.total_activity)}</td>
                    <td className="num">{money(r.metrics.opportunity)}</td>
                    <td>{closeBadge(r.day_close)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
        <div className="stack">
          <div className="card">
            <div className="card-head"><h2>Channel mix</h2><span className="hint">net revenue</span></div>
            <div className="card-body"><ChannelMix values={m.net_by_kind} title="Net revenue by channel" /></div>
          </div>
          <div className="card">
            <div className="card-head"><h2>Operational exceptions</h2><span className="hint">{data.exceptions.length}</span></div>
            <div className="card-body scroll-y" style={{ maxHeight: 260 }}>
              {data.exceptions.length === 0 ? <p className="subtle">No open exceptions.</p> : (
                <div className="list">
                  {data.exceptions.map((x, i) => (
                    <Link key={i} to={`/venues/${x.venue_id}?date=${x.date}`} className="list-item" style={{ textDecoration: 'none' }}>
                      <AlertTriangle size={16} color={x.type === 'missed_close' ? 'var(--bad)' : 'var(--warn)'} aria-hidden />
                      <div className="grow">
                        <div className="title" style={{ fontSize: 13 }}>{x.message}</div>
                        <div className="meta">{data.venues.find((v) => v.venue.id === x.venue_id)?.venue.name.replace('RallyGully ', '')} · {x.date}</div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}

/** The day at a glance: revenue first, then the aggregates ops reads every few minutes. */
function TodayBand({ data, win, date }) {
  const full = data.metrics || {}
  const live = data.comparison_mode === 'intraday'
  const now = data.metrics_now || full
  const a = full.activities || {}
  const open = data.venues.filter((r) => r.hours).length
  const closed = data.venues.filter((r) => r.day_close === 'closed' || r.day_close === 'closed_late').length
  const cells = [
    { label: 'Collected', value: money(full.collected), sub: full.refunds > 0 ? `${money(full.refunds)} refunded` : 'no refunds' },
    { label: 'To collect', value: money(full.outstanding), sub: full.overdue ? `${full.overdue} overdue` : 'none overdue', tone: full.overdue ? 'bad' : undefined },
    { label: live ? 'Bookings today' : 'Bookings', value: num((a.direct || 0) + (a.district || 0)), sub: `${a.direct || 0} direct · ${a.district || 0} District` },
    { label: 'Community · Academy', value: `${a.community || 0} · ${a.academy || 0}`, sub: `${a.corporate || 0} corporate event${a.corporate === 1 ? '' : 's'}` },
    { label: live ? 'Occupancy so far' : 'Occupancy', value: pct(now.occupancy, 1), sub: `${hours(now.occupied_units)} of ${hours(now.sellable_units)}` },
    { label: 'Customers', value: num(full.unique_customers), sub: `${num(full.returning_customers)} returning` },
    { label: 'Venues', value: `${open}/${data.venues.length} open`, sub: `${closed} day close${closed === 1 ? '' : 's'} done` },
  ]
  return (
    <section className="card today-band" aria-label={live ? 'Today at a glance' : 'Day at a glance'}>
      <div className="today-hero">
        <div className="kpi-label"><IndianRupee size={14} aria-hidden />{live ? "Today's revenue" : `Revenue · ${date}`}</div>
        <div className="today-value">{money(full.net_revenue)}</div>
        <div className="kpi-foot">
          <Delta win={data.comparisons.net_revenue?.windows?.[win]} />
          <span>{WINDOW_LABEL[win]}{live ? ', same time of day' : ''}</span>
        </div>
        <div className="subtle" style={{ fontSize: 12 }}>{money(full.booking_value)} booked value · net of refunds, as collected</div>
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

function minutesOf(label) {
  if (!label) return null
  const [h, m] = label.split(':').map(Number)
  return h * 60 + m
}
