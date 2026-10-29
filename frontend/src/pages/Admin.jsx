/** HQ: Customers, Configuration, Audit trail, Integrations. */
import { Fragment, useState } from 'react'
import { AlertTriangle, Mail, MessageCircle, Pencil, Play, Plus, Repeat, Search, Send, Trash2 } from 'lucide-react'
import { del, post, put } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/useCtx'
import { dateTime, hhmm, money, phoneFmt, shortDate, todayISO, time12 } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, KindLabel, Skeleton, Tabs } from '../components/ui'
import { getFix } from '../lib/attendance'

const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

// ── Customers ────────────────────────────────────────────────────────────────
export function CustomersPage() {
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null)
  const { data, error } = useApi(`/api/customers?limit=200${q ? `&q=${encodeURIComponent(q)}` : ''}`)
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Customers</h1></div>
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
      <div className="page-head"><div><h1>Configuration</h1></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'venues', label: 'Venues & hours' }, { value: 'pricing', label: 'Pricing' }, { value: 'memberships', label: 'Memberships' },
      ]} />
      {tab === 'venues' && <VenuesConfig />}
      {tab === 'pricing' && <PricingConfig />}
      {tab === 'memberships' && <MembershipsConfig />}
    </div>
  )
}

function VenuesConfig() {
  const { data: venues, reload } = useApi('/api/venues')
  const [sel, setSel] = useState(null)
  const [run, busy] = useAction()
  const [newFac, setNewFac] = useState({ name: '', facility_type: 'Court', sport: '' })
  const [venueForm, setVenueForm] = useState(null) // {} for new, venue for edit
  const [facForm, setFacForm] = useState(null)
  if (!venues) return <Skeleton h={300} />
  if (!venues.length) {
    return (
      <div className="card"><Empty title="No venues yet">
        <button className="btn btn-primary" style={{ marginTop: 8 }} onClick={() => setVenueForm({ code: '', name: '', location: '', status: 'active' })}><Plus /> Add first venue</button>
      </Empty>{venueForm && <VenueDrawer form={venueForm} onClose={() => setVenueForm(null)} onSaved={(nv) => { reload(); setSel(nv.id) }} />}</div>
    )
  }
  const v = venues.find((x) => x.id === sel) || venues[0]
  return (
    <div className="grid g-main">
      <div className="stack">
        <div className="row">
          {venues.map((x) => (
            <button key={x.id} className="btn" aria-pressed={x.id === v.id} onClick={() => setSel(x.id)}
              style={x.id === v.id ? { borderColor: 'var(--brand)', background: 'var(--brand-soft)' } : undefined}>
              {x.name.replace('RallyGully ', '')}{x.status !== 'active' && <span className="subtle"> · inactive</span>}
            </button>))}
          <button className="btn btn-ghost" onClick={() => setVenueForm({ code: '', name: '', location: '', status: 'active' })}><Plus /> Venue</button>
        </div>
        <div className="card card-pad">
          <div className="spread">
            <div>
              <h2>{v.name} <span className="mono subtle" style={{ fontWeight: 400 }}>{v.code}</span></h2>
              <p className="muted" style={{ marginTop: 4 }}>{v.location || 'No location set'}</p>
              {v.latitude == null && <p className="subtle" style={{ fontSize: 12.5, color: 'var(--warn)' }}>No map position: VMs can&apos;t clock in here yet</p>}
            </div>
            <div className="row">
              {v.status === 'active' ? <Badge tone="good">Active</Badge> : <Badge>Inactive</Badge>}
              <button className="btn btn-sm" onClick={() => setVenueForm(v)}><Pencil /> Edit details</button>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><h2>Facilities</h2></div>
          <div className="card-body">
            {!v.facilities.length && <p className="subtle" style={{ marginBottom: 8 }}>No facilities yet.</p>}
            {v.facilities.length > 0 && (
              <table className="table">
                <thead><tr><th>Name</th><th>Type</th><th>Sport</th><th>Status</th><th /></tr></thead>
                <tbody>{v.facilities.map((f) => (
                  <tr key={f.id}><td style={{ fontWeight: 500 }}>{f.name}</td><td>{f.facility_type}</td><td>{f.sport}</td>
                    <td>{f.status === 'active' ? <Badge tone="good">Active</Badge> : <Badge>Inactive</Badge>}</td>
                    <td className="num"><button className="btn btn-sm" onClick={() => setFacForm(f)}><Pencil /> Edit</button></td></tr>))}
                </tbody>
              </table>
            )}
            <form className="row" style={{ marginTop: 12 }} onSubmit={async (e) => {
              e.preventDefault()
              if (await run(() => post('/api/facilities', { ...newFac, venue_id: v.id, sort_order: v.facilities.length }), 'Facility added')) { setNewFac({ name: '', facility_type: 'Court', sport: '' }); reload() }
            }}>
              <input className="input" style={{ width: 130 }} placeholder="Name" value={newFac.name} onChange={(e) => setNewFac({ ...newFac, name: e.target.value })} required aria-label="Facility name" />
              <input className="input" style={{ width: 110 }} placeholder="Type" value={newFac.facility_type} onChange={(e) => setNewFac({ ...newFac, facility_type: e.target.value })} required aria-label="Facility type" />
              <input className="input" style={{ width: 140 }} placeholder="Sport" value={newFac.sport} onChange={(e) => setNewFac({ ...newFac, sport: e.target.value })} required aria-label="Sport" list="sports" />
              <datalist id="sports">{SPORTS.map((x) => <option key={x} value={x} />)}</datalist>
              <button className="btn" disabled={busy}><Plus /> Add facility</button>
            </form>
          </div>
        </div>
        <HoursEditor venue={v} onSaved={reload} />
      </div>
      <SpecialDates venue={v} />
      {venueForm && <VenueDrawer form={venueForm} onClose={() => setVenueForm(null)} onSaved={(nv) => { reload(); setSel(nv.id) }} />}
      {facForm && <FacilityDrawer facility={facForm} onClose={() => setFacForm(null)} onSaved={reload} />}
    </div>
  )
}

