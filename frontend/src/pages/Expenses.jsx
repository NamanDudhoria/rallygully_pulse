/** Expenses: HQ, Community and Academy log company spend; Venue Managers' cash expenses are added in by venue. */
import { useState } from 'react'
import { Pencil, Plus, Receipt, Trash2 } from 'lucide-react'
import { del, post, put } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { money, shortDate, todayISO } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, Skeleton } from '../components/ui'

const vname = (n) => (n || '').replace('RallyGully ', '')
const monthStart = () => `${todayISO().slice(0, 8)}01`

export default function ExpensesPage() {
  const { user, can } = useAuth()
  const [range, setRange] = useState({ from: monthStart(), to: todayISO() })
  const [venue, setVenue] = useState('')
  const [edit, setEdit] = useState(null) // null | {} (new) | row
  const [run, busy] = useAction()
  const venues = useApi('/api/venues').data || []
  const qs = `date_from=${range.from}&date_to=${range.to}${venue === 'central' ? '&central=true' : venue ? `&venue_id=${venue}` : ''}`
  const { data, error, reload } = useApi(`/api/expenses?${qs}`)
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!data) return <Skeleton h={300} />
  const mayChange = (r) => r.source === 'hq' && (can('corrections') || r.by_id === user.id)
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>Expenses</h1><p className="subtle">Company spend by venue. Venue Managers&apos; cash expenses are included automatically; revenue comes from Pulse.</p></div>
        <button className="btn btn-primary" onClick={() => setEdit({})}><Plus /> Add expense</button>
      </div>
      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <input className="input" type="date" aria-label="From" value={range.from} max={range.to} onChange={(e) => setRange({ ...range, from: e.target.value })} style={{ width: 'auto' }} />
        <input className="input" type="date" aria-label="To" value={range.to} min={range.from} onChange={(e) => setRange({ ...range, to: e.target.value })} style={{ width: 'auto' }} />
        <select className="select" aria-label="Venue" value={venue} onChange={(e) => setVenue(e.target.value)} style={{ width: 'auto' }}>
          <option value="">All venues + central</option>
          <option value="central">Central only</option>
          {venues.map((v) => <option key={v.id} value={v.id}>{vname(v.name)}</option>)}
        </select>
      </div>

      <div className="card card-pad stack">
        <div className="subtle">Total expenses</div>
        <div style={{ fontSize: 32, fontWeight: 700 }}>{money(data.totals.total)}</div>
        <div className="subtle">HQ-logged {money(data.totals.hq)} · Venue Manager cash {money(data.totals.vm)}</div>
      </div>

      {data.by_venue.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>By venue</h2></div>
          <div className="table-wrap"><table className="table">
            <thead><tr><th>Venue</th><th className="num">HQ expenses</th><th className="num">VM expenses</th><th className="num">Total</th></tr></thead>
            <tbody>{data.by_venue.map((b) => (
              <tr key={b.venue_id ?? 'central'}><td>{vname(b.name)}</td><td className="num">{money(b.hq)}</td><td className="num">{money(b.vm)}</td><td className="num"><strong>{money(b.total)}</strong></td></tr>
            ))}</tbody>
          </table></div>
        </div>
      )}
      {data.by_category.length > 0 && (
        <div className="card">
          <div className="card-head"><h2>By category</h2></div>
          <div className="card-body row" style={{ gap: 8, flexWrap: 'wrap' }}>
            {data.by_category.map((c) => <Badge key={c.category}>{c.category} · {money(c.total)}</Badge>)}
          </div>
        </div>
      )}

      <div className="card">
        <div className="card-head"><h2>All expenses</h2></div>
        {data.rows.length === 0 ? <Empty icon={Receipt} title="No expenses in this period">Add one with the button above.</Empty> : (
          <div className="list">
            {data.rows.map((r) => (
              <div key={`${r.source}-${r.id}`} className="list-item">
                <div className="grow">
                  <div className="title">{r.label}{r.subcategory ? ` · ${r.subcategory}` : ''} <Badge tone={r.source === 'vm' ? undefined : 'brand'}>{r.source === 'vm' ? 'VM cash' : vname(r.venue)}</Badge></div>
                  <div className="meta">
                    {shortDate(r.date)}{r.source === 'vm' && ` · ${vname(r.venue)}`}{r.location && ` · ${r.location}`}{r.notes && ` · ${r.notes}`} · {r.by}
                  </div>
                </div>
                <strong>{money(r.amount)}</strong>
                {mayChange(r) && <>
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label="Edit expense" onClick={() => setEdit(r)}><Pencil /></button>
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label="Delete expense" disabled={busy}
                    onClick={() => window.confirm('Delete this expense?') && run(() => del(`/api/expenses/${r.id}`), 'Expense deleted').then((ok) => ok && reload())}><Trash2 /></button>
                </>}
              </div>
            ))}
          </div>
        )}
      </div>
      {edit && <ExpenseDrawer row={edit} venues={venues} onClose={() => setEdit(null)} onDone={() => { setEdit(null); reload() }} />}
    </div>
  )
}

