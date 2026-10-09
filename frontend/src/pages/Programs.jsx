/** HQ-managed allocations: Community Games, Academy, Corporate/Private events. */
import { useState } from 'react'
import { Plus, Wallet, XCircle } from 'lucide-react'
import { post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/useCtx'
import { METHOD, addDays, money, range12, shortDate, todayISO } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, KindLabel, Seg, Skeleton } from '../components/ui'
import { GameDrawer } from '../components/drawers'
import { payBadge } from '../components/badges'

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
const METHODS = ['cash', 'upi', 'card', 'other'].map((m) => ({ value: m, label: METHOD[m] }))

function VenuePicker({ venues, value, onChange }) {
  return (
    <Field label="Venue">
      <select className="select" value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {venues.map((v) => <option key={v.id} value={v.id}>{v.name.replace('RallyGully ', '')}</option>)}
      </select>
    </Field>
  )
}

function FacilityPicker({ venue, sport, value, onChange }) {
  const list = (venue?.facilities || []).filter((f) => f.status === 'active' && (!sport || f.sport === sport))
  return (
    <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
      <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Facilities</span>
      <div className="row">
        {list.map((f) => (
          <label key={f.id} className="check badge" style={{ height: 32, padding: '0 10px' }}>
            <input type="checkbox" checked={value.includes(f.id)}
              onChange={(e) => onChange(e.target.checked ? [...value, f.id] : value.filter((x) => x !== f.id))} />
            {f.name} <span className="subtle">· {f.sport}</span>
          </label>
        ))}
        {!list.length && <span className="subtle">No matching facilities</span>}
      </div>
    </fieldset>
  )
}

function useVenues() {
  return useApi('/api/venues').data || []
}

// ── Community ────────────────────────────────────────────────────────────────
export function CommunityPage() {
  const venues = useVenues()
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState(null)
  const from = addDays(todayISO(), -30)
  const { data, error, reload } = useApi(`/api/community-games?date_from=${from}`)
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Community Games</h1><p>Lodged by HQ from the Community Team&apos;s plan; VMs record actual players on ground.</p></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={!venues.length}><Plus /> Lodge game</button>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} /> : !data ? <Skeleton h={240} /> : !data.length ? <Empty title="No games in the last 30 days" /> : (
          <table className="table">
            <thead><tr><th>Date</th><th>Game</th><th>Venue · courts</th><th>Players</th><th>State</th><th className="num">Collected</th></tr></thead>
            <tbody>{data.map((g) => (
              <tr key={g.id} className="clickable" onClick={() => setOpen(g.id)}>
                <td className="nowrap">{shortDate(g.date)}<div className="subtle mono" style={{ fontSize: 11.5 }}>{range12(g.start_min, g.end_min)}</div></td>
                <td><KindLabel kind="community">{g.title}</KindLabel><div className="subtle" style={{ fontSize: 12 }}>{g.sport} · {money(g.per_person)}/person</div></td>
                <td>{venues.find((v) => v.id === g.venue_id)?.code} · {g.facilities.join(' + ')}</td>
                <td>{g.attended}/{g.participant_count} <span className="subtle">of {g.capacity}</span></td>
                <td><Badge tone={g.state === 'cancelled' ? 'bad' : g.state === 'live' ? 'good' : undefined}>{g.state}</Badge></td>
                <td className="num">{money(g.collected)}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </div></div>
      {creating && <GameCreate venues={venues} onClose={() => setCreating(false)} onDone={reload} />}
      {open && <GameDrawer id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}

function GameCreate({ venues, onClose, onDone }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ venue_id: venues[0].id, title: 'Community Game', sport: '', date: todayISO(), start: '19:00', end: '21:00', facility_ids: [], capacity: 12, per_person: 400, community_link: 'https://chat.whatsapp.com/' })
  const venue = venues.find((v) => v.id === f.venue_id)
  const sports = [...new Set(venue.facilities.map((x) => x.sport))]
  const sport = f.sport || sports[0]
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e?.target ? e.target.value : e }))
  return (
    <Drawer open onClose={onClose} title="Lodge Community Game" subtitle="Reserves the selected courts for the full duration">
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await run(() => post('/api/community-games', { ...f, sport, capacity: Number(f.capacity), per_person: Number(f.per_person) }), 'Game lodged · courts reserved')
          if (r) { onDone(); close() }
        }}>
          <VenuePicker venues={venues} value={f.venue_id} onChange={(v) => setF((x) => ({ ...x, venue_id: v, facility_ids: [], sport: '' }))} />
          <Field label="Title"><input className="input" value={f.title} onChange={set('title')} /></Field>
          <Field label="Sport">
            <select className="select" value={sport} onChange={(e) => setF((x) => ({ ...x, sport: e.target.value, facility_ids: [] }))}>
              {sports.map((s) => <option key={s}>{s}</option>)}
            </select>
          </Field>
          <div className="form-row">
            <Field label="Date"><input className="input" type="date" value={f.date} onChange={set('date')} /></Field>
            <Field label="Start"><input className="input" type="time" step={1800} value={f.start} onChange={set('start')} /></Field>
            <Field label="End"><input className="input" type="time" step={1800} value={f.end} onChange={set('end')} /></Field>
          </div>
          <FacilityPicker venue={venue} sport={sport} value={f.facility_ids} onChange={set('facility_ids')} />
          <div className="form-row">
            <Field label="Max players"><input className="input" type="number" min={1} value={f.capacity} onChange={set('capacity')} /></Field>
            <Field label="Charge / person (₹)"><input className="input" type="number" min={0} value={f.per_person} onChange={set('per_person')} /></Field>
          </div>
          <Field label="Community joining link" help="Sent automatically on WhatsApp when a VM adds a player"><input className="input" type="url" value={f.community_link} onChange={set('community_link')} required /></Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !f.facility_ids.length}>Lodge game</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