const SPORTS = ['Pickleball', 'Padel', 'Badminton', 'Football 6v6', 'Box Cricket']

/** "28.5571, 77.2481" → [lat, lng]; '' → null (not set); anything else → undefined (invalid). */
function parsePosition(text) {
  if (!text.trim()) return null
  const m = text.trim().match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/)
  if (!m) return undefined
  const lat = Number(m[1])
  const lng = Number(m[2])
  return Math.abs(lat) <= 90 && Math.abs(lng) <= 180 ? [lat, lng] : undefined
}

function VenueDrawer({ form, onClose, onSaved }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ code: form.code, name: form.name, location: form.location || '', status: form.status || 'active', community_link: form.community_link || '' })
  const [rentals, setRentals] = useState((form.rental_items || []).map((x) => ({ id: x.id, name: x.name, price: String(x.price) })))
  const [discounts, setDiscounts] = useState((form.discounts || []).map((d) => ({ id: d.id, name: d.name, percent: String(d.percent) })))
  const [pos, setPos] = useState(form.latitude != null ? `${form.latitude}, ${form.longitude}` : '')
  const [locating, setLocating] = useState(false)
  const isNew = !form.id
  const coords = parsePosition(pos)
  return (
    <Drawer open onClose={onClose} title={isNew ? 'New venue' : `Edit ${form.name}`}
      subtitle={isNew ? 'Starts with 5–11 PM daily hours; add facilities and prices next' : 'Changes are audited'}>
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          if (coords === undefined) return
          const body = { ...f, latitude: coords?.[0] ?? null, longitude: coords?.[1] ?? null,
            rental_items: rentals.filter((x) => x.name.trim() || x.price).map((x) => ({ id: x.id ?? null, name: x.name, price: Number(x.price) })),
            discounts: discounts.filter((d) => d.name.trim() || d.percent).map((d) => ({ id: d.id ?? null, name: d.name, percent: Number(d.percent) })) }
          const r = await run(() => (isNew ? post('/api/venues', body) : put(`/api/venues/${form.id}`, body)), isNew ? 'Venue created' : 'Venue updated')
          if (r) { onSaved(r); close() }
        }}>
          <Field label="Code" help={isNew ? 'Short code, e.g. EOK. District emails are matched by this code or the venue name.' : 'District emails are matched by code or name — update District if you change it.'}>
            <input className="input mono" value={f.code} onChange={(e) => setF({ ...f, code: e.target.value.toUpperCase() })} required maxLength={20} />
          </Field>
          <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
          <Field label="Location"><input className="input" value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} placeholder="Area, city" /></Field>
          <Field label="Map position" error={coords === undefined ? 'Enter latitude, longitude, e.g. 28.5571, 77.2481' : null}
            help="Venue Managers can clock in only near this point. In Google Maps, right-click the venue and click the numbers to copy them.">
            <div className="row" style={{ gap: 8 }}>
              <input className="input mono grow" value={pos} onChange={(e) => setPos(e.target.value)} placeholder="28.5571, 77.2481" inputMode="decimal" />
              <button type="button" className="btn" disabled={locating} onClick={async () => {
                setLocating(true)
                const fix = await run(() => getFix())
                setLocating(false)
                if (fix) setPos(`${fix.latitude.toFixed(6)}, ${fix.longitude.toFixed(6)}`)
              }}>{locating ? 'Locating…' : 'Use my location'}</button>
            </div>
          </Field>
          <Field label="WhatsApp community group link" help="Sent in this venue's booking confirmations. Leave blank to use the default link.">
            <input className="input" type="url" value={f.community_link} onChange={(e) => setF({ ...f, community_link: e.target.value })} placeholder="https://chat.whatsapp.com/…" />
          </Field>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Discounts</span>
            <span className="help subtle" style={{ fontSize: 12 }}>Prices at this venue are fixed. Venue Managers can only apply one of these percentage discounts when booking; only HQ can type a different amount.</span>
            {discounts.map((d, i) => (
              <div key={d.id ?? `n${i}`} className="row" style={{ gap: 8 }}>
                <input className="input grow" value={d.name} maxLength={60} placeholder="Name, e.g. Member" aria-label="Discount name"
                  onChange={(e) => setDiscounts(discounts.map((x, k) => (k === i ? { ...x, name: e.target.value } : x)))} />
                <input className="input mono" style={{ width: 90 }} type="number" min={1} max={100} step="any" placeholder="%" aria-label="Percent off"
                  value={d.percent} onChange={(e) => setDiscounts(discounts.map((x, k) => (k === i ? { ...x, percent: e.target.value } : x)))} />
                <button type="button" className="btn btn-ghost btn-sm" aria-label="Remove discount" onClick={() => setDiscounts(discounts.filter((_, k) => k !== i))}><Trash2 /></button>
              </div>
            ))}
            <div><button type="button" className="btn btn-sm" onClick={() => setDiscounts([...discounts, { id: null, name: '', percent: '' }])}><Plus /> Add discount</button></div>
          </fieldset>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Equipment rental prices</span>
            <span className="help subtle" style={{ fontSize: 12 }}>Fixed prices for equipment hired at this venue (racket, shoes, balls…). Venue Managers pick an item and a quantity; only HQ changes prices here.</span>
            {rentals.map((x, i) => (
              <div key={x.id ?? `n${i}`} className="row" style={{ gap: 8 }}>
                <input className="input grow" value={x.name} maxLength={60} placeholder="Item, e.g. Racket" aria-label="Item name"
                  onChange={(e) => setRentals(rentals.map((y, k) => (k === i ? { ...y, name: e.target.value } : y)))} />
                <input className="input mono" style={{ width: 100 }} type="number" min={1} step="any" placeholder="₹ price" aria-label="Price"
                  value={x.price} onChange={(e) => setRentals(rentals.map((y, k) => (k === i ? { ...y, price: e.target.value } : y)))} />
                <button type="button" className="btn btn-ghost btn-sm" aria-label="Remove item" onClick={() => setRentals(rentals.filter((_, k) => k !== i))}><Trash2 /></button>
              </div>
            ))}
            <div><button type="button" className="btn btn-sm" onClick={() => setRentals([...rentals, { id: null, name: '', price: '' }])}><Plus /> Add item</button></div>
          </fieldset>
          {!isNew && (
            <Field label="Status" help="A venue can be made inactive only when it has no upcoming bookings or allocations.">
              <select className="select" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                <option value="active">Active</option><option value="inactive">Inactive</option>
              </select>
            </Field>
          )}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy}>{isNew ? 'Create venue' : 'Save'}</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

