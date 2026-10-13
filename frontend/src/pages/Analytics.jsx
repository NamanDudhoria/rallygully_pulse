import { useSearchParams } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { addDays, hours, money, moneyShort, num, pct, shortDate, todayISO } from '../lib/format'
import { Delta, Empty, ErrorState, Kpi, Meter, PageSkeleton, Seg } from '../components/ui'
import { ChannelMix, LineChart } from '../components/charts'

const RANGES = [
  { value: 'mtd', label: 'MTD' }, { value: '7', label: '7D' }, { value: '30', label: '30D' }, { value: '90', label: '90D' },
  { value: 'custom', label: 'Custom' },
]

function change(cur, prev) {
  if (prev == null || cur == null) return { available: false, samples: 0, required: 1 }
  return { available: true, samples: 1, baseline: prev, pct_change: prev ? (cur - prev) / prev : null }
}

/** Completed days only (through yesterday). MTD compares with the same days of the previous month. */
function periodFor(range, params, today = todayISO()) {
  const to = addDays(today, -1)
  if (range === 'custom') {
    const from = params.get('from') || addDays(to, -6)
    const end = params.get('to') || to
    const cf = params.get('cfrom')
    const ct = params.get('cto')
    return { from, to: end < from ? from : end, ...(cf && ct && cf <= ct ? { cmpFrom: cf, cmpTo: ct } : {}) }
  }
  if (range !== 'mtd') return { from: addDays(to, -(Number(range) - 1)), to }
  const [y, m, d] = to.split('-').map(Number)
  const from = `${to.slice(0, 8)}01`
  const pm = m === 1 ? 12 : m - 1
  const py = m === 1 ? y - 1 : y
  const pmDays = new Date(py, pm, 0).getDate()
  const cmpFrom = `${py}-${String(pm).padStart(2, '0')}-01`
  const cmpTo = `${py}-${String(pm).padStart(2, '0')}-${String(Math.min(d, pmDays)).padStart(2, '0')}`
  return { from, to, cmpFrom, cmpTo }
}

function DatePair({ label, from, to, max, onChange }) {
  const style = { width: 150, minHeight: 32, padding: '3px 8px' }
  return (
    <div className="field" style={{ gap: 6 }}>
      <span>{label}</span>
      <div className="row" style={{ gap: 6 }}>
        <input className="input" type="date" aria-label={`${label} from`} value={from} max={to || max} style={style}
          onChange={(e) => onChange(e.target.value, to || e.target.value)} />
        <span className="subtle">to</span>
        <input className="input" type="date" aria-label={`${label} to`} value={to} min={from || undefined} max={max} style={style}
          onChange={(e) => onChange(from || e.target.value, e.target.value)} />
      </div>
    </div>
  )
}

function Pts({ cur, prev }) {
  if (cur == null || prev == null) return <span className="delta na">—</span>
  const v = (cur - prev) * 100
  return <span className={`delta ${Math.abs(v) < 0.5 ? 'flat' : v > 0 ? 'up' : 'down'}`}>{v > 0 ? '+' : ''}{v.toFixed(1)} pts</span>
}