// ── Academy ──────────────────────────────────────────────────────────────────
export function AcademyPage() {
  const venues = useVenues()
  const [creating, setCreating] = useState(false)
  const [open, setOpen] = useState(null)
  const { data, error, reload } = useApi('/api/academy')
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Academy</h1><p>Each occurrence is materialized as its own inventory allocation. Revenue reconciles at month-end.</p></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={!venues.length}><Plus /> New academy booking</button>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} /> : !data ? <Skeleton h={200} /> : !data.length ? <Empty title="No Academy bookings yet" /> : (
          <table className="table">
            <thead><tr><th>Programme</th><th>Venue · facilities</th><th>Schedule</th><th>Occurrences</th><th className="num">Per session</th></tr></thead>
            <tbody>{data.map((s) => (
              <tr key={s.id} className="clickable" onClick={() => setOpen(s.id)}>
                <td><KindLabel kind="academy">{s.name}</KindLabel>{s.status !== 'active' && <Badge>{s.status}</Badge>}</td>
                <td>{venues.find((v) => v.id === s.venue_id)?.code} · {s.facilities.join(' + ')}</td>
                <td>{s.recurring ? s.weekdays.split('').map((d) => WD[d]).join(', ') : 'One-time'} · <span className="mono">{range12(s.start_min, s.end_min)}</span>
                  <div className="subtle" style={{ fontSize: 12 }}>{shortDate(s.start_date)} – {shortDate(s.end_date)}</div></td>
                <td>{s.scheduled} scheduled{s.skipped ? <span className="subtle"> · {s.skipped} skipped</span> : ''}{s.cancelled ? <span className="subtle"> · {s.cancelled} cancelled</span> : ''}</td>
                <td className="num">{money(s.value_per_occurrence)}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </div></div>
      {creating && <AcademyCreate venues={venues} onClose={() => setCreating(false)} onDone={reload} />}
      {open && <AcademyDetail id={open} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}

function AcademyCreate({ venues, onClose, onDone }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ venue_id: venues[0].id, name: '', facility_ids: [], recurring: true, weekdays: '02', start_date: todayISO(), end_date: addDays(todayISO(), 90), start: '17:00', end: '19:00', value_per_occurrence: 1500 })
  const venue = venues.find((v) => v.id === f.venue_id)
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  const toggleDay = (i) => setF((x) => ({ ...x, weekdays: x.weekdays.includes(String(i)) ? x.weekdays.replace(String(i), '') : [...x.weekdays, String(i)].sort().join('') }))
  return (
    <Drawer open onClose={onClose} title="New Academy booking">
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await run(() => post('/api/academy', { ...f, value_per_occurrence: Number(f.value_per_occurrence) }),
            (x) => `${x.scheduled} occurrence(s) scheduled${x.skipped_dates?.length ? ` · ${x.skipped_dates.length} skipped (clash/closed)` : ''}`)
          if (r) { onDone(); close() }
        }}>
          <VenuePicker venues={venues} value={f.venue_id} onChange={(v) => setF((x) => ({ ...x, venue_id: v, facility_ids: [] }))} />
          <Field label="Programme name"><input className="input" value={f.name} onChange={set('name')} required /></Field>
          <FacilityPicker venue={venue} value={f.facility_ids} onChange={set('facility_ids')} />
          <Seg value={f.recurring} onChange={set('recurring')} label="Type" options={[{ value: true, label: 'Recurring' }, { value: false, label: 'One-time' }]} />
          {f.recurring && (
            <div className="row" role="group" aria-label="Weekdays">
              {WD.map((d, i) => (
                <button key={d} type="button" className="btn btn-sm" aria-pressed={f.weekdays.includes(String(i))}
                  style={f.weekdays.includes(String(i)) ? { background: 'var(--brand-soft)', borderColor: 'var(--brand)', color: 'var(--brand-ink)' } : undefined}
                  onClick={() => toggleDay(i)}>{d}</button>
              ))}
            </div>
          )}
          <div className="form-row">
            <Field label={f.recurring ? 'From' : 'Date'}><input className="input" type="date" value={f.start_date} onChange={set('start_date')} /></Field>
            {f.recurring && <Field label="Until"><input className="input" type="date" value={f.end_date} onChange={set('end_date')} /></Field>}
          </div>
          <div className="form-row">
            <Field label="Start"><input className="input" type="time" step={1800} value={f.start} onChange={set('start')} /></Field>
            <Field label="End"><input className="input" type="time" step={1800} value={f.end} onChange={set('end')} /></Field>
            <Field label="Value / session (₹)"><input className="input" type="number" min={0} value={f.value_per_occurrence} onChange={set('value_per_occurrence')} /></Field>
          </div>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !f.facility_ids.length}>Create</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

function AcademyDetail({ id, onClose, onChanged }) {
  const { data: s, reload } = useApi(`/api/academy/${id}`)
  const [run, busy] = useAction()
  const [rec, setRec] = useState({ month: todayISO().slice(0, 7), amount: '', method: 'upi' })
  const act = async (fn, msg) => { if (await run(fn, msg)) { reload(); onChanged() } }
  if (!s) return <Drawer open onClose={onClose} title="Academy"><p className="subtle">Loading…</p></Drawer>
  const upcoming = s.occurrences.filter((o) => o.date >= todayISO()).slice(0, 12)
  return (
    <Drawer open onClose={onClose} width={540} title={<KindLabel kind="academy">{s.name}</KindLabel>} subtitle={`${s.facilities.join(' + ')} · ${range12(s.start_min, s.end_min)}`}>
      <section>
        <div className="section-title">Month-end reconciliation</div>
        <div className="row">
          <input className="input" type="month" value={rec.month} onChange={(e) => setRec({ ...rec, month: e.target.value })} style={{ width: 150 }} aria-label="Month" />
          <input className="input" type="number" min={0} placeholder="Amount collected" value={rec.amount} onChange={(e) => setRec({ ...rec, amount: e.target.value })} style={{ width: 160 }} aria-label="Amount" />
          <select className="select" value={rec.method} onChange={(e) => setRec({ ...rec, method: e.target.value })} style={{ width: 110 }} aria-label="Method">
            {METHODS.map((m) => <option key={m.value} value={m.value}>{m.label}</option>)}
          </select>
          <button className="btn" disabled={!rec.amount || busy} onClick={() => act(() => post(`/api/academy/${s.id}/reconcile`, { ...rec, amount: Number(rec.amount) }), 'Month reconciled')}><Wallet /> Save</button>
        </div>
        {s.reconciliations.length > 0 && (
          <table className="table" style={{ marginTop: 10 }}>
            <tbody>{s.reconciliations.map((r) => <tr key={r.month}><td>{r.month}</td><td className="num">{money(r.amount_collected)}</td><td>{METHOD[r.payment_method]}</td></tr>)}</tbody>
          </table>
        )}
      </section>
      <section>
        <div className="section-title">Upcoming occurrences</div>
        {upcoming.length === 0 ? <p className="subtle">None.</p> : (
          <div className="list">{upcoming.map((o) => (
            <div key={o.id} className="list-item">
              <div className="grow"><div className="title">{shortDate(o.date)} · <span className="mono">{range12(o.start_min, o.end_min)}</span></div><div className="meta">{o.status}{o.note ? ` · ${o.note}` : ''}</div></div>
              {o.status === 'scheduled' && <button className="btn btn-sm" disabled={busy} onClick={() => act(() => post(`/api/academy/occurrences/${o.id}/cancel`, { note: 'Cancelled by HQ' }), 'Occurrence cancelled — court released')}>Cancel</button>}
            </div>))}
          </div>
        )}
      </section>
      {s.status === 'active' && (
        <section>
          <button className="btn btn-danger" disabled={busy} onClick={() => act(() => post(`/api/academy/${s.id}/end`, { from_date: todayISO() }), 'Series ended — future courts released')}><XCircle /> End series from today</button>
        </section>
      )}
    </Drawer>
  )
}

// ── Corporate / Private ──────────────────────────────────────────────────────
export function EventsPage() {
  const venues = useVenues()
  const [creating, setCreating] = useState(false)
  const { data, error, reload } = useApi('/api/events')
  const [run, busy] = useAction()
  const [method, setMethod] = useState('other')
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Corporate / Private events</h1><p>Revenue-generating allocations; count toward occupancy.</p></div>
        <button className="btn btn-primary" onClick={() => setCreating(true)} disabled={!venues.length}><Plus /> New event</button>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} /> : !data ? <Skeleton h={200} /> : !data.length ? <Empty title="No events yet" /> : (
          <table className="table">
            <thead><tr><th>Date</th><th>Client</th><th>Venue · facilities</th><th>Payment</th><th className="num">Amount</th><th /></tr></thead>
            <tbody>{data.map((e) => (
              <tr key={e.id}>
                <td className="nowrap">{shortDate(e.date)}<div className="subtle mono" style={{ fontSize: 11.5 }}>{range12(e.start_min, e.end_min)}</div></td>
                <td><KindLabel kind="corporate">{e.company}</KindLabel><div className="subtle" style={{ fontSize: 12 }}>{e.contact_name} · {e.contact_phone}</div></td>
                <td>{venues.find((v) => v.id === e.venue_id)?.code} · {e.facilities.join(' + ') || '—'}</td>
                <td>{e.status === 'cancelled' ? <Badge tone="bad">Cancelled</Badge> : payBadge(e.payment_status)}</td>
                <td className="num">{money(e.amount)}</td>
                <td className="num">
                  {e.status !== 'cancelled' && e.payment_status !== 'paid' && (
                    <button className="btn btn-sm" disabled={busy} onClick={async () => { if (await run(() => post(`/api/events/${e.id}/pay`, { method }), 'Payment recorded')) reload() }}>Mark paid</button>
                  )}
                </td>
              </tr>))}
            </tbody>
          </table>
        )}
        <div className="row" style={{ marginTop: 10 }}><span className="subtle" style={{ fontSize: 12.5 }}>Method used for “Mark paid”:</span><Seg value={method} onChange={setMethod} options={METHODS} label="Method" /></div>
      </div></div>
      {creating && <EventCreate venues={venues} onClose={() => setCreating(false)} onDone={reload} />}
    </div>
  )
}