function ExpenseDrawer({ row, venues, onClose, onDone }) {
  const [run, busy] = useAction()
  const cats = useApi('/api/expenses/categories').data
  const [f, setF] = useState({
    spent_on: row.date || todayISO(), amount: row.amount ?? '', venue_id: row.venue_id ? String(row.venue_id) : '',
    location: row.location || '', category: row.category || '', subcategory: row.subcategory || '',
    category_other: row.category_other || '', notes: row.notes || '',
  })
  const others = cats && f.category === cats.others
  const subs = (cats && cats.categories[f.category]) || []
  const valid = Number(f.amount) > 0 && f.category && (!others || f.category_other.trim())
  const submit = async () => {
    const body = { ...f, amount: Number(f.amount), venue_id: f.venue_id ? Number(f.venue_id) : null, subcategory: f.subcategory || null,
      location: f.location || null, notes: f.notes || null, category_other: others ? f.category_other : null }
    const r = await run(() => (row.id ? put(`/api/expenses/${row.id}`, body) : post('/api/expenses', body)), row.id ? 'Expense updated' : 'Expense added')
    if (r) onDone()
  }
  return (
    <Drawer open onClose={onClose} title={row.id ? 'Edit expense' : 'Add expense'} subtitle="No venue means a central expense">
      {!cats ? <Skeleton h={200} /> : (
        <div className="form">
          <div className="form-row">
            <Field label="Date"><input className="input" type="date" value={f.spent_on} max={todayISO()} onChange={(e) => setF({ ...f, spent_on: e.target.value })} /></Field>
            <Field label="Amount (₹)"><input className="input" type="number" min={1} step="any" value={f.amount} autoFocus onChange={(e) => setF({ ...f, amount: e.target.value })} /></Field>
          </div>
          <Field label="Venue" help="Leave as Central if it isn't for one venue">
            <select className="select" value={f.venue_id} onChange={(e) => setF({ ...f, venue_id: e.target.value })}>
              <option value="">Central (no venue)</option>
              {venues.map((v) => <option key={v.id} value={v.id}>{vname(v.name)}</option>)}
            </select>
          </Field>
          <Field label="Location / paid to"><input className="input" maxLength={120} value={f.location} onChange={(e) => setF({ ...f, location: e.target.value })} placeholder="Shop, vendor or place" /></Field>
          <div className="form-row">
            <Field label="Category">
              <select className="select" value={f.category} onChange={(e) => setF({ ...f, category: e.target.value, subcategory: '' })}>
                <option value="">Choose…</option>
                {Object.keys(cats.categories).map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
            </Field>
            {!others && (
              <Field label="Subcategory">
                <select className="select" value={f.subcategory} disabled={!subs.length} onChange={(e) => setF({ ...f, subcategory: e.target.value })}>
                  <option value="">{subs.length ? 'Choose…' : '—'}</option>
                  {subs.map((c) => <option key={c} value={c}>{c}</option>)}
                </select>
              </Field>
            )}
          </div>
          {others && <Field label="Write the category"><input className="input" maxLength={60} value={f.category_other} onChange={(e) => setF({ ...f, category_other: e.target.value })} /></Field>}
          <Field label="Notes"><input className="input" maxLength={300} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !valid} onClick={submit}>{busy ? 'Saving…' : 'Save'}</button>
          </div>
        </div>
      )}
    </Drawer>
  )
}
