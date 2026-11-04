/** A Venue Manager's own cash: what they hold, and the two things they can do with it, deposit it or spend it. */
import { useState } from 'react'
import { Banknote, Camera, Landmark, Receipt } from 'lucide-react'
import { post } from '../lib/api'
import { openReceipt } from '../lib/receipt'
import { useApi } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { dateTime, money } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, Skeleton } from '../components/ui'
import { PhotoCamera } from '../components/PhotoCamera'

const vname = (n) => (n || '').replace('RallyGully ', '')
const KIND = { deposit: ['Deposited in bank', 'var(--bad)', '−'], expense: ['Expense', 'var(--bad)', '−'], adjust: ['Adjustment by HQ', 'var(--text-2)', ''] }

export default function MyCashPage() {
  const { user } = useAuth()
  const { data, error, reload } = useApi('/api/my-cash')
  const [open, setOpen] = useState(null) // 'deposit' | 'expense'
  if (user?.role !== 'vm') return <div className="card"><Empty icon={Banknote} title="This page is for Venue Managers">HQ sees every VM&apos;s cash in the VM roster.</Empty></div>
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton h={300} />
  const holding = data.venues.filter((v) => v.balance > 0)
  return (
    <div className="stack rise">
      <div className="page-head"><div><h1>My cash</h1><p className="subtle">Cash you have collected and not yet deposited or spent</p></div></div>
      <div className="card card-pad stack">
        <div className="subtle">Cash with you</div>
        <div style={{ fontSize: 34, fontWeight: 700 }}>{money(data.total)}</div>
        {holding.length > 0 && <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>{holding.map((v) => <Badge key={v.venue_id}>{vname(v.name)} · {money(v.balance)}</Badge>)}</div>}
        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
          <button className="btn btn-primary" disabled={!holding.length} onClick={() => setOpen('deposit')}><Landmark /> Deposit in bank</button>
          <button className="btn" disabled={!holding.length} onClick={() => setOpen('expense')}><Receipt /> Record an expense</button>
        </div>
        <p className="subtle" style={{ fontSize: 12.5, margin: 0 }}>
          Collected {money(data.collected)} · deposited {money(data.deposited)} · spent {money(data.expenses)}. Every deposit and expense is emailed to the finance lead with its photo.
        </p>
      </div>
      <div className="card">
        <div className="card-head"><h2>What you did with it</h2></div>
        <div className="card-body">
          {!data.entries.length ? <Empty title="Nothing yet">Deposits and expenses appear here.</Empty> : data.entries.map((e) => (
            <div key={e.id} style={{ padding: '10px 0', borderTop: '1px solid var(--line)' }}>
              <div className="row" style={{ justifyContent: 'space-between' }}>
                <span><b>{KIND[e.kind]?.[0] || e.kind}</b> · {vname(e.venue)}</span>
                <b style={{ color: KIND[e.kind]?.[1] }}>{e.kind === 'adjust' ? (e.amount > 0 ? '+' : '−') : KIND[e.kind]?.[2]}{money(Math.abs(e.amount))}</b>
              </div>
              <div className="subtle" style={{ fontSize: 12.5 }}>
                {dateTime(e.created_at)}{e.message ? ` · ${e.message}` : ''}
                {e.has_photo && <> · <button type="button" className="btn btn-ghost btn-sm" style={{ padding: '0 4px' }} onClick={() => openReceipt(e.id)}><Camera size={13} /> Photo</button></>}
              </div>
            </div>
          ))}
        </div>
      </div>
      {open && <MoveDrawer kind={open} holding={holding} onClose={() => setOpen(null)} onDone={() => { setOpen(null); reload() }} />}
    </div>
  )
}

function MoveDrawer({ kind, holding, onClose, onDone }) {
  const [run, busy] = useAction()
  const deposit = kind === 'deposit'
  const [f, setF] = useState({ venue_id: String(holding[0]?.venue_id ?? ''), amount: '', message: '' })
  const [shooting, setShooting] = useState(false)
  const [photo, setPhoto] = useState(null)
  const held = holding.find((v) => String(v.venue_id) === f.venue_id)?.balance ?? 0
  const amount = Number(f.amount)
  const valid = amount > 0 && amount <= held && (deposit ? !!photo : !!f.message.trim())
  const submit = async () => {
    const r = await run(() => post(`/api/my-cash/${kind}`, { venue_id: Number(f.venue_id), amount, message: f.message || null, photo }),
      deposit ? 'Deposit recorded · finance has been emailed' : 'Expense recorded · finance has been emailed')
    if (r) onDone()
  }
  return (
    <Drawer open onClose={onClose} title={deposit ? 'Deposit in bank' : 'Record an expense'} subtitle={`${money(held)} with you from this venue`}>
      <div className="form">
        {holding.length > 1 && (
          <Field label="Cash from">
            <select className="select" value={f.venue_id} onChange={(e) => setF({ ...f, venue_id: e.target.value })}>
              {holding.map((v) => <option key={v.venue_id} value={v.venue_id}>{vname(v.name)} · {money(v.balance)}</option>)}
            </select>
          </Field>
        )}
        <Field label="Amount (₹)" error={amount > held ? `You only hold ${money(held)} from this venue` : null}
          help={amount > 0 && amount <= held ? `You will have ${money(held - amount)} left from this venue` : null}>
          <input className="input" type="number" min={1} step="any" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} autoFocus />
        </Field>
        <Field label={deposit ? 'Bank, branch or slip number (optional)' : 'What was it spent on?'}>
          <input className="input" maxLength={300} value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} required={!deposit} />
        </Field>
        <div className="stack" style={{ gap: 8 }}>
          <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>{deposit ? 'Photo of the bank receipt (required)' : 'Photo of the bill (optional)'}</span>
          {photo && <img src={photo} alt="Receipt" style={{ maxWidth: 220, borderRadius: 8 }} />}
          <div><button type="button" className="btn" onClick={() => setShooting(true)}><Camera /> {photo ? 'Retake photo' : 'Take photo'}</button></div>
        </div>
        <div className="row" style={{ justifyContent: 'flex-end' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" disabled={busy || !valid} onClick={submit}>{busy ? 'Saving…' : deposit ? 'Record deposit' : 'Record expense'}</button>
        </div>
      </div>
      {shooting && <PhotoCamera title={deposit ? 'Bank receipt' : 'Bill or receipt'} hint="Take it now, with the camera: nothing from the gallery."
        onClose={() => setShooting(false)} onUse={(p) => { setPhoto(p); setShooting(false) }} />}
    </Drawer>
  )
}