function EventCreate({ venues, onClose, onDone }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ venue_id: venues[0].id, company: '', contact_name: '', contact_phone: '', facility_ids: [], date: todayISO(), start: '16:00', end: '19:00', amount: '', paid: false, method: 'other' })
  const venue = venues.find((v) => v.id === f.venue_id)
  const set = (k) => (e) => setF((x) => ({ ...x, [k]: e?.target ? (e.target.type === 'checkbox' ? e.target.checked : e.target.value) : e }))
  return (
    <Drawer open onClose={onClose} title="New Corporate / Private event">
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await run(() => post('/api/events', { ...f, amount: Number(f.amount), method: f.paid ? f.method : null }), 'Event created · facilities reserved')
          if (r) { onDone(); close() }
        }}>
          <VenuePicker venues={venues} value={f.venue_id} onChange={(v) => setF((x) => ({ ...x, venue_id: v, facility_ids: [] }))} />
          <Field label="Company / client"><input className="input" value={f.company} onChange={set('company')} required /></Field>
          <div className="form-row">
            <Field label="Contact person"><input className="input" value={f.contact_name} onChange={set('contact_name')} required /></Field>
            <Field label="Phone"><input className="input" inputMode="tel" value={f.contact_phone} onChange={set('contact_phone')} required /></Field>
          </div>
          <FacilityPicker venue={venue} value={f.facility_ids} onChange={set('facility_ids')} />
          <div className="form-row">
            <Field label="Date"><input className="input" type="date" value={f.date} onChange={set('date')} /></Field>
            <Field label="Start"><input className="input" type="time" step={1800} value={f.start} onChange={set('start')} /></Field>
            <Field label="End"><input className="input" type="time" step={1800} value={f.end} onChange={set('end')} /></Field>
          </div>
          <Field label="Total amount (₹)"><input className="input" type="number" min={0} value={f.amount} onChange={set('amount')} required /></Field>
          <label className="check"><input type="checkbox" checked={f.paid} onChange={set('paid')} /> Already paid</label>
          {f.paid && <Seg value={f.method} onChange={set('method')} options={METHODS} label="Method" />}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !f.facility_ids.length}>Create event</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}