function FacilityDrawer({ facility, onClose, onSaved }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ ...facility })
  const risky = f.status !== facility.status || f.sport !== facility.sport
  return (
    <Drawer open onClose={onClose} title={`Edit ${facility.name}`} subtitle={`${facility.facility_type} · ${facility.sport}`}>
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          if (await run(() => put(`/api/facilities/${facility.id}`, { ...f, sort_order: Number(f.sort_order) || 0 }), 'Facility updated')) { onSaved(); close() }
        }}>
          <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
          <div className="form-row">
            <Field label="Type"><input className="input" value={f.facility_type} onChange={(e) => setF({ ...f, facility_type: e.target.value })} required /></Field>
            <Field label="Sport"><input className="input" list="sports-edit" value={f.sport} onChange={(e) => setF({ ...f, sport: e.target.value })} required /></Field>
            <datalist id="sports-edit">{SPORTS.map((x) => <option key={x} value={x} />)}</datalist>
          </div>
          <div className="form-row">
            <Field label="Display order"><input className="input" type="number" value={f.sort_order} onChange={(e) => setF({ ...f, sort_order: e.target.value })} /></Field>
            <Field label="Status">
              <select className="select" value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                <option value="active">Active</option><option value="inactive">Inactive</option>
              </select>
            </Field>
          </div>
          {risky && <div className="callout warn"><AlertTriangle />Changing the sport or deactivating is only allowed when this facility has no upcoming bookings or allocations. Past records keep their history.</div>}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy}>Save</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