/** Portfolio → Venue → Sport → Facility, each level against the previous equal-length period. */
export default function Analytics() {
  const [params, setParams] = useSearchParams()
  const range = RANGES.some((r) => r.value === params.get('range')) ? params.get('range') : 'mtd'
  const venueId = params.get('venue')
  const sport = params.get('sport')
  const { from, to, cmpFrom, cmpTo } = periodFor(range, params)
  const q = new URLSearchParams({ date_from: from, date_to: to })
  if (cmpFrom) { q.set('compare_from', cmpFrom); q.set('compare_to', cmpTo) }
  if (venueId) q.set('venue_id', venueId)
  if (sport) q.set('sport', sport)
  const { data, error, reload } = useApi(`/api/analytics/drill?${q}`)
  const venues = useApi('/api/venues').data
  const set = (patch) => setParams((p) => {
    const n = new URLSearchParams(p)
    Object.entries(patch).forEach(([k, v]) => (v == null ? n.delete(k) : n.set(k, v)))
    return n
  })
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <PageSkeleton />
  const venueName = venues?.find((v) => String(v.id) === venueId)?.name.replace('RallyGully ', '')
  const t = data.total
  const drill = (row) => {
    if (data.level === 'venue') set({ venue: row.key, sport: null })
    else if (data.level === 'sport') set({ sport: row.key })
  }
  // Different-length comparison (custom ranges): scale additive totals to the same number of days.
  const days = (a, b) => Math.round((Date.parse(b) - Date.parse(a)) / 864e5) + 1
  const ratio = days(data.date_from, data.date_to) / days(data.previous_from, data.previous_to)
  const raw = data.previous_total || {}
  const p = ratio === 1 ? raw : {
    ...raw, net_revenue: raw.net_revenue * ratio, collected: raw.collected * ratio, opportunity: raw.opportunity * ratio,
    total_activity: raw.total_activity * ratio, unique_customers: null,
  }
  const vs = <span>vs {shortDate(data.previous_from)} – {shortDate(data.previous_to)}</span>

  return (
    <div className="stack rise">
      <div className="page-head">
        <div>
          <div className="crumbs">
            <button className="btn btn-ghost btn-sm" onClick={() => set({ venue: null, sport: null })}>Portfolio</button>
            {venueId && <><ChevronRight size={14} /><button className="btn btn-ghost btn-sm" onClick={() => set({ sport: null })}>{venueName}</button></>}
            {sport && <><ChevronRight size={14} /><span style={{ padding: '0 10px' }}>{sport}</span></>}
          </div>
          <h1 style={{ marginTop: 4 }}>Performance</h1>
          <p>
            {range === 'mtd' ? (from.slice(5, 7) === todayISO().slice(5, 7) ? 'Month to date' : 'Last month (no completed days yet this month)')
              : range === 'custom' ? 'Custom range' : `Last ${range} days`}
            {' · '}{shortDate(from)} – {shortDate(to)} · compared with {shortDate(data.previous_from)} – {shortDate(data.previous_to)}
            {range === 'mtd' ? ' (same days last month)' : cmpFrom ? ' (chosen comparison)' : ' (previous period)'}
            {ratio !== 1 && ` · ${days(data.previous_from, data.previous_to)}-day comparison scaled to ${days(data.date_from, data.date_to)} days`}
          </p>
        </div>
        <Seg value={range} onChange={(v) => set(v === 'custom' ? { range: v, from, to } : { range: v, from: null, to: null, cfrom: null, cto: null })}
          options={RANGES} label="Range" />
      </div>
      {range === 'custom' && (
        <div className="card card-pad row" style={{ gap: 16, flexWrap: 'wrap', alignItems: 'flex-end' }}>
          <DatePair label="Period" from={from} to={to} max={todayISO()} onChange={(a, b) => set({ from: a, to: b })} />
          <DatePair label="Compare with" from={cmpFrom || ''} to={cmpTo || ''} max={todayISO()}
            onChange={(a, b) => set({ cfrom: a || null, cto: b || null })} />
          {cmpFrom ? <button className="btn btn-sm btn-ghost" onClick={() => set({ cfrom: null, cto: null })}>Use previous period</button>
            : <span className="subtle" style={{ fontSize: 12.5 }}>Leave blank to compare with the previous period of the same length</span>}
        </div>
      )}

      <div className="kpis">
        <Kpi label="Net revenue" value={money(t.net_revenue)}
          foot={<><Delta win={change(t.net_revenue, p.net_revenue)} />{vs}</>} />
        <Kpi label="Occupancy" value={pct(t.occupancy, 1)}
          foot={<><Pts cur={t.occupancy} prev={p.occupancy} /><span>{hours(t.occupied_units)} of {hours(t.sellable_units)}</span></>} />
        <Kpi label="Utilization" value={pct(t.utilization, 1)}
          foot={<><Pts cur={t.utilization} prev={p.utilization} /><span>incl. {hours(t.blocked_units)} blocked</span></>} />
        <Kpi label="Revenue opportunity" value={money(t.opportunity)}
          foot={<><Delta win={change(t.opportunity, p.opportunity)} invert /><span>{hours(t.unused_units)} unused</span></>} />
        <Kpi label="Activity" value={num(t.total_activity)}
          foot={<><Delta win={change(t.total_activity, p.total_activity)} /><span>{t.no_shows} no-shows · {t.cancellations} cancelled</span></>} />
        <Kpi label="Customers" value={num(t.unique_customers)}
          foot={<><Delta win={change(t.unique_customers, p.unique_customers)} /><span>{num(t.returning_customers)} returning</span></>} />
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head"><h2>Daily net revenue</h2><span className="hint">{venueId ? venueName : 'All venues'}</span></div>
          <div className="card-body">
            <LineChart data={data.series} format={money} yFormat={moneyShort} ariaLabel="Daily net revenue"
              series={[{ key: 'net_revenue', label: 'Net revenue', color: 'var(--brand)' }]} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Channel mix</h2><span className="hint">net revenue</span></div>
          <div className="card-body"><ChannelMix values={t.net_by_kind} title="Channel mix" /></div>
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>By {data.level}</h2>
          <span className="hint">{data.level !== 'facility' ? 'Select a row to drill down' : 'Facility-level'}{!data.previous_available && ' · previous period predates Pulse'}</span>
        </div>
        <div className="card-body table-wrap">
          {data.rows.length === 0 ? <Empty title="No data in this range" /> : (
            <table className="table">
              <thead>
                <tr>
                  <th>{data.level[0].toUpperCase() + data.level.slice(1)}</th>
                  <th className="num">Net revenue</th><th className="num">Δ prev</th>
                  <th className="num">Occupancy</th><th className="num">Δ prev (pts)</th>
                  <th className="num">Activity</th><th className="num">Opportunity</th>
                  {data.level !== 'facility' && <th aria-label="Drill" />}
                </tr>
              </thead>
              <tbody>
                {data.rows.map((r) => {
                  const m = r.metrics
                  const rp = r.previous
                  const dOcc = rp && m.occupancy != null && rp.occupancy != null ? (m.occupancy - rp.occupancy) * 100 : null
                  return (
                    <tr key={r.key} className={data.level !== 'facility' ? 'clickable' : ''} onClick={() => drill(r)}>
                      <td style={{ fontWeight: 600 }}>{String(r.name).replace('RallyGully ', '')}</td>
                      <td className="num">{money(m.net_revenue)}</td>
                      <td className="num"><Delta win={change(m.net_revenue, rp && rp.net_revenue * ratio)} /></td>
                      <td className="num" style={{ minWidth: 120 }}>{pct(m.occupancy, 1)}<Meter value={m.occupancy} /></td>
                      <td className="num">
                        {dOcc == null ? <span className="subtle">—</span> : (
                          <span className={`delta ${Math.abs(dOcc) < 0.5 ? 'flat' : dOcc > 0 ? 'up' : 'down'}`}>{dOcc > 0 ? '+' : ''}{dOcc.toFixed(1)}</span>
                        )}
                      </td>
                      <td className="num">{num(m.total_activity)}<div><Delta win={change(m.total_activity, rp && rp.total_activity * ratio)} /></div></td>
                      <td className="num">{money(m.opportunity)}</td>
                      {data.level !== 'facility' && <td className="num"><ChevronRight size={16} color="var(--text-3)" /></td>}
                    </tr>
                  )
                })}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </div>
  )
}
