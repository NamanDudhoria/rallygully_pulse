import { useSearchParams } from 'react-router-dom'
import { ChevronRight } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { addDays, hours, money, moneyShort, num, pct, shortDate, todayISO } from '../lib/format'
import { Delta, Empty, ErrorState, Kpi, Meter, PageSkeleton, Seg } from '../components/ui'
import { ChannelMix, LineChart } from '../components/charts'

const RANGES = [
  { value: '7', label: '7 days' }, { value: '30', label: '30 days' }, { value: '90', label: '90 days' },
]

function change(cur, prev) {
  if (prev == null || cur == null) return { available: false, samples: 0, required: 1 }
  return { available: true, samples: 1, baseline: prev, pct_change: prev ? (cur - prev) / prev : null }
}

/** Portfolio → Venue → Sport → Facility, each level against the previous equal-length period. */
export default function Analytics() {
  const [params, setParams] = useSearchParams()
  const range = params.get('range') || '30'
  const venueId = params.get('venue')
  const sport = params.get('sport')
  const to = addDays(todayISO(), -1)
  const from = addDays(to, -(Number(range) - 1))
  const q = new URLSearchParams({ date_from: from, date_to: to })
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
  const prevTotal = data.previous_available ? data.rows.reduce((a, r) => a + (r.previous?.net_revenue || 0), 0) : null

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
          <p>{shortDate(from)} – {shortDate(to)} · compared with {shortDate(data.previous_from)} – {shortDate(data.previous_to)}</p>
        </div>
        <Seg value={range} onChange={(v) => set({ range: v })} options={RANGES} label="Range" />
      </div>

      <div className="kpis">
        <Kpi label="Net revenue" value={money(t.net_revenue)}
          foot={<><Delta win={change(t.net_revenue, sport ? null : prevTotal)} /><span>vs previous period</span></>} />
        <Kpi label="Occupancy" value={pct(t.occupancy, 1)} foot={<span>{hours(t.occupied_units)} of {hours(t.sellable_units)}</span>} />
        <Kpi label="Utilization" value={pct(t.utilization, 1)} foot={<span>incl. {hours(t.blocked_units)} blocked</span>} />
        <Kpi label="Revenue opportunity" value={money(t.opportunity)} foot={<span>{hours(t.unused_units)} unused sellable</span>} />
        <Kpi label="Activity" value={num(t.total_activity)} foot={<span>{t.no_shows} no-shows · {t.cancellations} cancelled</span>} />
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
                  const p = r.previous
                  const dOcc = p && m.occupancy != null && p.occupancy != null ? (m.occupancy - p.occupancy) * 100 : null
                  return (
                    <tr key={r.key} className={data.level !== 'facility' ? 'clickable' : ''} onClick={() => drill(r)}>
                      <td style={{ fontWeight: 600 }}>{String(r.name).replace('RallyGully ', '')}</td>
                      <td className="num">{money(m.net_revenue)}</td>
                      <td className="num"><Delta win={change(m.net_revenue, p?.net_revenue)} /></td>
                      <td className="num" style={{ minWidth: 120 }}>{pct(m.occupancy, 1)}<Meter value={m.occupancy} /></td>
                      <td className="num">
                        {dOcc == null ? <span className="subtle">—</span> : (
                          <span className={`delta ${Math.abs(dOcc) < 0.5 ? 'flat' : dOcc > 0 ? 'up' : 'down'}`}>{dOcc > 0 ? '+' : ''}{dOcc.toFixed(1)}</span>
                        )}
                      </td>
                      <td className="num">{num(m.total_activity)}</td>
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