function HoursEditor({ venue, onSaved }) {
  const [rows, setRows] = useState(null)
  const [bulk, setBulk] = useState(false)
  const [run, busy] = useAction()
  const cur = rows?.venue === venue.id ? rows.data : WD.map((_, i) => {
    const h = venue.weekly_hours.find((w) => w.weekday === i)
    return { weekday: i, open: hhmm(h?.open_min ?? 1020), close: h?.close_min === 1440 ? '24:00' : hhmm(h?.close_min ?? 1380), closed: h?.closed ?? false }
  })
  const update = (i, k, val) => setRows({ venue: venue.id, data: cur.map((r, j) => (j === i ? { ...r, [k]: val } : r)) })
  return (
    <div className="card">
      <div className="card-head"><h2>Weekly operating hours</h2></div>
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
          <button className="btn" disabled={busy} onClick={() => setBulk(true)}>Apply to other venues</button>
          <button className="btn btn-primary" disabled={!rows || busy} onClick={async () => { if (await run(() => put(`/api/venues/${venue.id}/hours`, cur), 'Hours saved')) { setRows(null); onSaved() } }}>Save hours</button>
        </div>
      </div>
      {bulk && <BulkHours from={venue} hours={cur} onClose={() => setBulk(false)} onSaved={() => { setBulk(false); setRows(null); onSaved() }} />}
    </div>
  )
}

function BulkHours({ from, hours, onClose, onSaved }) {
  const venues = useApi('/api/venues').data || []
  const [chosen, setChosen] = useState(null) // null until the user changes it: every venue is ticked
  const [run, busy] = useAction()
  const picked = chosen || new Set(venues.map((v) => v.id))
  const all = venues.length > 0 && picked.size === venues.length
  const toggle = (id) => setChosen(() => { const n = new Set(picked); n.has(id) ? n.delete(id) : n.add(id); return n })
  return (
    <Drawer open onClose={onClose} title="Apply these hours to venues"
      footer={<div className="row" style={{ justifyContent: 'flex-end' }}>
        <button type="button" className="btn" onClick={onClose}>Cancel</button>
        <button type="button" className="btn btn-primary" disabled={busy || !picked.size}
          onClick={async () => { if (await run(() => post('/api/hours/bulk', { venue_ids: [...picked], hours }), `Hours applied to ${picked.size} venues`)) onSaved() }}>
          Apply to {picked.size} venue{picked.size === 1 ? '' : 's'}
        </button></div>}>
      <div className="stack">
        <label className="check"><input type="checkbox" checked={all} onChange={() => setChosen(all ? new Set() : new Set(venues.map((v) => v.id)))} /> All venues</label>
        {venues.map((v) => (
          <label key={v.id} className="check">
            <input type="checkbox" checked={picked.has(v.id)} onChange={() => toggle(v.id)} /> {v.name.replace('RallyGully ', '')}{v.id === from.id && <span className="subtle"> · source</span>}
          </label>
        ))}
      </div>
    </Drawer>
  )
}

