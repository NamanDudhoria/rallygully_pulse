/** HQ: Customers, Configuration, Audit trail, Integrations. */
import { Fragment, useState } from 'react'
import { Mail, MessageCircle, Play, Plus, Repeat, Search, Send } from 'lucide-react'
import { post, put } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/useCtx'
import { dateTime, hhmm, money, phoneFmt, shortDate, todayISO, time12 } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, KindLabel, Skeleton, Tabs } from '../components/ui'

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// ── Customers ────────────────────────────────────────────────────────────────
export function CustomersPage() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const { data, error } = useApi(`/api/customers?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`)
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Customers</h1><p>One profile per phone number across District, Direct and Community Games.</p></div>
        <div style={{ position: 'relative', width: 280 }}>
          <Search size={15} style={{ position: 'absolute', left: 10, top: 11, color: 'var(--text-3)' }} aria-hidden />
          <input className="input" style={{ paddingLeft: 32 }} placeholder="Search phone or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search customers" />
        </div>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} /> : !data ? <Skeleton h={300} /> : !data.length ? <Empty title="No customers found" /> : (
          <table className="table">
            <thead><tr><th>Customer</th><th>Phone</th><th className="num">Completed visits</th><th>Last seen</th><th>Status</th></tr></thead>
            <tbody>{data.map((c) => (
              <tr key={c.id} className="clickable" onClick={() => setOpen(c.id)}>
                <td style={{ fontWeight: 500 }}>{c.name || <span className="subtle">Name not captured</span>}</td>
                <td className="mono">{phoneFmt(c.phone)}</td>
                <td className="num">{c.interactions}</td>
                <td>{c.last_seen ? shortDate(c.last_seen) : '—'}</td>
                <td>{c.returning ? <Badge tone="brand" icon={Repeat}>Returning</Badge> : c.interactions ? <Badge>New</Badge> : <Badge>No visits yet</Badge>}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </div></div>
      {open && <CustomerDrawer id={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function CustomerDrawer({ id, onClose }) {
  const { data: c } = useApi(`/api/customers/${id}`)
  return (
    <Drawer open onClose={onClose} width={540} title={c ? c.name || phoneFmt(c.phone) : 'Customer'} subtitle={c && `${phoneFmt(c.phone)} · since ${shortDate(c.created_at.slice(0, 10))}`}>
      {!c ? <p className="subtle">Loading…</p> : (
        <>
          <div className="row">
            {c.returning ? <Badge tone="brand" icon={Repeat}>Returning customer</Badge> : <Badge>{c.completed ? 'One visit' : 'No completed visits'}</Badge>}
            <Badge>{c.completed} completed</Badge>
            <Badge tone="good">{money(c.net_spend)} spent</Badge>
          </div>
          <p className="subtle" style={{ fontSize: 12.5 }}>Only attended consumer activity counts toward repeat status — cancellations and no-shows are excluded.</p>
          <div className="list">
            {c.history.map((h, i) => (
              <div key={i} className="list-item">
                <KindLabel kind={h.kind} />
                <div className="grow"><div className="title">{h.venue.replace('RallyGully ', '')} · {shortDate(h.date)}</div><div className="meta">{h.time}{h.level ? ` · ${h.level}` : ''} · {h.status === 'cancelled' ? 'cancelled' : h.attendance}</div></div>
                <span style={{ fontWeight: 500 }}>{money(h.amount)}</span>
                {h.counts && <Badge tone="good">counts</Badge>}
              </div>
            ))}
          </div>
        </>
      )}
    </Drawer>
  )
}

// ── Configuration ────────────────────────────────────────────────────────────
export function ConfigPage() {
  const [tab, setTab] = useState('venues')
  return (
    <div className="stack rise">
      <div className="page-head"><div><h1>Configuration</h1><p>Venues, facilities, operating hours, special dates, pricing and people.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'venues', label: 'Venues & hours' }, { value: 'pricing', label: 'Pricing' }, { value: 'users', label: 'People & access' },
      ]} />
      {tab === 'venues' && <VenuesConfig />}
      {tab === 'pricing' && <PricingConfig />}
      {tab === 'users' && <UsersConfig />}
    </div>
  )
}

function VenuesConfig() {
  const { data: venues, reload } = useApi('/api/venues')
  const [sel, setSel] = useState(null)
  const [run, busy] = useAction()
  const [newFac, setNewFac] = useState({ name: '', facility_type: 'Court', sport: '' })
  const [newVenue, setNewVenue] = useState(null)
  if (!venues) return <Skeleton h={300} />
  const v = venues.find((x) => x.id === sel) || venues[0]
  return (
    <div className="grid g-main">
      <div className="stack">
        <div className="row">
          {venues.map((x) => <button key={x.id} className="btn" aria-pressed={x.id === v.id} style={x.id === v.id ? { borderColor: 'var(--brand)', background: 'var(--brand-soft)' } : undefined} onClick={() => setSel(x.id)}>{x.name.replace('RallyGully ', '')}</button>)}
          <button className="btn btn-ghost" onClick={() => setNewVenue({ code: '', name: '', location: '' })}><Plus /> Venue</button>
        </div>
        <div className="card">
          <div className="card-head"><h2>Facilities · {v.code}</h2><span className="hint">sport is derived from the facility</span></div>
          <div className="card-body">
            <table className="table">
              <thead><tr><th>Name</th><th>Type</th><th>Sport</th><th>Status</th><th /></tr></thead>
              <tbody>{v.facilities.map((f) => (
                <tr key={f.id}><td style={{ fontWeight: 500 }}>{f.name}</td><td>{f.facility_type}</td><td>{f.sport}</td>
                  <td>{f.status === 'active' ? <Badge tone="good">Active</Badge> : <Badge>Inactive</Badge>}</td>
                  <td className="num"><button className="btn btn-sm" disabled={busy} onClick={async () => {
                    if (await run(() => put(`/api/facilities/${f.id}`, { ...f, status: f.status === 'active' ? 'inactive' : 'active' }), 'Facility updated')) reload()
                  }}>{f.status === 'active' ? 'Deactivate' : 'Activate'}</button></td></tr>))}
              </tbody>
            </table>
            <form className="row" style={{ marginTop: 12 }} onSubmit={async (e) => {
              e.preventDefault()
              if (await run(() => post('/api/facilities', { ...newFac, venue_id: v.id, sort_order: v.facilities.length }), 'Facility added')) { setNewFac({ name: '', facility_type: 'Court', sport: '' }); reload() }
            }}>
              <input className="input" style={{ width: 130 }} placeholder="Name" value={newFac.name} onChange={(e) => setNewFac({ ...newFac, name: e.target.value })} required aria-label="Facility name" />
              <input className="input" style={{ width: 110 }} placeholder="Type" value={newFac.facility_type} onChange={(e) => setNewFac({ ...newFac, facility_type: e.target.value })} required aria-label="Facility type" />
              <input className="input" style={{ width: 140 }} placeholder="Sport" value={newFac.sport} onChange={(e) => setNewFac({ ...newFac, sport: e.target.value })} required aria-label="Sport" list="sports" />
              <datalist id="sports">{['Pickleball', 'Padel', 'Badminton', 'Football 6v6', 'Box Cricket'].map((s) => <option key={s} value={s} />)}</datalist>
              <button className="btn" disabled={busy}><Plus /> Add facility</button>
            </form>
          </div>
        </div>
        <HoursEditor venue={v} onSaved={reload} />
      </div>
      <SpecialDates venue={v} />
      {newVenue && (
        <Drawer open onClose={() => setNewVenue(null)} title="New venue">
          {(close) => (
            <form className="form" onSubmit={async (e) => { e.preventDefault(); if (await run(() => post('/api/venues', newVenue), 'Venue created')) { reload(); close() } }}>
              <Field label="Code" help="Short code used in District emails, e.g. EOK"><input className="input" value={newVenue.code} onChange={(e) => setNewVenue({ ...newVenue, code: e.target.value })} required /></Field>
              <Field label="Name"><input className="input" value={newVenue.name} onChange={(e) => setNewVenue({ ...newVenue, name: e.target.value })} required /></Field>
              <Field label="Location"><input className="input" value={newVenue.location} onChange={(e) => setNewVenue({ ...newVenue, location: e.target.value })} /></Field>
              <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn btn-primary" disabled={busy}>Create</button></div>
            </form>
          )}
        </Drawer>
      )}
    </div>
  )
}

function HoursEditor({ venue, onSaved }) {
  const [rows, setRows] = useState(null)
  const [run, busy] = useAction()
  const cur = rows?.venue === venue.id ? rows.data : WD.map((_, i) => {
    const h = venue.weekly_hours.find((w) => w.weekday === i)
    return { weekday: i, open: hhmm(h?.open_min ?? 1020), close: h?.close_min === 1440 ? '24:00' : hhmm(h?.close_min ?? 1380), closed: h?.closed ?? false }
  })
  const update = (i, k, val) => setRows({ venue: venue.id, data: cur.map((r, j) => (j === i ? { ...r, [k]: val } : r)) })
  return (
    <div className="card">
      <div className="card-head"><h2>Weekly operating hours</h2><span className="hint">30-minute boundaries</span></div>
      <div className="card-body">
        <table className="table">
          <tbody>{cur.map((r, i) => (
            <tr key={i}><td style={{ width: 70, fontWeight: 500 }}>{WD[i]}</td>
              <td><input className="input" type="time" step={1800} value={r.open} disabled={r.closed} onChange={(e) => update(i, 'open', e.target.value)} aria-label={`${WD[i]} open`} /></td>
              <td><input className="input" type="time" step={1800} value={r.close} disabled={r.closed} onChange={(e) => update(i, 'close', e.target.value)} aria-label={`${WD[i]} close`} /></td>
              <td><label className="check"><input type="checkbox" checked={r.closed} onChange={(e) => update(i, 'closed', e.target.checked)} /> Closed</label></td></tr>))}
          </tbody>
        </table>
        <div className="row" style={{ justifyContent: 'flex-end', marginTop: 10 }}>
          <button className="btn btn-primary" disabled={!rows || busy} onClick={async () => { if (await run(() => put(`/api/venues/${venue.id}/hours`, cur), 'Hours saved')) { setRows(null); onSaved() } }}>Save hours</button>
        </div>
      </div>
    </div>
  )
}

function SpecialDates({ venue }) {
  const { data, reload } = useApi(`/api/venues/${venue.id}/special-dates`)
  const [run, busy] = useAction()
  const [f, setF] = useState({ date: todayISO(), open: '17:00', close: '23:00', closed: false, note: '' })
  return (
    <div className="card" style={{ alignSelf: 'start' }}>
      <div className="card-head"><h2>Special dates</h2><span className="hint">override weekly hours</span></div>
      <div className="card-body stack">
        <form className="form" onSubmit={async (e) => { e.preventDefault(); if (await run(() => post(`/api/venues/${venue.id}/special-dates`, f), 'Override saved')) reload() }}>
          <div className="form-row">
            <Field label="Date"><input className="input" type="date" value={f.date} onChange={(e) => setF({ ...f, date: e.target.value })} /></Field>
            <Field label="Note"><input className="input" placeholder="Diwali" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} /></Field>
          </div>
          <div className="form-row">
            <Field label="Open"><input className="input" type="time" step={1800} value={f.open} disabled={f.closed} onChange={(e) => setF({ ...f, open: e.target.value })} /></Field>
            <Field label="Close"><input className="input" type="time" step={1800} value={f.close} disabled={f.closed} onChange={(e) => setF({ ...f, close: e.target.value })} /></Field>
          </div>
          <div className="spread"><label className="check"><input type="checkbox" checked={f.closed} onChange={(e) => setF({ ...f, closed: e.target.checked })} /> Venue closed</label><button className="btn" disabled={busy}>Save override</button></div>
        </form>
        {data?.length ? (
          <div className="list">{data.map((s) => (
            <div key={s.id} className="list-item"><div className="grow"><div className="title">{shortDate(s.date)} {s.note && <span className="subtle">· {s.note}</span>}</div>
              <div className="meta">{s.closed ? 'Closed' : `${time12(s.open_min)} – ${time12(s.close_min)}`}</div></div></div>))}
          </div>
        ) : <p className="subtle">No overrides.</p>}
      </div>
    </div>
  )
}

function PricingConfig() {
  const venues = useApi('/api/venues').data
  const [vid, setVid] = useState(null)
  const v = venues?.find((x) => x.id === vid) || venues?.[0]
  const { data, reload } = useApi(v ? `/api/pricing?venue_id=${v.id}` : null)
  const [run, busy] = useAction()
  const [f, setF] = useState({ sport: '', facility_id: '', weekdays: '0123456', start: '17:00', end: '23:00', duration_min: 60, amount: '', effective_from: todayISO() })
  if (!v) return <Skeleton h={300} />
  const sports = [...new Set(v.facilities.map((x) => x.sport))]
  return (
    <div className="grid g-main">
      <div className="card">
        <div className="card-head">
          <h2>Pricing rules</h2>
          <select className="select" style={{ width: 200, minHeight: 32 }} value={v.id} onChange={(e) => setVid(Number(e.target.value))} aria-label="Venue">
            {venues.map((x) => <option key={x.id} value={x.id}>{x.name.replace('RallyGully ', '')}</option>)}
          </select>
        </div>
        <div className="card-body table-wrap">
          <table className="table">
            <thead><tr><th>Applies to</th><th>Days</th><th>Time</th><th>Duration</th><th className="num">Price</th><th>Effective from</th></tr></thead>
            <tbody>{(data || []).map((r) => (
              <tr key={r.id}>
                <td>{r.facility_id ? v.facilities.find((x) => x.id === r.facility_id)?.name : r.sport || 'All facilities'}</td>
                <td>{r.weekdays === '0123456' ? 'Every day' : r.weekdays.split('').map((d) => WD[d]).join(', ')}</td>
                <td className="mono">{r.start}–{r.end}</td><td>{r.duration_min} min</td>
                <td className="num" style={{ fontWeight: 600 }}>{money(r.amount)}</td>
                <td>{r.effective_from > todayISO() ? <Badge tone="info">from {shortDate(r.effective_from)}</Badge> : shortDate(r.effective_from)}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      </div>
      <div className="card" style={{ alignSelf: 'start' }}>
        <div className="card-head"><h2>Add price</h2></div>
        <div className="card-body">
          <form className="form" onSubmit={async (e) => {
            e.preventDefault()
            if (await run(() => post('/api/pricing', { ...f, venue_id: v.id, facility_id: f.facility_id ? Number(f.facility_id) : null, sport: f.facility_id ? null : f.sport || null, amount: Number(f.amount), duration_min: Number(f.duration_min) }), 'Price added — existing bookings keep their price')) reload()
          }}>
            <Field label="Sport"><select className="select" value={f.sport} onChange={(e) => setF({ ...f, sport: e.target.value })}><option value="">All sports</option>{sports.map((s) => <option key={s}>{s}</option>)}</select></Field>
            <Field label="Specific facility (optional)"><select className="select" value={f.facility_id} onChange={(e) => setF({ ...f, facility_id: e.target.value })}><option value="">—</option>{v.facilities.map((x) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></Field>
            <Field label="Days" help="Digits 0=Mon … 6=Sun"><input className="input" value={f.weekdays} onChange={(e) => setF({ ...f, weekdays: e.target.value.replace(/[^0-6]/g, '') })} /></Field>
            <div className="form-row">
              <Field label="From"><input className="input" type="time" step={1800} value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} /></Field>
              <Field label="To"><input className="input" type="time" step={1800} value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} /></Field>
            </div>
            <div className="form-row">
              <Field label="Duration"><select className="select" value={f.duration_min} onChange={(e) => setF({ ...f, duration_min: e.target.value })}><option value={60}>60 min</option><option value={30}>30 min</option></select></Field>
              <Field label="Price (₹)"><input className="input" type="number" min={0} value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} required /></Field>
            </div>
            <Field label="Effective from"><input className="input" type="date" value={f.effective_from} onChange={(e) => setF({ ...f, effective_from: e.target.value })} /></Field>
            <button className="btn btn-primary" disabled={busy}>Add rule</button>
          </form>
        </div>
      </div>
    </div>
  )
}

function UsersConfig() {
  const { data, reload } = useApi('/api/users')
  const venues = useApi('/api/venues').data || []
  const [edit, setEdit] = useState(null)
  const [run, busy] = useAction()
  return (
    <div className="card">
      <div className="card-head"><h2>People</h2><button className="btn btn-sm" onClick={() => setEdit({ name: '', email: '', role: 'vm', password: '', venue_ids: [], active: true })}><Plus /> Add person</button></div>
      <div className="card-body table-wrap">
        <table className="table">
          <thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Venues</th><th>Status</th></tr></thead>
          <tbody>{(data || []).map((u) => (
            <tr key={u.id} className="clickable" onClick={() => setEdit({ ...u, password: '' })}>
              <td style={{ fontWeight: 500 }}>{u.name}</td><td>{u.email}</td>
              <td>{u.role === 'hq' ? <Badge tone="brand">HQ</Badge> : <Badge>Venue Manager</Badge>}</td>
              <td>{u.role === 'hq' ? 'All' : u.venue_ids.map((id) => venues.find((v) => v.id === id)?.code).join(', ')}</td>
              <td>{u.active ? <Badge tone="good">Active</Badge> : <Badge>Disabled</Badge>}</td>
            </tr>))}
          </tbody>
        </table>
      </div>
      {edit && (
        <Drawer open onClose={() => setEdit(null)} title={edit.id ? 'Edit person' : 'Add person'}>
          {(close) => (
            <form className="form" onSubmit={async (e) => {
              e.preventDefault()
              const r = await run(() => (edit.id ? put(`/api/users/${edit.id}`, edit) : post('/api/users', edit)), 'Saved')
              if (r) { reload(); close() }
            }}>
              <Field label="Name"><input className="input" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} required /></Field>
              <Field label="Email"><input className="input" type="email" value={edit.email} disabled={!!edit.id} onChange={(e) => setEdit({ ...edit, email: e.target.value })} required /></Field>
              <Field label="Role"><select className="select" value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}><option value="vm">Venue Manager</option><option value="hq">HQ (full access)</option></select></Field>
              {edit.role === 'vm' && (
                <fieldset className="field" style={{ border: 0, padding: 0 }}>
                  <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Assigned venues</span>
                  {venues.map((v) => (
                    <label key={v.id} className="check"><input type="checkbox" checked={edit.venue_ids.includes(v.id)} onChange={(e) => setEdit({ ...edit, venue_ids: e.target.checked ? [...edit.venue_ids, v.id] : edit.venue_ids.filter((x) => x !== v.id) })} />{v.name}</label>
                  ))}
                </fieldset>
              )}
              <Field label={edit.id ? 'New password (optional)' : 'Password'} help="At least 8 characters"><input className="input" type="password" value={edit.password} onChange={(e) => setEdit({ ...edit, password: e.target.value })} required={!edit.id} /></Field>
              {edit.id && <label className="check"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Active</label>}
              <div className="row" style={{ justifyContent: 'flex-end' }}><button className="btn btn-primary" disabled={busy}>Save</button></div>
            </form>
          )}
        </Drawer>
      )}
    </div>
  )
}

// ── Audit ────────────────────────────────────────────────────────────────────
export function AuditPage() {
  const [action, setAction] = useState('')
  const [openRow, setOpenRow] = useState(null)
  const { data, error } = useApi(`/api/audit?limit=300${action ? `&action=${encodeURIComponent(action)}` : ''}`)
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Audit trail</h1><p>Who changed what, before and after. HQ only.</p></div>
        <select className="select" style={{ width: 220 }} value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
          <option value="">All actions</option>
          {['booking.create', 'assign_court', 'reassign', 'payment', 'amount_change', 'cancel', 'refund', 'block', 'day.', 'community', 'academy', 'pricing'].map((a) => <option key={a} value={a}>{a}</option>)}
        </select>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} /> : !data ? <Skeleton h={300} /> : !data.length ? <Empty title="No audit events" /> : (
          <table className="table">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Record</th><th>Change</th></tr></thead>
            <tbody>{data.map((a) => (
              <Fragment key={a.id}>
                <tr className="clickable" onClick={() => setOpenRow(openRow === a.id ? null : a.id)}>
                  <td className="nowrap">{dateTime(a.at)}</td><td>{a.actor}</td>
                  <td><span className="mono">{a.action}</span></td><td>{a.entity} #{a.entity_id}</td>
                  <td className="subtle" style={{ maxWidth: 360, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{summarize(a)}</td>
                </tr>
                {openRow === a.id && (
                  <tr><td colSpan={5}>
                    <div className="grid g-2">
                      <pre className="mono" style={{ margin: 0, whiteSpace: 'pre-wrap', background: 'var(--surface-sunk)', padding: 10, borderRadius: 8 }}>Before {JSON.stringify(a.before, null, 2)}</pre>
                      <pre className="mono" style={{ margin: 0, whiteSpace: 'pre-wrap', background: 'var(--surface-sunk)', padding: 10, borderRadius: 8 }}>After {JSON.stringify(a.after, null, 2)}</pre>
                    </div>
                  </td></tr>
                )}
              </Fragment>))}
            </tbody>
          </table>
        )}
      </div></div>
    </div>
  )
}

function summarize(a) {
  if (!a.before || !a.after) return a.after ? Object.entries(a.after).slice(0, 3).map(([k, v]) => `${k}: ${JSON.stringify(v)}`).join(' · ') : ''
  return Object.keys(a.after).filter((k) => JSON.stringify(a.before[k]) !== JSON.stringify(a.after[k]))
    .map((k) => `${k}: ${JSON.stringify(a.before[k])} → ${JSON.stringify(a.after[k])}`).join(' · ')
}

// ── Integrations ─────────────────────────────────────────────────────────────
const SAMPLE_ID = `DST-${Math.floor(Math.random() * 90000 + 10000)}`
const SAMPLE = `From: bookings@district.in
Subject: Booking Confirmed - ${SAMPLE_ID}

Booking ID: ${SAMPLE_ID}
Venue: EOK
Date: ${todayISO()}
Time: 9:00 PM - 10:00 PM
Amount: Rs. 800
Payment Status: Paid
Phone: +91 98100 12345
`

export function SystemPage() {
  const [tab, setTab] = useState('district')
  const runs = useApi('/api/system/ingestion')
  const wa = useApi('/api/system/notifications?channel=whatsapp')
  const mail = useApi('/api/system/notifications?channel=email')
  const [raw, setRaw] = useState(SAMPLE)
  const [run, busy] = useAction()
  return (
    <div className="stack rise">
      <div className="page-head"><div><h1>Integrations</h1><p>District ingestion (every 5 min), WhatsApp messages and day-close escalations.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'district', label: 'District ingestion', icon: Mail }, { value: 'whatsapp', label: 'WhatsApp', icon: MessageCircle }, { value: 'email', label: 'Escalations', icon: Send },
      ]} />
      {tab === 'district' && (
        <div className="grid g-main">
          <div className="card">
            <div className="card-head"><h2>Recent runs</h2><button className="btn btn-sm" disabled={busy} onClick={async () => { if (await run(() => post('/api/system/district/run'), (r) => `Checked mailbox · ${r.created} new, ${r.duplicates} duplicate`)) runs.reload() }}><Play /> Run now</button></div>
            <div className="card-body table-wrap">
              {!runs.data?.length ? <Empty title="No runs yet">The scheduler checks the mailbox every 5 minutes.</Empty> : (
                <table className="table">
                  <thead><tr><th>Started</th><th>Source</th><th className="num">Emails</th><th className="num">New</th><th className="num">Duplicate</th><th>Errors</th></tr></thead>
                  <tbody>{runs.data.map((r) => (
                    <tr key={r.id}><td>{dateTime(r.started_at)}</td><td>{r.source}</td><td className="num">{r.messages}</td><td className="num">{r.created}</td><td className="num">{r.duplicates}</td>
                      <td>{r.errors?.length ? <Badge tone="bad">{r.errors.length} failed</Badge> : <Badge tone="good">OK</Badge>}{r.errors?.map((e, i) => <div key={i} className="subtle" style={{ fontSize: 12 }}>{e.source}: {e.message}</div>)}</td></tr>))}
                  </tbody>
                </table>
              )}
            </div>
          </div>
          <div className="card" style={{ alignSelf: 'start' }}>
            <div className="card-head"><h2>Test an email</h2><span className="hint">real parser, idempotent</span></div>
            <div className="card-body form">
              <textarea className="textarea mono" rows={12} value={raw} onChange={(e) => setRaw(e.target.value)} aria-label="Raw email" />
              <button className="btn btn-primary" disabled={busy} onClick={() => run(() => post('/api/system/district/simulate', { raw }), (r) => r.result === 'created' ? `Booking #${r.booking_id} created` : `Duplicate — already booking #${r.booking_id}`)}>Ingest</button>
              <p className="subtle" style={{ fontSize: 12.5 }}>Parse failures are engineering issues: they are logged on the run, never shown to VMs.</p>
            </div>
          </div>
        </div>
      )}
      {tab === 'whatsapp' && <NotificationTable q={wa} />}
      {tab === 'email' && <NotificationTable q={mail} />}
    </div>
  )
}

function NotificationTable({ q }) {
  return (
    <div className="card"><div className="card-body table-wrap">
      {!q.data ? <Skeleton h={200} /> : !q.data.length ? <Empty title="Nothing sent yet" /> : (
        <table className="table">
          <thead><tr><th>When</th><th>To</th><th>Template</th><th>Message</th><th>Status</th></tr></thead>
          <tbody>{q.data.map((n) => (
            <tr key={n.id}><td className="nowrap">{dateTime(n.at)}</td><td className="mono">{n.recipient}</td><td>{n.template}</td>
              <td style={{ maxWidth: 420, whiteSpace: 'pre-wrap', fontSize: 12.5 }}>{n.body}</td>
              <td>{n.status === 'sent' ? <Badge tone="good">Sent</Badge> : n.status === 'failed' ? <Badge tone="bad">Failed</Badge> : <Badge tone="info">Simulated</Badge>}{n.error && <div className="subtle" style={{ fontSize: 12 }}>{n.error}</div>}</td></tr>))}
          </tbody>
        </table>
      )}
    </div></div>
  )
}
