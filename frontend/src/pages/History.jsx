/** Sales history imported from the pre-Pulse monthly workbooks (read-only). */
import { Fragment, useMemo, useState } from 'react'
import { AlertTriangle, CheckCircle2, FileSpreadsheet, Info, XCircle } from 'lucide-react'
import { useApi } from '../lib/hooks'
import { money, moneyShort, num, shortDate } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Kpi, PageSkeleton, Seg, Tabs } from '../components/ui'
import { LineChart } from '../components/charts'

const MONTH = (k) => new Date(`${k}-01T00:00:00`).toLocaleDateString('en-IN', { month: 'short', year: '2-digit' })
const FY = (k) => { const [y, m] = k.split('-').map(Number); const s = m >= 4 ? y : y - 1; return `FY${String(s).slice(2)}-${String(s + 1).slice(2)}` }
const VCOLOR = {
  online: '#3b6fd8', offline: '#e2783a', community: '#2f9e6b', academy: '#d9a21b', b2b: '#c94f86', commerce: '#7a5fc0', other: '#7d8590',
}
const SEV = { error: { tone: 'bad', icon: XCircle }, warn: { tone: 'warn', icon: AlertTriangle }, info: { tone: 'info', icon: Info } }

export default function History() {
  const [tab, setTab] = useState('overview')
  return (
    <div className="stack rise">
      <div className="page-head">
        <div>
          <h1>Sales history</h1>
          <p>Imported from the monthly venue sales workbooks — gross revenue as recorded, before GST and commissions.</p>
        </div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'overview', label: 'Overview' }, { value: 'recon', label: 'Tracker reconciliation' },
        { value: 'notes', label: 'Data notes & sources' },
      ]} />
      {tab === 'overview' ? <Overview /> : tab === 'recon' ? <Reconciliation /> : <Notes />}
    </div>
  )
}