function SpecialDates({ venue }) {
  const { data, reload } = useApi(`/api/venues/${venue.id}/special-dates`)
  const [run, busy] = useAction()
  const [f, setF] = useState({ date: todayISO(), open: '17:00', close: '23:00', closed: false, note: '' })
  return (
    <div className="card" style={{ alignSelf: 'start' }}>
      <div className="card-head"><h2>Special dates</h2></div>
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
              <div className="meta">{s.closed ? 'Closed' : `${time12(s.open_min)} – ${time12(s.close_min)}`}</div></div>
              {s.date >= todayISO() && (
                <button className="btn btn-sm btn-ghost btn-icon" disabled={busy} aria-label={`Remove override for ${s.date}`}
                  onClick={async () => { if (await run(() => del(`/api/special-dates/${s.id}`), 'Override removed — weekly hours apply')) reload() }}><Trash2 /></button>
              )}</div>))}
          </div>
        ) : <p className="subtle">No overrides.</p>}
      </div>
    </div>
  )
}

const MEMBER_STATUS = { active: ['good', 'Active'], expired: ['warn', 'Expired'], used_up: ['info', 'Hours used up'], cancelled: ['bad', 'Cancelled'] }
const PAY_METHODS = [['cash', 'Cash'], ['upi', 'UPI'], ['razorpay', 'Razorpay'], ['other', 'Other']]

/** Memberships: hours one person bought in a single payment. Their bookings are covered from the balance. */
function MembershipsConfig() {
  const [q, setQ] = useState('')
  const { data, error, reload } = useApi(`/api/memberships${q ? `?q=${encodeURIComponent(q)}` : ''}`)
  const venues = useApi('/api/venues').data
  const [open, setOpen] = useState(null) // 'new' | membership id
  const code = (id) => venues?.find((v) => v.id === id)?.code || id
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  return (
    <div className="card">
      <div className="card-head">
        <h2>Memberships</h2>
        <div className="row">
          <input className="input" style={{ width: 200, minHeight: 32 }} placeholder="Search name or phone" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search memberships" />
          <button className="btn btn-primary" onClick={() => setOpen('new')}><Plus /> New membership</button>
        </div>
      </div>
      <div className="card-body table-wrap">
        {!data ? <Skeleton h={160} /> : data.length === 0 ? <Empty title="No memberships yet">When someone buys hours in one payment, add it here. Their bookings are then covered automatically.</Empty> : (
          <table className="table">
            <thead><tr><th>Member</th><th>Venues</th><th className="num">Hours left</th><th className="num">Paid</th><th>Valid until</th><th>Status</th></tr></thead>
            <tbody>{data.map((m) => {
              const [tone, label] = MEMBER_STATUS[m.status] || ['info', m.status]
              return (
                <tr key={m.id} style={{ cursor: 'pointer' }} onClick={() => setOpen(m.id)}>
                  <td><div style={{ fontWeight: 500 }}>{m.customer.name}</div><div className="subtle" style={{ fontSize: 12 }}>{phoneFmt(m.customer.phone)}</div></td>
                  <td className="mono">{m.venue_ids.map(code).join(', ')}</td>
                  <td className="num"><b>{m.left_hours}</b> <span className="subtle">of {m.total_hours}</span></td>
                  <td className="num">{money(m.amount_paid)}</td>
                  <td className="nowrap">{m.expires_on ? shortDate(m.expires_on) : 'No expiry'}</td>
                  <td><Badge tone={tone}>{label}</Badge></td>
                </tr>
              )
            })}</tbody>
          </table>
        )}
      </div>
      {open === 'new' && venues && <MembershipDrawer venues={venues} onClose={() => setOpen(null)} onSaved={() => { setOpen(null); reload() }} />}
      {open && open !== 'new' && <MembershipDetail id={open} venues={venues || []} onClose={() => setOpen(null)} onChanged={reload} />}
    </div>
  )
}

