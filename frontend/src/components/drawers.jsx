import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Check, Clock, UserCheck, UserX, Wallet, XCircle, RotateCcw, Undo2, Trash2 } from 'lucide-react'
import { api, del, patch, post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { KIND, METHOD, dateTime, hhmm, money, phoneFmt, range12, time12 } from '../lib/format'
import { Badge, Drawer, Field, KindLabel, Seg } from './ui'
import { attendBadge, payBadge } from './badges'

const METHODS = ['cash', 'upi', 'card', 'other'].map((m) => ({ value: m, label: METHOD[m] }))

const digitsOf = (p) => p.replace(/\D/g, '')

function useReasons() {
  return useApi('/api/meta/reasons').data
}

/** District/Direct booking: court assignment, attendance, payment, cancellation, refunds. */
export function BookingDrawer({ id, onClose, onChanged }) {
  const { isHQ } = useAuth()
  const { data: b, reload } = useApi(`/api/bookings/${id}`)
  const reasons = useReasons()
  const [run, busy] = useAction()
  const [method, setMethod] = useState('upi')
  const [fac, setFac] = useState('')
  const [cancelReason, setCancelReason] = useState('')
  const [note, setNote] = useState('')
  const [windowEnd, setWindowEnd] = useState('')
  const [amount, setAmount] = useState('')
  const [refund, setRefund] = useState({ amount: '', reason: '' })
  const free = useApi(b && b.status === 'confirmed'
    ? `/api/venues/${b.venue_id}/free?date=${b.date}&start=${hhmm(b.start_min)}&end=${hhmm(b.end_min)}` : null).data

  const act = async (fn, msg) => {
    const r = await run(fn, msg)
    if (r) { await reload(); onChanged?.() }
    return r
  }
  if (!b) return <Drawer open onClose={onClose} title="Booking"><p className="subtle">Loading…</p></Drawer>

  const live = b.status === 'confirmed'
  const unpaid = live && ['to_collect', 'overdue'].includes(b.payment_status) && b.attendance !== 'no_show'
  const sameSport = (free || []).filter((f) => !b.sport || b.source === 'district' && !b.facility_id || f.sport === b.sport)

  return (
    <Drawer open onClose={onClose} width={500}
      title={<span className="row" style={{ gap: 10 }}><KindLabel kind={b.source} />{b.customer.name || phoneFmt(b.customer.phone)}</span>}
      subtitle={`${range12(b.start_min, b.end_min)} · ${b.date}${b.external_ref ? ` · ${b.external_ref}` : ''}`}>
      <div className="row">{attendBadge(b)}{live && b.attendance !== 'no_show' && payBadge(b.payment_status)}</div>

      <dl className="kv">
        <dt>Customer</dt><dd>{b.customer.name || <span className="subtle">Name not captured</span>} · {phoneFmt(b.customer.phone)}</dd>
        <dt>Court</dt><dd>{b.facility || <span className="subtle">Not assigned yet</span>}{b.sport ? ` · ${b.sport}` : ''}</dd>
        <dt>Booking value</dt><dd>{money(b.booking_value)}</dd>
        <dt>Collected</dt><dd>{money(b.collected_amount)}{b.payment_method ? ` · ${METHOD[b.payment_method] || b.payment_method}` : ''}</dd>
        {b.refunded > 0 && <><dt>Refunded</dt><dd>{money(b.refunded)}</dd></>}
        {unpaid && <><dt>Payment window</dt><dd>{time12(b.payment_window_start_min)} – {time12(b.payment_window_end_min)}</dd></>}
        {b.cancel_reason && <><dt>Cancelled</dt><dd>{reasons?.booking_cancel?.[b.cancel_reason] || b.cancel_reason}{b.cancel_note ? ` — ${b.cancel_note}` : ''}</dd></>}
      </dl>

      {live && b.source === 'district' && !b.facility_id && b.attendance === 'pending' && (
        <section>
          <div className="section-title">Customer arrived — assign their court</div>
          <div className="row">
            <select className="select" style={{ flex: 1 }} value={fac} onChange={(e) => setFac(e.target.value)} aria-label="Court">
              <option value="">Choose a free court…</option>
              {(free || []).map((f) => <option key={f.id} value={f.id}>{f.name} · {f.sport}</option>)}
            </select>
            <button className="btn btn-primary" disabled={!fac || busy}
              onClick={() => act(() => post(`/api/bookings/${b.id}/assign`, { facility_id: Number(fac) }), 'Court assigned — marked attended')}>
              <UserCheck /> Assign
            </button>
          </div>
          {free && free.length === 0 && <p className="help subtle" style={{ marginTop: 6, fontSize: 12.5 }}>No court is free for this full slot.</p>}
        </section>
      )}

      {live && b.source === 'direct' && b.attendance === 'pending' && (
        <section className="row">
          <button className="btn btn-primary" disabled={busy} onClick={() => act(() => post(`/api/bookings/${b.id}/attended`), 'Checked in')}>
            <UserCheck /> Customer arrived
          </button>
        </section>
      )}

      {live && b.attendance === 'pending' && (
        <section>
          <button className="btn" disabled={busy || !b.can_no_show}
            onClick={() => act(() => post(`/api/bookings/${b.id}/no-show`), 'Marked no-show')}>
            <UserX /> Mark no-show
          </button>
          {!b.can_no_show && <span className="subtle" style={{ marginLeft: 10, fontSize: 12.5 }}>Available from {time12(b.no_show_from_min)}</span>}
        </section>
      )}

      {live && b.facility_id && b.attendance !== 'no_show' && (
        <section>
          <div className="section-title">Move to another court</div>
          <div className="row">
            <select className="select" style={{ flex: 1 }} value={fac} onChange={(e) => setFac(e.target.value)} aria-label="New court">
              <option value="">Choose a court…</option>
              {sameSport.filter((f) => f.id !== b.facility_id).map((f) => <option key={f.id} value={f.id}>{f.name} · {f.sport}</option>)}
            </select>
            <button className="btn" disabled={!fac || busy}
              onClick={() => act(() => post(`/api/bookings/${b.id}/reassign`, { facility_id: Number(fac) }), 'Court changed')}>
              <RotateCcw /> Move
            </button>
          </div>
        </section>
      )}

      {unpaid && (
        <section>
          <div className="section-title">Collect payment · {money(b.booking_value)} (full amount)</div>
          <div className="row">
            <Seg value={method} onChange={setMethod} options={METHODS} label="Payment method" />
            <button className="btn btn-primary" disabled={busy}
              onClick={() => act(() => post(`/api/bookings/${b.id}/pay`, { method }), `Payment recorded · ${METHOD[method]}`)}>
              <Wallet /> Record
            </button>
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <input className="input" type="time" step={1800} value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)}
              style={{ width: 130 }} aria-label="New payment window end" />
            <button className="btn btn-sm" disabled={!windowEnd || busy}
              onClick={() => act(() => post(`/api/bookings/${b.id}/extend-window`, { end: windowEnd }), 'Payment window extended')}>
              <Clock /> Extend window
            </button>
          </div>
        </section>
      )}

      {live && (b.attendance !== 'attended' || isHQ) && (
        <section>
          <div className="section-title">Cancel</div>
          {b.payment_status === 'overdue' ? (
            <p className="subtle" style={{ fontSize: 13, marginBottom: 8 }}>Payment is overdue — cancelling releases the court; no reason needed.</p>
          ) : (
            <p className="subtle" style={{ fontSize: 13, marginBottom: 8 }}>Only for issues attributable to RallyGully. To reschedule, cancel and create a new booking.</p>
          )}
          <div className="form">
            {b.payment_status !== 'overdue' && (
              <select className="select" value={cancelReason} onChange={(e) => setCancelReason(e.target.value)} aria-label="Cancellation reason">
                <option value="">Reason…</option>
                {Object.entries(reasons?.booking_cancel || {}).filter(([k]) => k !== 'payment_overdue').map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            )}
            {cancelReason === 'other' && <input className="input" placeholder="Note" value={note} onChange={(e) => setNote(e.target.value)} />}
            <div>
              <button className="btn btn-danger" disabled={busy || (b.payment_status !== 'overdue' && !cancelReason)}
                onClick={() => act(() => post(`/api/bookings/${b.id}/cancel`, { reason: cancelReason || 'payment_overdue', note }), 'Booking cancelled')}>
                <XCircle /> {b.payment_status === 'overdue' ? 'Cancel & release court' : 'Cancel booking'}
              </button>
            </div>
          </div>
        </section>
      )}

      {(isHQ || b.payment_status !== 'paid') && live && (
        <section>
          <div className="section-title">Adjust amount{isHQ && b.payment_status === 'paid' ? ' (HQ correction)' : ''}</div>
          <div className="row">
            <input className="input" type="number" min={0} placeholder={String(b.booking_value)} value={amount}
              onChange={(e) => setAmount(e.target.value)} style={{ width: 140 }} aria-label="Amount" />
            <button className="btn btn-sm" disabled={amount === '' || busy}
              onClick={() => act(() => post(`/api/bookings/${b.id}/amount`, { amount: Number(amount) }), 'Amount updated')}>Save</button>
          </div>
        </section>
      )}

      {isHQ && b.collected_amount - b.refunded > 0 && (
        <section>
          <div className="section-title">Refund (applies to {b.date})</div>
          <div className="form">
            <div className="row">
              <input className="input" type="number" min={1} max={b.collected_amount - b.refunded} placeholder={`Up to ${b.collected_amount - b.refunded}`}
                value={refund.amount} onChange={(e) => setRefund({ ...refund, amount: e.target.value })} style={{ width: 150 }} aria-label="Refund amount" />
              <input className="input" placeholder="Reason" value={refund.reason} onChange={(e) => setRefund({ ...refund, reason: e.target.value })} style={{ flex: 1 }} aria-label="Refund reason" />
            </div>
            <div>
              <button className="btn" disabled={!refund.amount || !refund.reason || busy}
                onClick={() => act(() => post(`/api/bookings/${b.id}/refund`, { amount: Number(refund.amount), reason: refund.reason }), 'Refund recorded')}>
                <Undo2 /> Record refund
              </button>
            </div>
          </div>
        </section>
      )}

      {b.assignments?.length > 0 && (
        <section>
          <div className="section-title">Court history</div>
          <div className="timeline">
            {b.assignments.map((a, i) => (
              <div key={i} className={`t-item ${a.current ? 'current' : ''}`}>
                <b>{a.facility}</b> <span className="subtle">assigned {dateTime(a.assigned_at)}{a.released_at ? ` · released ${dateTime(a.released_at)}` : a.current ? ' · current' : ''}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      {b.refunds?.length > 0 && (
        <section>
          <div className="section-title">Refunds</div>
          {b.refunds.map((r) => <div key={r.id} style={{ fontSize: 13 }}>{money(r.amount)} · {r.reason} <span className="subtle">· {dateTime(r.processed_at)}</span></div>)}
        </section>
      )}
    </Drawer>
  )
}

/** Direct on-ground booking created from an available slot (PRD §20). */
export function DirectBookingDrawer({ venue, date, facilityId, start, onClose, onDone }) {
  const facilities = venue.facilities.filter((f) => f.status === 'active')
  const [form, setForm] = useState({ facility_id: facilityId, date, start: hhmm(start), duration: 60, phone: '', name: '', amount: '', pay_now: false, method: 'upi' })
  const [quote, setQuote] = useState(null)
  const [lookup, setLookup] = useState({ phone: '', customer: null })
  const existing = digitsOf(form.phone) === lookup.phone ? lookup.customer : null
  const [run, busy] = useAction()
  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v?.target ? (v.target.type === 'checkbox' ? v.target.checked : v.target.value) : v }))

  useEffect(() => {
    const q = `/api/venues/${venue.id}/quote?facility_id=${form.facility_id}&date=${form.date}&start=${form.start}&duration=${form.duration}`
    api(q).then(setQuote).catch(() => setQuote(null))
  }, [venue.id, form.facility_id, form.date, form.start, form.duration])

  useEffect(() => {
    const digits = digitsOf(form.phone)
    if (digits.length < 10) return
    api(`/api/customers/lookup?phone=${encodeURIComponent(form.phone)}`).then((r) => {
      setLookup({ phone: digits, customer: r.found ? r.customer : null })
      if (r.found && r.customer.name) setForm((f) => ({ ...f, name: r.customer.name }))
    }).catch(() => {})
  }, [form.phone])

  const submit = async (e, close) => {
    e.preventDefault()
    const r = await run(() => post('/api/bookings/direct', {
      ...form, venue_id: venue.id, facility_id: Number(form.facility_id), duration: Number(form.duration),
      amount: form.amount === '' ? null : Number(form.amount), method: form.pay_now ? form.method : null,
    }), 'Direct booking created · WhatsApp sent')
    if (r) { onDone?.(r); close() }
  }
  const fac = facilities.find((f) => f.id === Number(form.facility_id))

  return (
    <Drawer open onClose={onClose} title="New Direct booking" subtitle={`${venue.name} · on-ground`}>
      {(close) => (
        <form className="form" onSubmit={(e) => submit(e, close)}>
          <div className="form-row">
            <Field label="Facility">
              <select className="select" value={form.facility_id} onChange={set('facility_id')}>
                {facilities.map((f) => <option key={f.id} value={f.id}>{f.name} · {f.sport}</option>)}
              </select>
            </Field>
            <Field label="Date"><input className="input" type="date" value={form.date} onChange={set('date')} /></Field>
          </div>
          <div className="form-row">
            <Field label="Start"><input className="input" type="time" step={1800} value={form.start} onChange={set('start')} /></Field>
            <Field label="Duration">
              <Seg value={Number(form.duration)} onChange={set('duration')} options={[{ value: 30, label: '30 min' }, { value: 60, label: '60 min' }]} label="Duration" />
            </Field>
          </div>
          {quote && !quote.available && <div className="callout bad"><AlertTriangle />This facility is not free for that time.</div>}
          <Field label="Customer phone" help={existing ? `Existing customer${existing.name_locked ? ' — name locked' : ''}` : 'Phone is the customer identity'}>
            <input className="input" inputMode="tel" autoComplete="off" placeholder="98xxxxxxxx" value={form.phone} onChange={set('phone')} required />
          </Field>
          <Field label="Customer name">
            <input className="input" value={form.name} onChange={set('name')} required disabled={existing?.name_locked} />
          </Field>
          <Field label="Amount" help={quote?.amount != null ? `Configured price ${money(quote.amount)} for ${fac?.sport} — edit for a discount` : 'No price configured — enter the amount'}>
            <input className="input" type="number" min={0} placeholder={quote?.amount ?? ''} value={form.amount} onChange={set('amount')} />
          </Field>
          <label className="check"><input type="checkbox" checked={form.pay_now} onChange={set('pay_now')} /> Paid now</label>
          {form.pay_now && <Seg value={form.method} onChange={set('method')} options={METHODS} label="Payment method" />}
          {!form.pay_now && <p className="subtle" style={{ fontSize: 12.5 }}>Reserve now — collect between 30 min before start and 30 min after end.</p>}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || (quote && !quote.available)}>Create booking</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

/** Create an Operational Block, or view/release an existing one. */
export function BlockDrawer({ venue, date, facilityId, start, blockId, onClose, onDone }) {
  const reasons = useReasons()
  const [run, busy] = useAction()
  const { data: blocks } = useApi(blockId ? `/api/blocks?venue_id=${venue.id}&date_from=${date}` : null)
  const blk = blocks?.find((b) => b.id === blockId)
  const [form, setForm] = useState({
    facility_id: facilityId || venue.facilities[0]?.id, date, start: hhmm(start ?? 17 * 60),
    end: hhmm((start ?? 17 * 60) + 60), reason: 'maintenance', note: '',
  })
  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))
  if (blockId) {
    return (
      <Drawer open onClose={onClose} title={<KindLabel kind="block">Operational Block</KindLabel>}
        subtitle={blk ? `${blk.facility} · ${range12(blk.start_min, blk.end_min)}` : ''}>
        {(close) => blk ? (
          <>
            <dl className="kv">
              <dt>Reason</dt><dd>{reasons?.block?.[blk.reason] || blk.reason}</dd>
              {blk.note && <><dt>Note</dt><dd>{blk.note}</dd></>}
              <dt>Created</dt><dd>{dateTime(blk.created_at)}</dd>
              <dt>Status</dt><dd>{blk.released_at ? `Released ${dateTime(blk.released_at)}` : 'Active'}</dd>
            </dl>
            <p className="subtle" style={{ fontSize: 13 }}>Blocked time is excluded from sellable inventory and earns no revenue.</p>
            {!blk.released_at && (
              <div><button className="btn" disabled={busy} onClick={async () => {
                const r = await run(() => post(`/api/blocks/${blk.id}/release`), 'Block released — court available again')
                if (r) { onDone?.(); close() }
              }}><Undo2 /> Release early</button></div>
            )}
          </>
        ) : <p className="subtle">Loading…</p>}
      </Drawer>
    )
  }
  return (
    <Drawer open onClose={onClose} title="Block a facility" subtitle="Takes effect immediately">
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await run(() => post('/api/blocks', { ...form, venue_id: venue.id, facility_id: Number(form.facility_id) }), 'Facility blocked')
          if (r) { onDone?.(); close() }
        }}>
          <Field label="Facility">
            <select className="select" value={form.facility_id} onChange={set('facility_id')}>
              {venue.facilities.map((f) => <option key={f.id} value={f.id}>{f.name} · {f.sport}</option>)}
            </select>
          </Field>
          <div className="form-row">
            <Field label="Date"><input className="input" type="date" value={form.date} onChange={set('date')} /></Field>
            <Field label="From"><input className="input" type="time" step={1800} value={form.start} onChange={set('start')} /></Field>
            <Field label="To"><input className="input" type="time" step={1800} value={form.end} onChange={set('end')} /></Field>
          </div>
          <Field label="Reason">
            <select className="select" value={form.reason} onChange={set('reason')}>
              {Object.entries(reasons?.block || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
          </Field>
          <Field label={`Note${form.reason === 'other' ? '' : ' (optional)'}`}>
            <input className="input" value={form.note} onChange={set('note')} required={form.reason === 'other'} />
          </Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy}>Block now</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

/** Community Game: on-ground participants, levels, attendance, payment. */
export function GameDrawer({ id, onClose, onChanged }) {
  const { isHQ } = useAuth()
  const { data: g, reload } = useApi(`/api/community-games/${id}`)
  const reasons = useReasons()
  const [run, busy] = useAction()
  const [add, setAdd] = useState({ phone: '', name: '', level: 'intermediate' })
  const [lookup, setLookup] = useState({ phone: '', customer: null })
  const existing = digitsOf(add.phone) === lookup.phone ? lookup.customer : null
  const [method, setMethod] = useState('upi')
  const [cancel, setCancel] = useState({ reason: '', note: '' })

  useEffect(() => {
    const digits = digitsOf(add.phone)
    if (digits.length < 10) return
    api(`/api/customers/lookup?phone=${encodeURIComponent(add.phone)}`)
      .then((r) => setLookup({ phone: digits, customer: r.found ? r.customer : null })).catch(() => {})
  }, [add.phone])

  const act = async (fn, msg) => {
    const r = await run(fn, msg)
    if (r) { await reload(); onChanged?.() }
    return r
  }
  const stats = useMemo(() => g ? {
    attended: g.participants.filter((p) => p.attendance === 'attended').length,
    paid: g.participants.filter((p) => p.payment_status === 'paid').length,
    collected: g.participants.reduce((a, p) => a + (p.payment_status === 'paid' ? p.amount : 0), 0),
  } : null, [g])
  if (!g) return <Drawer open onClose={onClose} title="Community Game"><p className="subtle">Loading…</p></Drawer>
  const open = g.status !== 'cancelled'

  return (
    <Drawer open onClose={onClose} width={560}
      title={<span className="row" style={{ gap: 10 }}><KindLabel kind="community" />{g.title}</span>}
      subtitle={`${g.facilities.join(' + ')} · ${range12(g.start_min, g.end_min)} · ${g.date} · ${money(g.per_person)}/person`}>
      <div className="row">
        <Badge tone={g.state === 'live' ? 'good' : g.state === 'cancelled' ? 'bad' : undefined}>{g.state}</Badge>
        <Badge>{g.participant_count}/{g.capacity} players</Badge>
        <Badge tone="good">{stats.attended} attended</Badge>
        <Badge tone="brand">{money(stats.collected)} collected</Badge>
      </div>

      {open && (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await act(() => post(`/api/community-games/${g.id}/participants`, add), 'Participant added · WhatsApp link sent')
          if (r) setAdd({ phone: '', name: '', level: add.level })
        }}>
          <div className="section-title">Add participant on ground</div>
          <div className="form-row">
            <Field label="Phone" help={existing ? `Existing: ${existing.name || 'no name yet'}` : 'New players need a name'}>
              <input className="input" inputMode="tel" value={add.phone} onChange={(e) => setAdd({ ...add, phone: e.target.value })} required />
            </Field>
            <Field label="Name">
              <input className="input" value={existing?.name_locked ? existing.name : add.name} disabled={existing?.name_locked}
                onChange={(e) => setAdd({ ...add, name: e.target.value })} required={!existing} />
            </Field>
          </div>
          <div className="spread">
            <Seg value={add.level} onChange={(v) => setAdd({ ...add, level: v })} label="Level"
              options={(reasons?.levels || ['beginner', 'intermediate', 'advanced']).map((l) => ({ value: l, label: l[0].toUpperCase() + l.slice(1) }))} />
            <button className="btn btn-primary" disabled={busy || g.participant_count >= g.capacity}>Add</button>
          </div>
        </form>
      )}

      <section>
        <div className="spread" style={{ marginBottom: 8 }}>
          <div className="section-title" style={{ margin: 0 }}>Participants</div>
          <Seg value={method} onChange={setMethod} options={METHODS} label="Payment method for Paid" />
        </div>
        {g.participants.length === 0 ? <p className="subtle">No one recorded yet.</p> : (
          <div className="list">
            {g.participants.map((p) => (
              <div key={p.id} className="list-item" style={{ flexWrap: 'wrap' }}>
                <div className="grow">
                  <div className="title">{p.name || phoneFmt(p.phone)}</div>
                  <div className="meta">{phoneFmt(p.phone)} · {p.level}</div>
                </div>
                <Seg value={p.attendance} label={`Attendance for ${p.name}`}
                  onChange={(v) => act(() => patch(`/api/participants/${p.id}`, { attendance: v }))}
                  options={[{ value: 'attended', label: 'In' }, { value: 'no_show', label: 'No-show' }]} />
                {p.payment_status === 'paid' ? (
                  <button className="btn btn-sm" title="Undo payment" onClick={() => act(() => patch(`/api/participants/${p.id}`, { paid: false }), 'Payment reverted')}>
                    <Check /> {METHOD[p.payment_method]}
                  </button>
                ) : (
                  <button className="btn btn-sm btn-primary" disabled={busy}
                    onClick={() => act(() => patch(`/api/participants/${p.id}`, { paid: true, method, attendance: 'attended' }), `${money(g.per_person)} recorded`)}>
                    <Wallet /> Paid
                  </button>
                )}
                {p.payment_status !== 'paid' && (
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label={`Remove ${p.name}`} onClick={() => act(() => del(`/api/participants/${p.id}`), 'Removed')}><Trash2 /></button>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {isHQ && g.state === 'upcoming' && (
        <section>
          <div className="section-title">Cancel game (before start)</div>
          <div className="form">
            <select className="select" value={cancel.reason} onChange={(e) => setCancel({ ...cancel, reason: e.target.value })} aria-label="Reason">
              <option value="">Reason…</option>
              {Object.entries(reasons?.game_cancel || {}).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </select>
            {cancel.reason === 'other' && <input className="input" placeholder="Note" value={cancel.note} onChange={(e) => setCancel({ ...cancel, note: e.target.value })} />}
            <div><button className="btn btn-danger" disabled={!cancel.reason || busy}
              onClick={() => act(() => post(`/api/community-games/${g.id}/cancel`, cancel), 'Game cancelled')}><XCircle /> Cancel game</button></div>
          </div>
        </section>
      )}
      <p className="subtle" style={{ fontSize: 12.5 }}>Joining link: <a href={g.community_link} target="_blank" rel="noreferrer">{g.community_link}</a></p>
    </Drawer>
  )
}

/** Read-only details for HQ-managed allocations (Academy, Corporate). */
export function AllocationInfoDrawer({ cell, onClose }) {
  const { isHQ } = useAuth()
  return (
    <Drawer open onClose={onClose} title={<KindLabel kind={cell.kind}>{cell.title}</KindLabel>} subtitle={`${cell.time}`}>
      <dl className="kv">
        <dt>Type</dt><dd>{KIND[cell.kind].label}</dd>
        {cell.amount != null && <><dt>Value</dt><dd>{money(cell.amount)}</dd></>}
        {cell.payment_status && <><dt>Payment</dt><dd>{payBadge(cell.payment_status)}</dd></>}
      </dl>
      <p className="subtle" style={{ fontSize: 13 }}>
        {cell.kind === 'academy' ? 'Academy allocations are managed by HQ. Collected revenue is reconciled at month-end.' : 'Corporate/private events are managed by HQ.'}
      </p>
      {isHQ && <Link className="btn" to={cell.kind === 'academy' ? '/academy' : '/events'}>Open in {cell.kind === 'academy' ? 'Academy' : 'Events'}</Link>}
    </Drawer>
  )
}
