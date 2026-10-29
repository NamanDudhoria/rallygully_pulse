import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, Check, ClipboardPaste, Clock, FileSpreadsheet, UserCheck, UserX, Wallet, XCircle, RotateCcw, Undo2, Trash2 } from 'lucide-react'
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
  const { isHQ, can } = useAuth()
  const operate = can('venue.operate')
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
  const free = useApi(b && b.status === 'confirmed' && operate
    ? `/api/venues/${b.venue_id}/free?date=${b.date}&start=${hhmm(b.start_min)}&end=${hhmm(b.end_min)}` : null).data

  const act = async (fn, msg) => {
    const r = await run(fn, msg)
    if (r) { await reload(); onChanged?.() }
    return r
  }
  if (!b) return <Drawer open onClose={onClose} title="Booking"><p className="subtle">Loading…</p></Drawer>

  const live = b.status === 'confirmed' && operate // view-only roles see details, not actions
  const unpaid = live && ['to_collect', 'overdue'].includes(b.payment_status) && b.attendance !== 'no_show'
  const sameSport = (free || []).filter((f) => !b.sport || b.source === 'district' && !b.facility_id || f.sport === b.sport)

  return (
    <Drawer open onClose={onClose} width={500}
      title={<span className="row" style={{ gap: 10 }}><KindLabel kind={b.source} />{b.customer.name || phoneFmt(b.customer.phone)}</span>}
      subtitle={`${range12(b.start_min, b.end_min)} · ${b.date}${b.external_ref ? ` · ${b.external_ref}` : ''}`}>
      <div className="row">{attendBadge(b)}{b.status === 'confirmed' && b.attendance !== 'no_show' && payBadge(b.payment_status)}</div>
      {!operate && <div className="callout info">View only.</div>}

      <dl className="kv">
        <dt>Customer</dt><dd>{b.customer.name || <span className="subtle">Name not captured</span>} · {phoneFmt(b.customer.phone)}</dd>
        <dt>Court</dt><dd>{b.facility || <span className="subtle">Not assigned yet</span>}{b.sport ? ` · ${b.sport}` : ''}</dd>
        <dt>Booking value</dt><dd>{money(b.booking_value)}</dd>
        {b.discount_name && <><dt>Discount</dt><dd>{b.discount_name} ({b.discount_percent}% off {money(b.list_price)})</dd></>}
        {!b.discount_name && b.list_price != null && Math.abs(b.list_price - b.booking_value) > 0.5 && <><dt>Configured price</dt><dd>{money(b.list_price)} <span className="subtle">· changed by HQ</span></dd></>}
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

      {isHQ && live && (
        <section>
          <div className="section-title">Adjust amount{b.payment_status === 'paid' ? ' (HQ correction)' : ''}</div>
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

const toMin = (t) => { const [h, m] = (t || '').split(':').map(Number); return h * 60 + m }

/** Direct on-ground booking created from an available slot (PRD §20): the slot runs from a start to an end time. */
export function DirectBookingDrawer({ venue, date, facilityId, start, onClose, onDone }) {
  const { isHQ } = useAuth()
  const discounts = venue.discounts || []
  const facilities = venue.facilities.filter((f) => f.status === 'active')
  const [form, setForm] = useState({ facility_id: facilityId, date, start: hhmm(start), end: hhmm(start + 60), phone: '', name: '', email: '', amount: '', discount_id: '', pay_now: false, method: 'upi' })
  const [quote, setQuote] = useState(null)
  const [lookup, setLookup] = useState({ phone: '', customer: null })
  const existing = digitsOf(form.phone) === lookup.phone ? lookup.customer : null
  const [run, busy] = useAction()
  const set = (k) => (v) => setForm((f) => ({ ...f, [k]: v?.target ? (v.target.type === 'checkbox' ? v.target.checked : v.target.value) : v }))
  // Moving the start keeps the slot length, so the end follows it.
  const setStart = (e) => setForm((f) => {
    const [h, m] = e.target.value.split(':').map(Number)
    const len = Math.max(30, toMin(f.end) - toMin(f.start))
    return Number.isNaN(h) ? { ...f, start: e.target.value } : { ...f, start: e.target.value, end: hhmm(h * 60 + m + len) }
  })
  const badTimes = toMin(form.end) <= toMin(form.start)

  useEffect(() => {
    const q = `/api/venues/${venue.id}/quote?facility_id=${form.facility_id}&date=${form.date}&start=${form.start}&end=${form.end}${form.discount_id ? `&discount_id=${form.discount_id}` : ''}`
    api(q).then(setQuote).catch(() => setQuote(null))
  }, [venue.id, form.facility_id, form.date, form.start, form.end, form.discount_id])

  useEffect(() => {
    const digits = digitsOf(form.phone)
    if (digits.length < 10) return
    api(`/api/customers/lookup?phone=${encodeURIComponent(form.phone)}`).then((r) => {
      setLookup({ phone: digits, customer: r.found ? r.customer : null })
      if (r.found) setForm((f) => ({ ...f, name: r.customer.name || f.name, email: r.customer.email || f.email }))
    }).catch(() => {})
  }, [form.phone])

  const submit = async (e, close) => {
    e.preventDefault()
    const r = await run(() => post('/api/bookings/direct', {
      ...form, venue_id: venue.id, facility_id: Number(form.facility_id),
      amount: form.amount === '' ? null : Number(form.amount), discount_id: form.discount_id ? Number(form.discount_id) : null,
      method: form.pay_now ? form.method : null,
    }), form.email ? 'Direct booking created · WhatsApp and email sent' : 'Direct booking created · WhatsApp sent')
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
            <Field label="From"><input className="input" type="time" step={1800} value={form.start} onChange={setStart} required /></Field>
            <Field label="To" error={badTimes ? 'End must be after the start' : null}><input className="input" type="time" step={1800} value={form.end} onChange={set('end')} required /></Field>
          </div>
          {quote && !quote.available && <div className="callout bad"><AlertTriangle />This facility is not free for that time.</div>}
          <Field label="Customer phone" help={existing ? `Existing customer${existing.name_locked ? ' — name locked' : ''}` : 'Phone is the customer identity'}>
            <input className="input" inputMode="tel" autoComplete="off" placeholder="98xxxxxxxx" value={form.phone} onChange={set('phone')} required />
          </Field>
          <Field label="Customer name">
            <input className="input" value={form.name} onChange={set('name')} required disabled={existing?.name_locked} />
          </Field>
          <Field label="Customer email (optional)" help="If given, the booking confirmation is emailed too">
            <input className="input" type="email" autoComplete="off" placeholder="name@example.com" value={form.email} onChange={set('email')} />
          </Field>
          <Field label="Discount" help={discounts.length ? 'Percentage off the configured price' : 'No discounts are set up for this venue (Configuration → Venues)'}>
            <select className="select" value={form.discount_id} disabled={!discounts.length} onChange={(e) => setForm((f) => ({ ...f, discount_id: e.target.value, amount: '' }))}>
              <option value="">No discount</option>
              {discounts.map((d) => <option key={d.id} value={d.id}>{d.name} · {d.percent}% off</option>)}
            </select>
          </Field>
          {isHQ && (
            <Field label="Amount (HQ)" help={quote?.amount != null ? `Configured price ${money(quote.amount)} for ${fac?.sport}. Typing an amount here replaces it.` : 'No price configured — enter the amount'}>
              <input className="input" type="number" min={0} placeholder={quote?.total ?? ''} value={form.amount} disabled={!!form.discount_id}
                onChange={(e) => setForm((f) => ({ ...f, amount: e.target.value, discount_id: '' }))} />
            </Field>
          )}
          <div className="callout info" style={{ alignItems: 'baseline' }}>
            {quote?.total != null
              ? <span><b>{money(form.amount !== '' ? Number(form.amount) : quote.total)}</b>{form.discount_id && quote.amount != null ? <span className="subtle"> · {money(quote.amount)} less {discounts.find((d) => String(d.id) === String(form.discount_id))?.percent}%</span> : null}</span>
              : <span>{isHQ ? 'No price configured for this slot. Enter the amount above.' : 'No price is configured for this slot. Ask HQ to add it under Pricing.'}</span>}
          </div>
          <label className="check"><input type="checkbox" checked={form.pay_now} onChange={set('pay_now')} /> Paid now</label>
          {form.pay_now && <Seg value={form.method} onChange={set('method')} options={METHODS} label="Payment method" />}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || badTimes || (quote && !quote.available) || (!isHQ && quote?.total == null)}>Create booking</button>
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
  const { can, user } = useAuth()
  const manage = can('community.manage')
  const operate = manage || can('venue.operate')
  const { data: g, reload } = useApi(`/api/community-games/${id}`)
  const reasons = useReasons()
  const [run, busy] = useAction()
  const [add, setAdd] = useState({ phone: '', name: '', level: 'intermediate' })
  const [lookup, setLookup] = useState({ phone: '', customer: null })
  const existing = digitsOf(add.phone) === lookup.phone ? lookup.customer : null
  const [method, setMethod] = useState('upi')
  const [cancel, setCancel] = useState({ reason: '', note: '' })
  const [bulk, setBulk] = useState(false)

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
  const started = g.state === 'live' || g.state === 'completed'
  const canAdd = open && manage && g.state !== 'completed'
  const full = g.participant_count >= g.capacity
  const levels = (reasons?.levels || ['beginner', 'intermediate', 'advanced']).map((l) => ({ value: l, label: l[0].toUpperCase() + l.slice(1) }))

  return (
    <Drawer open onClose={onClose} width={600}
      title={<span className="row" style={{ gap: 10 }}><KindLabel kind="community" />{g.title}</span>}
      subtitle={`${g.facilities.join(' + ')} · ${range12(g.start_min, g.end_min)} · ${g.date} · ${money(g.per_person)}/person`}>
      <div className="row">
        <Badge tone={g.state === 'live' ? 'good' : g.state === 'cancelled' ? 'bad' : undefined}>{g.state}</Badge>
        <Badge>{g.participant_count}/{g.capacity} players</Badge>
        <Badge tone="good">{stats.attended} attended</Badge>
        <Badge tone="brand">{stats.paid} paid · {money(stats.collected)}</Badge>
      </div>

      {canAdd && !bulk && (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const r = await act(() => post(`/api/community-games/${g.id}/participants`, { ...add, phone: add.phone.trim() || null }),
            add.phone.trim() ? 'Player added · WhatsApp link sent' : 'Player added')
          if (r) setAdd({ phone: '', name: '', level: add.level })
        }}>
          <div className="spread">
            <div className="section-title" style={{ margin: 0 }}>Add player</div>
            <button type="button" className="btn btn-sm" onClick={() => setBulk(true)} disabled={full}><ClipboardPaste /> Paste list / upload Excel</button>
          </div>
          <div className="form-row">
            <Field label="Name">
              <input className="input" value={existing?.name_locked ? existing.name : add.name} disabled={existing?.name_locked}
                onChange={(e) => setAdd({ ...add, name: e.target.value })} required={!existing} />
            </Field>
            <Field label="Phone (optional)" help={existing ? `Existing: ${existing.name || 'no name yet'}` : 'Without it, the player is name-only'}>
              <input className="input" inputMode="tel" value={add.phone} onChange={(e) => setAdd({ ...add, phone: e.target.value })} />
            </Field>
          </div>
          <div className="spread">
            <Seg value={add.level} onChange={(v) => setAdd({ ...add, level: v })} label="Level" options={levels} />
            <button className="btn btn-primary" disabled={busy || full}>{full ? 'Game full' : 'Add'}</button>
          </div>
        </form>
      )}
      {canAdd && bulk && (
        <ImportPlayers game={g} levels={levels} onCancel={() => setBulk(false)}
          onAdded={async () => { setBulk(false); await reload(); onChanged?.() }} />
      )}

      <section>
        <div className="spread" style={{ marginBottom: 8 }}>
          <div className="section-title" style={{ margin: 0 }}>Players</div>
          {operate && open && <Seg value={method} onChange={setMethod} options={METHODS} label="Payment method for Paid" />}
        </div>
        {g.participants.length === 0 ? <p className="subtle">{manage ? 'No players added yet.' : 'The Community team hasn’t added players yet.'}</p> : (
          <div className="list">
            {g.participants.map((p) => {
              const canUndo = manage || (p.collected_by != null && p.collected_by === user.id)
              return (
                <div key={p.id} className="list-item" style={{ flexWrap: 'wrap' }}>
                  <div className="grow">
                    <div className="title">{p.name || phoneFmt(p.phone)}</div>
                    <div className="meta">
                      {p.phone ? phoneFmt(p.phone) : <span>no phone</span>} · {p.level}{p.guest_of_name && <> · guest of {p.guest_of_name}</>}
                    </div>
                    {!p.phone && operate && open && <AddPhone onSave={(phone) => act(() => patch(`/api/participants/${p.id}`, { phone }), 'Phone added')} />}
                  </div>
                  {!operate || !open ? (
                    <>
                      {p.attendance === 'attended' ? <Badge tone="good">In</Badge> : p.attendance === 'no_show' ? <Badge tone="bad">No-show</Badge> : <Badge>Added</Badge>}
                      {payBadge(p.payment_status)}
                    </>
                  ) : (<>
                    {(started || !manage) && (
                      <Seg value={p.attendance} label={`Attendance for ${p.name}`}
                        onChange={(v) => act(() => patch(`/api/participants/${p.id}`, { attendance: v }))}
                        options={[{ value: 'attended', label: 'In' }, { value: 'no_show', label: 'No-show' }]} />
                    )}
                    {p.payment_status === 'paid' ? (
                      canUndo ? (
                        <button className="btn btn-sm" title="Undo payment" disabled={busy} onClick={() => act(() => patch(`/api/participants/${p.id}`, { paid: false }), 'Payment reverted')}>
                          <Check /> Paid · {METHOD[p.payment_method]}
                        </button>
                      ) : <Badge tone="good" icon={Check}>Paid · {METHOD[p.payment_method]}</Badge>
                    ) : (
                      <button className="btn btn-sm btn-primary" disabled={busy}
                        onClick={() => act(() => patch(`/api/participants/${p.id}`, started || !manage ? { paid: true, method, attendance: 'attended' } : { paid: true, method }),
                          `${money(g.per_person)} recorded`)}>
                        <Wallet /> {started || !manage ? 'Collect' : 'Mark prepaid'}
                      </button>
                    )}
                    {manage && p.payment_status !== 'paid' && (
                      <button className="btn btn-sm btn-ghost btn-icon" aria-label={`Remove ${p.name}`} onClick={() => act(() => del(`/api/participants/${p.id}`), 'Removed')}><Trash2 /></button>
                    )}
                  </>)}
                </div>
              )
            })}
          </div>
        )}
      </section>

      {manage && g.state === 'upcoming' && (
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

function AddPhone({ onSave }) {
  const [on, setOn] = useState(false)
  const [v, setV] = useState('')
  if (!on) return <button type="button" className="btn btn-ghost btn-sm" style={{ padding: '0 4px', minHeight: 22 }} onClick={() => setOn(true)}>+ Add phone</button>
  return (
    <form className="row" style={{ gap: 6, marginTop: 4 }} onSubmit={async (e) => { e.preventDefault(); if (await onSave(v)) { setOn(false); setV('') } }}>
      <input className="input" inputMode="tel" autoFocus value={v} onChange={(e) => setV(e.target.value)} placeholder="Mobile" style={{ width: 150, minHeight: 28, padding: '2px 8px' }} />
      <button className="btn btn-sm btn-primary" disabled={digitsOf(v).length < 10}>Save</button>
      <button type="button" className="btn btn-sm btn-ghost" onClick={() => setOn(false)}>Cancel</button>
    </form>
  )
}

const IMPORT_TONE = { ready: 'good', added: 'good', skip: undefined, error: 'bad' }
const IMPORT_LABEL = { ready: 'Will add', added: 'Added', skip: 'Skipped', error: 'Fix' }

function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader()
    r.onload = () => resolve(String(r.result).split(',')[1] || '')
    r.onerror = () => reject(new Error('Could not read the file'))
    r.readAsDataURL(file)
  })
}

/** Paste rows from a sheet / WhatsApp, or upload .xlsx / .csv → preview → add. */
function ImportPlayers({ game, levels, onCancel, onAdded }) {
  const [text, setText] = useState('')
  const [file, setFile] = useState(null)
  const [level, setLevel] = useState('intermediate')
  const [preview, setPreview] = useState(null)
  const [only, setOnly] = useState(null)
  const [run, busy] = useAction()
  const url = `/api/community-games/${game.id}/participants/import`
  const payload = async (dryRun, sections = only) => {
    const base = { default_level: level, dry_run: dryRun, sections }
    if (file) return { ...base, filename: file.name, file_b64: await readFileB64(file) }
    return { ...base, text }
  }
  const check = async (sections = only) => {
    const r = await run(async () => post(url, await payload(true, sections)))
    if (r) {
      setPreview(r)
      if (r.sections?.length) setOnly(r.sections.filter((x) => x.selected).map((x) => x.label))
    }
  }
  const toggleSection = (label) => {
    const next = only.includes(label) ? only.filter((x) => x !== label) : [...only, label]
    setOnly(next)
    check(next)
  }
  const commit = async () => {
    const r = await run(async () => post(url, await payload(false)), (x) => `${x.added} player${x.added === 1 ? '' : 's'} added`)
    if (r) {
      setPreview(r)
      if (r.added) onAdded(r)
    }
  }
  const reset = () => { setPreview(null); setOnly(null) }

  return (
    <section className="form">
      <div className="spread">
        <div className="section-title" style={{ margin: 0 }}>Add players in bulk</div>
        <button type="button" className="btn btn-sm btn-ghost" onClick={onCancel}>One at a time</button>
      </div>
      {!preview ? (
        <>
          <Field label="Paste the WhatsApp sign-up post, or rows from Excel / Sheets"
            help="Sections like “7-8 PM | Intermediate” set time and level; “Name + 1” adds a guest; empty slots are ignored. Phones are optional.">
            <textarea className="input" rows={8} value={text} disabled={!!file} placeholder={'7-8 PM | Intermediate\n1. Jatan\n2. Jatan + 1\n3. Lavitt\n4.\n\n8-9 PM | Beginner\n1. Ayush Sharma'}
              onChange={(e) => setText(e.target.value)} style={{ fontFamily: 'var(--font-mono, monospace)', fontSize: 12.5 }} />
          </Field>
          <div className="row" style={{ gap: 8 }}>
            <label className="btn btn-sm">
              <FileSpreadsheet /> {file ? file.name : 'Upload .xlsx / .csv'}
              <input type="file" accept=".xlsx,.csv" hidden onChange={(e) => setFile(e.target.files?.[0] || null)} />
            </label>
            {file && <button type="button" className="btn btn-sm btn-ghost" onClick={() => setFile(null)}>Clear file</button>}
          </div>
          <Field label="Level for rows without one"><Seg value={level} onChange={setLevel} label="Default level" options={levels} /></Field>
          <div><button type="button" className="btn btn-primary" disabled={busy || (!file && !text.trim())} onClick={() => check(null)}>Preview</button></div>
        </>
      ) : (
        <>
          {preview.sections?.length > 0 && (
            <div className="stack" style={{ gap: 6 }}>
              <div className="subtle" style={{ fontSize: 12.5 }}>Slots in this post — this game is {range12(game.start_min, game.end_min)}</div>
              <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
                {preview.sections.map((x) => (
                  <label key={x.label} className="check" style={{ border: '1px solid var(--border)', borderRadius: 8, padding: '4px 10px' }}>
                    <input type="checkbox" checked={!!only?.includes(x.label)} disabled={busy || preview.added > 0} onChange={() => toggleSection(x.label)} />
                    {x.label} <span className="subtle">· {x.count}</span>{x.matches && <Badge tone="good">this game</Badge>}
                  </label>
                ))}
              </div>
            </div>
          )}
          <div className="row" style={{ gap: 6 }}>
            <Badge tone="good">{preview.ready} to add</Badge>
            {preview.problems > 0 && <Badge tone="bad">{preview.problems} need fixing</Badge>}
            {preview.skipped > 0 && <Badge>{preview.skipped} skipped</Badge>}
            <span className="subtle" style={{ fontSize: 12.5 }}>{preview.capacity_left} spot{preview.capacity_left === 1 ? '' : 's'} left before import</span>
          </div>
          <div className="table-wrap" style={{ maxHeight: 320, overflow: 'auto' }}>
            <table className="table">
              <thead><tr><th>Row</th><th>Name</th><th>Phone</th><th>Level</th><th>Status</th></tr></thead>
              <tbody>{preview.rows.map((r) => (
                <tr key={r.line}>
                  <td className="mono subtle">{r.line}</td>
                  <td>
                    {r.name || <span className="subtle">—</span>}{r.guests > 0 && <b> +{r.guests}</b>}
                    {r.existing && <span className="subtle" style={{ fontSize: 11.5 }}> · existing</span>}
                  </td>
                  <td className="mono nowrap">{r.phone ? (r.status === 'error' ? r.phone : phoneFmt(r.phone)) : <span className="subtle">—</span>}</td>
                  <td>{r.level}</td>
                  <td><Badge tone={IMPORT_TONE[r.status]}>{IMPORT_LABEL[r.status]}</Badge>{r.message && <div className="subtle" style={{ fontSize: 11.5 }}>{r.message}</div>}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
          <div className="row" style={{ gap: 8 }}>
            <button type="button" className="btn btn-primary" disabled={busy || !preview.ready || preview.added > 0} onClick={commit}>
              Add {preview.ready} player{preview.ready === 1 ? '' : 's'}
            </button>
            <button type="button" className="btn" disabled={busy} onClick={reset}>Edit list</button>
          </div>
        </>
      )}
    </section>
  )
}

/** Read-only details for HQ-managed allocations (Academy, Corporate). */
export function AllocationInfoDrawer({ cell, onClose }) {
  const { can } = useAuth()
  const isHQ = can(cell.kind === 'academy' ? 'academy.manage' : 'events.manage')
  return (
    <Drawer open onClose={onClose} title={<KindLabel kind={cell.kind}>{cell.title}</KindLabel>} subtitle={`${cell.time}`}>
      <dl className="kv">
        <dt>Type</dt><dd>{KIND[cell.kind].label}</dd>
        {cell.amount != null && <><dt>Value</dt><dd>{money(cell.amount)}</dd></>}
        {cell.payment_status && <><dt>Payment</dt><dd>{payBadge(cell.payment_status)}</dd></>}
      </dl>
      {isHQ && <Link className="btn" to={cell.kind === 'academy' ? '/academy' : '/events'}>Open in {cell.kind === 'academy' ? 'Academy' : 'Events'}</Link>}
    </Drawer>
  )
}