function MembershipDrawer({ venues, onClose, onSaved }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ phone: '', name: '', venue_ids: [], hours: '', amount_paid: '', method: 'upi', sold_on: todayISO(), starts_on: todayISO(), expires_on: '', notes: '' })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value })
  return (
    <Drawer open onClose={onClose} title="New membership" subtitle="Hours bought in one payment, for one person">
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await run(() => post('/api/memberships', {
            ...f, hours: Number(f.hours), amount_paid: Number(f.amount_paid), expires_on: f.expires_on || null, notes: f.notes || null,
          }), 'Membership added. Their bookings are now covered.')
          if (r) { onSaved(); close() }
        }}>
          <div className="form-row">
            <Field label="Phone" help="This is how the member is recognised"><input className="input" inputMode="tel" autoComplete="off" placeholder="98xxxxxxxx" value={f.phone} onChange={set('phone')} required /></Field>
            <Field label="Name"><input className="input" value={f.name} onChange={set('name')} required /></Field>
          </div>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Valid at</span>
            <div className="row">
              {venues.map((v) => (
                <label key={v.id} className="check badge" style={{ height: 32, padding: '0 10px' }}>
                  <input type="checkbox" checked={f.venue_ids.includes(v.id)}
                    onChange={(e) => setF({ ...f, venue_ids: e.target.checked ? [...f.venue_ids, v.id] : f.venue_ids.filter((x) => x !== v.id) })} />
                  {v.name.replace('RallyGully ', '')}
                </label>
              ))}
            </div>
          </fieldset>
          <div className="form-row">
            <Field label="Hours bought" help="In steps of 0.5"><input className="input" type="number" min={0.5} step={0.5} value={f.hours} onChange={set('hours')} required /></Field>
            <Field label="Amount paid (₹)"><input className="input" type="number" min={0} step="any" value={f.amount_paid} onChange={set('amount_paid')} required /></Field>
          </div>
          <div className="form-row">
            <Field label="Paid by"><select className="select" value={f.method} onChange={set('method')}>{PAY_METHODS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></Field>
            <Field label="Sold on"><input className="input" type="date" value={f.sold_on} onChange={set('sold_on')} required /></Field>
          </div>
          <div className="form-row">
            <Field label="Starts on"><input className="input" type="date" value={f.starts_on} onChange={set('starts_on')} required /></Field>
            <Field label="Expires on (optional)"><input className="input" type="date" value={f.expires_on} onChange={set('expires_on')} /></Field>
          </div>
          <Field label="Notes (optional)"><input className="input" maxLength={200} value={f.notes} onChange={set('notes')} /></Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !f.venue_ids.length}>Add membership</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

function MembershipDetail({ id, venues, onClose, onChanged }) {
  const { data: m, reload } = useApi(`/api/memberships/${id}`)
  const [run, busy] = useAction()
  const code = (vid) => venues.find((v) => v.id === vid)?.code || vid
  if (!m) return <Drawer open onClose={onClose} title="Membership"><p className="subtle">Loading…</p></Drawer>
  const [tone, label] = MEMBER_STATUS[m.status] || ['info', m.status]
  return (
    <Drawer open onClose={onClose} title={m.customer.name} subtitle={`${phoneFmt(m.customer.phone)} · ${m.venue_ids.map(code).join(', ')}`}>
      <div className="row"><Badge tone={tone}>{label}</Badge></div>
      <dl className="kv">
        <dt>Hours left</dt><dd><b>{m.left_hours}</b> of {m.total_hours}</dd>
        <dt>Played</dt><dd>{m.used_hours} h</dd>
        <dt>Booked, not played yet</dt><dd>{m.reserved_hours} h</dd>
        <dt>Paid</dt><dd>{money(m.amount_paid)} · {PAY_METHODS.find(([v]) => v === m.payment_method)?.[1] || m.payment_method} · {shortDate(m.sold_on)}</dd>
        <dt>Valid</dt><dd>{shortDate(m.starts_on)} to {m.expires_on ? shortDate(m.expires_on) : 'no expiry'}</dd>
        {m.notes && <><dt>Notes</dt><dd>{m.notes}</dd></>}
      </dl>
      <section>
        <div className="section-title">Hours used</div>
        {m.uses.length ? m.uses.map((u) => (
          <div key={u.booking_id} className="row" style={{ justifyContent: 'space-between' }}>
            <span>{shortDate(u.used_on)} · {code(u.venue_id)} · {u.time}</span><b>{u.hours} h</b>
          </div>
        )) : <p className="subtle">No hours used yet.</p>}
      </section>
      {m.status !== 'cancelled' && (
        <div><button className="btn" disabled={busy} onClick={async () => {
          if (!window.confirm('Cancel this membership? Their remaining hours will no longer cover bookings.')) return
          if (await run(() => post(`/api/memberships/${m.id}/cancel`), 'Membership cancelled')) { reload(); onChanged() }
        }}>Cancel membership</button></div>
      )}
    </Drawer>
  )
}