function Overview() {
  const { data, error, reload } = useApi('/api/history/overview')
  const [fy, setFy] = useState('all')
  const [open, setOpen] = useState(null)
  const months = useMemo(() => (data?.months || []).filter((k) => fy === 'all' || FY(k) === fy), [data, fy])
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <PageSkeleton />
  if (!data.months.length) return <Empty icon={FileSpreadsheet} title="No history imported yet">Run <code>python -m app.history.ingest</code> with the sales workbooks folder.</Empty>
  const fys = [...new Set(data.months.map(FY))]
  const sum = (obj) => months.reduce((a, k) => a + (obj?.[k] || 0), 0)
  const total = sum(data.per_month)
  const verts = Object.keys(data.by_vertical).sort((a, b) => sum(data.by_vertical[b]) - sum(data.by_vertical[a]))
  const pnl = data.pnl || {}
  const series = months.map((k) => ({ label: MONTH(k), total: data.per_month[k] || 0 }))
  const venues = data.venues.map((v) => ({ ...v, total: months.reduce((a, k) => a + (data.cells[`${v.id}|${k}`] || 0), 0) }))
    .filter((v) => v.total).sort((a, b) => b.total - a.total)
  const maxCell = Math.max(1, ...venues.flatMap((v) => months.map((k) => data.cells[`${v.id}|${k}`] || 0)))
  const best = months.reduce((b, k) => ((data.per_month[k] || 0) > (data.per_month[b] || 0) ? k : b), months[0])

  return (
    <>
      <div className="spread">
        <Seg value={fy} onChange={setFy} label="Financial year" options={[{ value: 'all', label: 'All' }, ...fys.map((f) => ({ value: f, label: f }))]} />
        <span className="subtle" style={{ fontSize: 12.5 }}>{MONTH(months[0])} – {MONTH(months[months.length - 1])} · {venues.length} venues</span>
      </div>
      <div className="kpis">
        <Kpi label="Gross revenue" value={money(total)} foot={<span>{months.length} months · avg {moneyShort(total / months.length)}/month</span>} />
        <Kpi label="Best month" value={MONTH(best)} foot={<span>{money(data.per_month[best])}</span>} />
        {['online', 'offline', 'community'].map((v) => (
          <Kpi key={v} label={data.vertical_labels[v]} value={money(sum(data.by_vertical[v]))}
            foot={<span>{total ? `${((sum(data.by_vertical[v]) / total) * 100).toFixed(1)}% of gross` : ''}</span>} />
        ))}
        <Kpi label="Net profit (stated)" value={money(sum(pnl.profit))} foot={<span>from monthly Summaries that state it</span>} />
      </div>

      <div className="grid g-main">
        <div className="card">
          <div className="card-head"><h2>Monthly gross revenue</h2><span className="hint">all venues</span></div>
          <div className="card-body">
            <LineChart data={series} x="label" xFormat={(v) => v} format={money} yFormat={moneyShort} ariaLabel="Monthly gross revenue"
              series={[{ key: 'total', label: 'Gross revenue', color: 'var(--brand)' }]} />
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Revenue mix</h2><span className="hint">by vertical</span></div>
          <div className="card-body stack" style={{ gap: 10 }}>
            <div style={{ display: 'flex', height: 12, borderRadius: 6, overflow: 'hidden' }}>
              {verts.map((v) => <div key={v} title={data.vertical_labels[v]} style={{ width: `${(sum(data.by_vertical[v]) / total) * 100}%`, background: VCOLOR[v] }} />)}
            </div>
            <table className="table" style={{ fontSize: 13 }}>
              <tbody>{verts.map((v) => (
                <tr key={v}>
                  <td><span className="kind-dot" style={{ background: VCOLOR[v], display: 'inline-block', marginRight: 8 }} />{data.vertical_labels[v]}</td>
                  <td className="num">{money(sum(data.by_vertical[v]))}</td>
                  <td className="num subtle">{((sum(data.by_vertical[v]) / total) * 100).toFixed(1)}%</td>
                </tr>))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><h2>Venue × month</h2><span className="hint">gross · select a cell for the breakdown</span></div>
        <div className="card-body table-wrap">
          <table className="table" style={{ fontSize: 12.5 }}>
            <thead><tr><th>Venue</th>{months.map((k) => <th key={k} className="num nowrap">{MONTH(k)}</th>)}<th className="num">Total</th></tr></thead>
            <tbody>
              {venues.map((v) => (
                <tr key={v.id}>
                  <td className="nowrap" style={{ fontWeight: 600 }}>{v.name.replace('RallyGully ', '')}</td>
                  {months.map((k) => {
                    const x = data.cells[`${v.id}|${k}`]
                    return (
                      <td key={k} className={`num ${x ? 'clickable' : ''}`} onClick={() => x && setOpen({ venue: v.id, month: k })}
                        style={{ background: x ? `color-mix(in srgb, var(--brand) ${Math.round((x / maxCell) * 28)}%, transparent)` : undefined }}>
                        {x ? moneyShort(x) : <span className="subtle">—</span>}
                      </td>
                    )
                  })}
                  <td className="num" style={{ fontWeight: 600 }}>{moneyShort(v.total)}</td>
                </tr>
              ))}
              <tr>
                <td style={{ fontWeight: 600 }}>All venues</td>
                {months.map((k) => <td key={k} className="num" style={{ fontWeight: 600 }}>{moneyShort(data.per_month[k] || 0)}</td>)}
                <td className="num" style={{ fontWeight: 600 }}>{moneyShort(total)}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div className="grid g-2">
        <div className="card">
          <div className="card-head"><h2>By channel</h2><span className="hint">all months shown above</span></div>
          <div className="card-body table-wrap"><ChannelTable data={data} months={months} /></div>
        </div>
        <div className="card">
          <div className="card-head"><h2>P&amp;L (as stated in the Summaries)</h2><span className="hint">sum of venue-months that state each line</span></div>
          <div className="card-body">
            <dl className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
              <dt>Gross revenue</dt><dd className="right">{money(total)}</dd>
              <dt>GST (stated)</dt><dd className="right">{money(sum(pnl.gst))}</dd>
              <dt>Commissions, gateway &amp; event costs (stated)</dt><dd className="right">{money(sum(pnl.commissions))}</dd>
              <dt>Expenses</dt><dd className="right">{money(sum(pnl.expense))}</dd>
              <dt>Net profit (stated)</dt><dd className="right">{money(sum(pnl.profit))}</dd>
              <dt>Partner / school shares</dt><dd className="right">{money(sum(pnl.share))}</dd>
            </dl>
            <p className="subtle" style={{ fontSize: 12, marginTop: 8 }}>Older Summaries don’t state every line, so these are sums of what each file records — not a recomputed P&amp;L.</p>
          </div>
        </div>
      </div>
      {open && <VenueMonth {...open} onClose={() => setOpen(null)} />}
    </>
  )
}

function ChannelTable({ data, months }) {
  // by_channel is all-time; recompute for the visible months from cells is not possible per channel, so show all-time when filtered
  const filtered = months.length !== data.months.length
  return (
    <>
      {filtered && <p className="subtle" style={{ fontSize: 12 }}>Channel totals cover all imported months.</p>}
      <table className="table" style={{ fontSize: 13 }}>
        <thead><tr><th>Channel</th><th>Vertical</th><th className="num">Gross</th></tr></thead>
        <tbody>{data.by_channel.map((c) => (
          <tr key={c.channel}><td>{c.label}</td><td><span className="kind-dot" style={{ background: VCOLOR[c.vertical], display: 'inline-block', marginRight: 6 }} />{data.vertical_labels[c.vertical]}</td><td className="num">{money(c.gross)}</td></tr>
        ))}</tbody>
      </table>
    </>
  )
}

function VenueMonth({ venue, month, onClose }) {
  const { data } = useApi(`/api/history/venue/${venue}/${month}`)
  const [tab, setTab] = useState('channels')
  return (
    <Drawer open onClose={onClose} width={720} title={data ? `${data.venue.name.replace('RallyGully ', '')} · ${MONTH(month)}` : 'Loading…'}
      subtitle={data ? `${money(data.gross)} gross · ${data.sources.map((s) => s.file + (s.sheet ? ` › ${s.sheet}` : '')).join(', ')}` : null}>
      {!data ? <p className="subtle">Loading…</p> : (
        <>
          <Tabs value={tab} onChange={setTab} tabs={[
            { value: 'channels', label: 'Channels' }, { value: 'daily', label: 'Daily', count: data.daily.length },
            { value: 'items', label: 'Items', count: data.lines.length }, { value: 'pnl', label: 'P&L' },
            { value: 'notes', label: 'Notes', count: data.issues.length, alert: data.issues.some((i) => i.severity !== 'info') },
          ]} />
          {tab === 'channels' && (
            <>
              <table className="table" style={{ fontSize: 13 }}>
                <thead><tr><th>Channel</th><th className="num">Gross</th><th className="num">Realised (stated)</th><th className="num">Lines</th></tr></thead>
                <tbody>{data.channels.map((c) => (
                  <tr key={c.channel}><td><span className="kind-dot" style={{ background: VCOLOR[c.vertical], display: 'inline-block', marginRight: 6 }} />{c.label}</td>
                    <td className="num">{money(c.gross)}</td><td className="num">{c.net == null ? <span className="subtle">—</span> : money(c.net)}</td><td className="num subtle">{c.lines}</td></tr>
                ))}</tbody>
              </table>
              <div className="section-title" style={{ marginTop: 14 }}>By sport</div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {data.sports.map((s) => <Badge key={s.sport}>{s.sport}: {money(s.gross)}</Badge>)}
              </div>
            </>
          )}
          {tab === 'daily' && (data.daily.length ? (
            <>
              <LineChart data={data.daily.map((d) => ({ ...d, total: Object.entries(d).filter(([k]) => VCOLOR[k]).reduce((a, [, x]) => a + x, 0) }))}
                format={money} yFormat={moneyShort} ariaLabel="Daily takings recorded day by day"
                series={[{ key: 'total', label: 'Recorded daily takings', color: 'var(--brand)' }]} />
              <p className="subtle" style={{ fontSize: 12 }}>Only channels recorded day by day (offline bookings, community, rentals, F&amp;B). Platform settlements are weekly or monthly — see Items.</p>
              <table className="table" style={{ fontSize: 12.5 }}>
                <thead><tr><th>Date</th><th className="num">Offline</th><th className="num">Community</th><th className="num">Commerce</th><th className="num">Booked hours</th></tr></thead>
                <tbody>{data.daily.map((d) => (
                  <tr key={d.date}><td>{shortDate(d.date)}</td><td className="num">{d.offline ? money(d.offline) : '—'}</td><td className="num">{d.community ? money(d.community) : '—'}</td>
                    <td className="num">{d.commerce ? money(d.commerce) : '—'}</td><td className="num">{d.hours ? num(d.hours) : '—'}</td></tr>
                ))}</tbody>
              </table>
            </>
          ) : <Empty title="No day-by-day records this month" />)}
          {tab === 'items' && (data.lines.length ? (
            <table className="table" style={{ fontSize: 12.5 }}>
              <thead><tr><th>When</th><th>Channel</th><th>Detail</th><th className="num">Gross</th><th className="num">Realised</th></tr></thead>
              <tbody>{data.lines.map((l) => (
                <tr key={l.id}>
                  <td className="nowrap">{l.basis === 'month' ? 'Month' : `${shortDate(l.date_from)}${l.date_to !== l.date_from ? ` – ${shortDate(l.date_to)}` : ''}`}</td>
                  <td>{l.label}{l.sport && <div className="subtle" style={{ fontSize: 11.5 }}>{l.sport}</div>}</td>
                  <td style={{ maxWidth: 260 }}>{l.counterparty && <b>{l.counterparty} </b>}<span className="subtle">{l.description}</span>
                    <div className="subtle mono" style={{ fontSize: 11 }}>{l.sheet}{l.row ? ` · row ${l.row}` : ''}</div></td>
                  <td className="num">{money(l.gross)}</td><td className="num">{l.net == null ? '—' : money(l.net)}</td>
                </tr>
              ))}</tbody>
            </table>
          ) : <Empty title="No itemised lines" />)}
          {tab === 'pnl' && (data.pnl.length ? (
            <dl className="kv" style={{ gridTemplateColumns: '1fr auto' }}>
              {data.pnl.map((p, i) => (
                <Fragment key={i}><dt>{p.label}{p.note && <div className="subtle" style={{ fontSize: 11.5 }}>{p.note}</div>}</dt>
                  <dd className="right">{p.amount == null ? '—' : money(p.amount)}</dd></Fragment>
              ))}
            </dl>
          ) : <Empty title="No P&L lines stated for this month" />)}
          {tab === 'notes' && (data.issues.length ? <IssueList items={data.issues} /> : <Empty title="No data notes for this month" />)}
        </>
      )}
    </Drawer>
  )
}

function IssueList({ items }) {
  return (
    <div className="list">
      {items.map((i, n) => {
        const s = SEV[i.severity] || SEV.info
        return (
          <div key={n} className="list-item" style={{ alignItems: 'flex-start' }}>
            <s.icon size={16} color={`var(--${s.tone})`} aria-hidden style={{ marginTop: 2, flex: 'none' }} />
            <div className="grow">
              <div style={{ fontSize: 13 }}>{i.message}</div>
              <div className="meta">{[i.venue?.replace('RallyGully ', ''), i.month && MONTH(i.month), i.file, i.code].filter(Boolean).join(' · ')}</div>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Notes() {
  const { data, error, reload } = useApi('/api/history/issues')
  const [sev, setSev] = useState('warn')
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <PageSkeleton />
  const shown = data.issues.filter((i) => sev === 'all' || i.severity === sev || (sev === 'warn' && i.severity === 'error'))
  return (
    <div className="grid g-main">
      <div className="card">
        <div className="card-head">
          <h2>Data notes</h2>
          <Seg value={sev} onChange={setSev} label="Severity" options={[{ value: 'warn', label: 'Needs a look' }, { value: 'info', label: 'Info' }, { value: 'all', label: 'All' }]} />
        </div>
        <div className="card-body scroll-y" style={{ maxHeight: 640 }}>{shown.length ? <IssueList items={shown} /> : <Empty title="Nothing here" />}</div>
      </div>
      <div className="card">
        <div className="card-head"><h2>Source files</h2><span className="hint">{data.sources.length}</span></div>
        <div className="card-body scroll-y" style={{ maxHeight: 640 }}>
          <div className="list">
            {data.sources.map((s, n) => (
              <div key={n} className="list-item" style={{ alignItems: 'flex-start' }}>
                <FileSpreadsheet size={16} aria-hidden style={{ marginTop: 2, flex: 'none' }} />
                <div className="grow">
                  <div style={{ fontSize: 12.5, fontWeight: 500 }}>{s.file}{s.sheet ? ` › ${s.sheet}` : ''}</div>
                  <div className="meta">{[s.venue?.replace('RallyGully ', ''), s.month && MONTH(s.month), s.ingested_total != null && money(s.ingested_total)].filter(Boolean).join(' · ')}</div>
                  {s.reason && <div className="meta">{s.reason}</div>}
                </div>
                <Badge tone={s.status === 'ingested' ? 'good' : s.status === 'duplicate' ? undefined : 'warn'}>{s.status}</Badge>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

const STATUS = {
  match: { tone: 'good', label: 'Matches' }, rounding: { tone: 'info', label: 'Rounding' },
  differs: { tone: 'bad', label: 'Differs' }, missing: { tone: 'warn', label: 'Missing' },
}
const signed = (x) => {
  if (x == null) return '—'
  if (Math.abs(x) < 0.005) return '0'
  const a = Math.abs(x)
  return `${x > 0 ? '+' : '−'}${a < 1 ? `₹${a.toFixed(2)}` : money(a)}`
}

function Reconciliation() {
  const { data, error, reload } = useApi('/api/history/reconciliation')
  const [show, setShow] = useState('all')
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <PageSkeleton />
  if (!data.captured) return <Empty icon={FileSpreadsheet} title="No tracker captured">Re-run the import with the folder that holds the B2C Revenue Trackers.</Empty>
  const s = data.summary
  const rows = data.rows.filter((r) => show === 'all' || r.status !== 'match')
  const exTotal = data.excluded.reduce((a, e) => a + e.total, 0)
  return (
    <>
      <div className="kpis">
        <Kpi label="Venue-months compared" value={num(s.venue_months)} foot={<span>Pulse vs the trackers' Central Dashboard</span>} />
        <Kpi label="Match exactly" value={num(s.matching)} foot={<span>to the paisa</span>} />
        <Kpi label="Rounding only" value={num(s.rounding)} foot={<span>within ₹1 — files keep paise, the tracker whole rupees</span>} />
        <Kpi label="Differ" value={num(s.differs + s.pulse_only + s.tracker_only)} foot={<span>{s.differs + s.pulse_only + s.tracker_only ? 'see the rows below' : 'none'}</span>} />
      </div>

      <div className="card">
        <div className="card-head"><h2>Monthly totals</h2><span className="hint">why the tracker's grand total is higher than Pulse</span></div>
        <div className="card-body table-wrap">
          <table className="table" style={{ fontSize: 12.5 }}>
            <thead><tr><th>Month</th><th className="num">Pulse</th><th className="num">Venues not in Pulse</th><th className="num">Pulse + those</th><th className="num">Tracker Total Gross</th><th className="num">Unexplained</th></tr></thead>
            <tbody>
              {data.monthly.map((m) => (
                <tr key={m.month}>
                  <td className="nowrap">{MONTH(m.month)}</td>
                  <td className="num">{money(m.pulse)}</td>
                  <td className="num subtle">{m.excluded ? money(m.excluded) : '—'}</td>
                  <td className="num">{money(m.pulse + m.excluded)}</td>
                  <td className="num">{m.tracker_total == null ? '—' : money(m.tracker_total)}</td>
                  <td className="num" style={{ color: m.unexplained && Math.abs(m.unexplained) > 1 ? 'var(--bad)' : undefined }}>{signed(m.unexplained)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {data.excluded.length > 0 && (
            <p className="subtle" style={{ fontSize: 12.5, marginTop: 10 }}>
              Venues not in Pulse (excluded by HQ, counted in the tracker's total only): {data.excluded.map((e) => `${e.label.replace(/^RallyGully I /, '')} ${money(e.total)}`).join(' · ')} — {money(exTotal)} in all.
            </p>
          )}
        </div>
      </div>

      <div className="card">
        <div className="card-head">
          <h2>Venue × month</h2>
          <Seg value={show} onChange={setShow} label="Show" options={[{ value: 'all', label: 'All' }, { value: 'notexact', label: 'Not exact' }]} />
        </div>
        <div className="card-body table-wrap scroll-y" style={{ maxHeight: 640 }}>
          {rows.length ? (
            <table className="table" style={{ fontSize: 12.5 }}>
              <thead><tr><th>Venue</th><th>Month</th><th className="num">Pulse</th><th className="num">Tracker</th><th className="num">Difference</th><th /></tr></thead>
              <tbody>
                {rows.map((r) => {
                  const st = STATUS[r.status] || STATUS.differs
                  return (
                    <tr key={`${r.venue_id}|${r.month}`}>
                      <td>{r.venue.replace('RallyGully ', '')}</td>
                      <td className="nowrap">{MONTH(r.month)}</td>
                      <td className="num">{r.pulse == null ? '—' : money(r.pulse)}</td>
                      <td className="num">{r.tracker == null ? '—' : money(r.tracker)}</td>
                      <td className="num">{signed(r.diff)}</td>
                      <td>{r.status === 'match' ? <CheckCircle2 size={15} color="var(--good)" aria-label="Matches" /> : <Badge tone={st.tone}>{st.label}</Badge>}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          ) : <Empty title="Every venue-month matches exactly" />}
        </div>
      </div>
    </>
  )
}