function PricingConfig() {
  const venues = useApi('/api/venues').data
  const [vid, setVid] = useState(null)
  const v = venues?.find((x) => x.id === vid) || venues?.[0]
  const { data, reload } = useApi(v ? `/api/pricing?venue_id=${v.id}` : null)
  const [run, busy] = useAction()
  const [f, setF] = useState({ sport: '', facility_id: '', weekdays: '0123456', start: '17:00', end: '23:00', duration_min: 60, amount: '', effective_from: todayISO() })
  const [showOld, setShowOld] = useState(false)
  const [change, setChange] = useState(null)
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
            <thead><tr><th>Applies to</th><th>Days</th><th>Time</th><th>Duration</th><th className="num">Price</th><th>From</th><th>Status</th><th /></tr></thead>
            <tbody>{(data || []).filter((r) => showOld || r.status !== 'superseded').map((r) => (
              <tr key={r.id} style={r.status === 'superseded' ? { opacity: 0.6 } : undefined}>
                <td>{r.facility_id ? v.facilities.find((x) => x.id === r.facility_id)?.name : r.sport || 'All facilities'}</td>
                <td>{r.weekdays === '0123456' ? 'Every day' : r.weekdays.split('').map((d) => WD[d]).join(', ')}</td>
                <td className="mono">{r.start}–{r.end}</td><td>{r.duration_min} min</td>
                <td className="num" style={{ fontWeight: 600 }}>{money(r.amount)}</td>
                <td className="nowrap">{shortDate(r.effective_from)}</td>
                <td>{r.status === 'current' ? <Badge tone="good">Current</Badge> : r.status === 'scheduled' ? <Badge tone="info">Scheduled</Badge> : <Badge>Superseded</Badge>}</td>
                <td className="num nowrap">
                  {r.status !== 'superseded' && <button className="btn btn-sm" onClick={() => setChange({ rule: r, amount: r.amount, effective_from: todayISO() })}>Change price</button>}
                  {r.status === 'scheduled' && <button className="btn btn-sm btn-ghost btn-icon" aria-label="Delete scheduled price" disabled={busy}
                    onClick={async () => { if (await run(() => del(`/api/pricing/${r.id}`), 'Scheduled price removed')) reload() }}><Trash2 /></button>}
                </td>
              </tr>))}
            </tbody>
          </table>
          <label className="check" style={{ marginTop: 8 }}><input type="checkbox" checked={showOld} onChange={(e) => setShowOld(e.target.checked)} /> Show superseded prices</label>
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
      {change && (
        <Drawer open onClose={() => setChange(null)} title="Change price"
          subtitle={`${change.rule.facility_id ? v.facilities.find((x) => x.id === change.rule.facility_id)?.name : change.rule.sport || 'All facilities'} · ${change.rule.start}–${change.rule.end} · ${change.rule.duration_min} min`}>
          {(close) => (
            <form className="form" onSubmit={async (e) => {
              e.preventDefault()
              if (await run(() => post(`/api/pricing/${change.rule.id}/change`, { amount: Number(change.amount), effective_from: change.effective_from }),
                'New price scheduled — existing bookings keep their price')) { reload(); close() }
            }}>
              <Field label="New price (₹)" help={`Currently ${money(change.rule.amount)}`}>
                <input className="input" type="number" min={0} value={change.amount} onChange={(e) => setChange({ ...change, amount: e.target.value })} required />
              </Field>
              <Field label="Effective from" help="Applies to bookings created from this date. Bookings already made keep their price (PRD §11.1).">
                <input className="input" type="date" min={todayISO()} value={change.effective_from} onChange={(e) => setChange({ ...change, effective_from: e.target.value })} required />
              </Field>
              <div className="row" style={{ justifyContent: 'flex-end' }}>
                <button type="button" className="btn" onClick={close}>Cancel</button>
                <button className="btn btn-primary" disabled={busy}>Schedule price</button>
              </div>
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
        <div><h1>Audit trail</h1></div>
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
      <div className="page-head"><div><h1>Integrations</h1></div></div>
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
            <div className="card-head"><h2>Test an email</h2></div>
            <div className="card-body form">
              <textarea className="textarea mono" rows={12} value={raw} onChange={(e) => setRaw(e.target.value)} aria-label="Raw email" />
              <button className="btn btn-primary" disabled={busy} onClick={() => run(() => post('/api/system/district/simulate', { raw }), (r) => r.result === 'created' ? `Booking #${r.booking_id} created` : `Duplicate — already booking #${r.booking_id}`)}>Ingest</button>
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
