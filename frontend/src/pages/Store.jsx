/** RG Store: sell food and drink at a venue; HQ manages products, prices and stock, and sees and voids sales. */
import { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { History as HistoryIcon, Minus, PackagePlus, Pencil, Plus, ShoppingBag, SlidersHorizontal, Trash2, Undo2 } from 'lucide-react'
import { api, post, put } from '../lib/api'
import { useApi, useLocalState } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { addDays, dateTime, money, phoneFmt, shortDate, todayISO } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, Seg, Skeleton, Tabs } from '../components/ui'

const METHODS = [['cash', 'Cash'], ['upi', 'UPI'], ['razorpay', 'Razorpay'], ['other', 'Other']].map(([value, label]) => ({ value, label }))
const METHOD_LABEL = Object.fromEntries(METHODS.map((m) => [m.value, m.label]))
const vname = (n) => n.replace('RallyGully ', '')
const digitsOf = (p) => (p || '').replace(/\D/g, '')

export default function StorePage() {
  const { user, can } = useAuth()
  const hq = can('store.manage')
  const today = todayISO()
  const all = useApi('/api/venues').data
  // A VM sells only where they are rostered today; HQ can open any venue's store.
  const venues = (all || []).filter((v) => hq || (user.roster || []).some((r) => r.date === today && r.venue_id === v.id))
  const [vid, setVid] = useLocalState('pulse.store.venue', null)
  const venue = venues.find((v) => v.id === vid) || venues[0]
  const [tab, setTab] = useState('sell')

  if (!all) return <Skeleton h={300} />
  if (!venue) {
    return (
      <div className="stack rise">
        <div className="page-head"><div><h1>RG Store</h1></div></div>
        <div className="card"><Empty icon={ShoppingBag} title="No store to open today">You can sell at the venue you are rostered at, once you have clocked in there.</Empty></div>
      </div>
    )
  }
  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>RG Store</h1><p className="subtle">{vname(venue.name)}</p></div>
        {venues.length > 1 && (
          <select className="select" style={{ width: 220 }} value={venue.id} onChange={(e) => setVid(Number(e.target.value))} aria-label="Venue">
            {venues.map((v) => <option key={v.id} value={v.id}>{vname(v.name)}</option>)}
          </select>
        )}
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { value: 'sell', label: 'Sell' },
        ...(hq ? [{ value: 'products', label: 'Products & stock' }] : []),
        { value: 'sales', label: hq ? 'Store sales' : "Today's sales" },
      ]} />
      {tab === 'sell' && <SellTab key={venue.id} venue={venue} />}
      {tab === 'products' && hq && <ProductsTab key={venue.id} venue={venue} />}
      {tab === 'sales' && <SalesTab key={venue.id} venue={venue} hq={hq} />}
    </div>
  )
}

// ── Sell ─────────────────────────────────────────────────────────────────────
function SellTab({ venue }) {
  const { data: products, error, reload } = useApi(`/api/store/${venue.id}/products`)
  const [run, busy] = useAction()
  const [cart, setCart] = useState({})
  const [who, setWho] = useState({ phone: '', name: '', email: '' })
  const [method, setMethod] = useState('cash')
  const [done, setDone] = useState(null)

  // Fill the name and email of a known customer from their number.
  useEffect(() => {
    const d = digitsOf(who.phone)
    if (d.length < 10) return
    api(`/api/customers/lookup?phone=${encodeURIComponent(who.phone)}`).then((r) => {
      if (r.found) setWho((w) => ({ ...w, name: w.name || r.customer.name || '', email: w.email || r.customer.email || '' }))
    }).catch(() => {})
  }, [who.phone])

  if (error?.status === 428) {
    return <div className="card card-pad"><div className="callout warn">Clock in at <Link to={`/venues/${venue.id}`}>{vname(venue.name)}</Link> first, then the store opens.</div></div>
  }
  if (error && !products) return <ErrorState error={error} onRetry={reload} />
  if (!products) return <Skeleton h={300} />
  if (!products.length) return <div className="card"><Empty icon={ShoppingBag} title="No products yet">HQ adds this venue&apos;s products under Products &amp; stock.</Empty></div>

  const byId = Object.fromEntries(products.map((p) => [p.id, p]))
  const lines = Object.entries(cart).filter(([, q]) => q > 0).map(([id, q]) => ({ p: byId[id], q })).filter((x) => x.p)
  const total = lines.reduce((n, x) => n + x.p.price * x.q, 0)
  const add = (p, n = 1) => setCart((c) => ({ ...c, [p.id]: Math.max(0, Math.min(p.stock, (c[p.id] || 0) + n)) }))
  const groups = products.reduce((g, p) => ((g[p.category] = g[p.category] || []).push(p), g), {})

  const confirm = async () => {
    const r = await run(() => post(`/api/store/${venue.id}/sales`, {
      items: lines.map((x) => ({ product_id: x.p.id, quantity: x.q })), phone: who.phone, name: who.name || null,
      email: who.email || null, method,
    }), 'Sale confirmed · receipt sent')
    if (r) { setDone(r); setCart({}); setWho({ phone: '', name: '', email: '' }); reload() }
    else reload()
  }

  return (
    <div className="grid g-main">
      <div className="stack">
        {done && (
          <div className="callout good" style={{ display: 'grid', gap: 4 }}>
            <b>Receipt {done.receipt} · {money(done.total)} · {METHOD_LABEL[done.payment_method]}</b>
            <span>{done.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}. Sent to {phoneFmt(done.customer.phone)}.</span>
          </div>
        )}
        {Object.entries(groups).map(([cat, items]) => (
          <div key={cat} className="card">
            <div className="card-head"><h2>{cat}</h2></div>
            <div className="card-body" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(150px, 1fr))', gap: 10 }}>
              {items.map((p) => (
                <button key={p.id} type="button" className="btn" disabled={p.out || (cart[p.id] || 0) >= p.stock} onClick={() => add(p)}
                  style={{ display: 'grid', justifyItems: 'start', gap: 4, height: 'auto', padding: 12, textAlign: 'left' }}>
                  <b>{p.name}</b>
                  <span>{money(p.price)}</span>
                  {p.out ? <Badge tone="bad">Out of stock</Badge> : p.low ? <Badge tone="warn">{p.stock} left</Badge> : <span className="subtle" style={{ fontSize: 12 }}>{p.stock} in stock</span>}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
      <div className="card" style={{ alignSelf: 'start' }}>
        <div className="card-head"><h2>Order</h2>{lines.length > 0 && <button className="btn btn-sm btn-ghost" onClick={() => setCart({})}><Trash2 /> Clear</button>}</div>
        <div className="card-body stack">
          {!lines.length ? <p className="subtle">Tap products to add them.</p> : lines.map(({ p, q }) => (
            <div key={p.id} className="row" style={{ justifyContent: 'space-between' }}>
              <span>{p.name}<div className="subtle" style={{ fontSize: 12 }}>{money(p.price)} each</div></span>
              <span className="row" style={{ gap: 6 }}>
                <button className="btn btn-sm" aria-label={`One fewer ${p.name}`} onClick={() => add(p, -1)}><Minus /></button>
                <b style={{ minWidth: 20, textAlign: 'center' }}>{q}</b>
                <button className="btn btn-sm" aria-label={`One more ${p.name}`} disabled={q >= p.stock} onClick={() => add(p, 1)}><Plus /></button>
              </span>
            </div>
          ))}
          <div className="row" style={{ justifyContent: 'space-between', fontSize: 18 }}><span>Total</span><b>{money(total)}</b></div>
          <Field label="Customer phone" help="The receipt is sent here on WhatsApp">
            <input className="input" inputMode="tel" autoComplete="off" placeholder="98xxxxxxxx" value={who.phone} onChange={(e) => setWho({ ...who, phone: e.target.value })} />
          </Field>
          <div className="form-row">
            <Field label="Name (optional)"><input className="input" value={who.name} onChange={(e) => setWho({ ...who, name: e.target.value })} /></Field>
            <Field label="Email (optional)"><input className="input" type="email" value={who.email} onChange={(e) => setWho({ ...who, email: e.target.value })} /></Field>
          </div>
          <Seg value={method} onChange={setMethod} options={METHODS} label="Payment method" />
          {method === 'razorpay' && <p className="subtle" style={{ fontSize: 12.5 }}>Confirm only once the customer has paid on Razorpay. Pulse doesn&apos;t take Razorpay payments itself yet.</p>}
          <button className="btn btn-primary" disabled={busy || !lines.length || digitsOf(who.phone).length < 10} onClick={confirm}>
            <ShoppingBag /> {busy ? 'Confirming…' : `Confirm sale · ${money(total)}`}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Products & stock (HQ) ────────────────────────────────────────────────────
function ProductsTab({ venue }) {
  const { data, error, reload } = useApi(`/api/store/${venue.id}/products`)
  const [open, setOpen] = useState(null) // {mode, product}
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  const close = () => setOpen(null)
  const saved = () => { setOpen(null); reload() }
  return (
    <div className="card">
      <div className="card-head">
        <h2>Products &amp; stock</h2>
        <button className="btn btn-primary" onClick={() => setOpen({ mode: 'new' })}><Plus /> Add product</button>
      </div>
      <div className="card-body table-wrap">
        {!data ? <Skeleton h={200} /> : !data.length ? <Empty title="No products yet">Add what this venue sells, with its price and opening stock.</Empty> : (
          <table className="table">
            <thead><tr><th>Product</th><th>Category</th><th className="num">Price</th><th className="num">Stock</th><th className="num">Low at</th><th>Status</th><th /></tr></thead>
            <tbody>{data.map((p) => (
              <tr key={p.id} style={p.active ? undefined : { opacity: 0.55 }}>
                <td style={{ fontWeight: 500 }}>{p.name}</td><td>{p.category}</td>
                <td className="num">{money(p.price)}</td>
                <td className="num" style={{ fontWeight: 700, color: p.out ? 'var(--bad)' : p.low ? 'var(--warn)' : undefined }}>{p.stock}</td>
                <td className="num">{p.low_stock_level}</td>
                <td>{!p.active ? <Badge>Off sale</Badge> : p.out ? <Badge tone="bad">Out</Badge> : p.low ? <Badge tone="warn">Low</Badge> : <Badge tone="good">OK</Badge>}</td>
                <td className="num nowrap">
                  <button className="btn btn-sm" onClick={() => setOpen({ mode: 'restock', product: p })}><PackagePlus /> Restock</button>
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label={`Correct ${p.name}`} title="Correct stock" onClick={() => setOpen({ mode: 'adjust', product: p })}><SlidersHorizontal /></button>
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label={`Edit ${p.name}`} title="Edit" onClick={() => setOpen({ mode: 'edit', product: p })}><Pencil /></button>
                  <button className="btn btn-sm btn-ghost btn-icon" aria-label={`History of ${p.name}`} title="Stock history" onClick={() => setOpen({ mode: 'history', product: p })}><HistoryIcon /></button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </div>
      {open?.mode === 'new' && <ProductDrawer venue={venue} onClose={close} onSaved={saved} />}
      {open?.mode === 'edit' && <ProductDrawer venue={venue} product={open.product} onClose={close} onSaved={saved} />}
      {open?.mode === 'restock' && <StockDrawer product={open.product} kind="restock" onClose={close} onSaved={saved} />}
      {open?.mode === 'adjust' && <StockDrawer product={open.product} kind="adjust" onClose={close} onSaved={saved} />}
      {open?.mode === 'history' && <HistoryDrawer product={open.product} onClose={close} />}
    </div>
  )
}

function ProductDrawer({ venue, product, onClose, onSaved }) {
  const [run, busy] = useAction()
  const isNew = !product
  const [f, setF] = useState(product
    ? { name: product.name, category: product.category, price: String(product.price), low_stock_level: String(product.low_stock_level), active: product.active }
    : { name: '', category: '', price: '', low_stock_level: '5', opening_stock: '0', active: true })
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === 'checkbox' ? e.target.checked : e.target.value })
  return (
    <Drawer open onClose={onClose} title={isNew ? 'Add product' : `Edit ${product.name}`} subtitle={vname(venue.name)}>
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const body = { name: f.name, category: f.category || null, price: Number(f.price), low_stock_level: Number(f.low_stock_level),
            opening_stock: Number(f.opening_stock || 0), active: f.active }
          const r = await run(() => (isNew ? post(`/api/store/${venue.id}/products`, body) : put(`/api/store/products/${product.id}`, body)), isNew ? 'Product added' : 'Product saved')
          if (r) { onSaved(); close() }
        }}>
          <div className="form-row">
            <Field label="Name"><input className="input" value={f.name} maxLength={80} onChange={set('name')} required /></Field>
            <Field label="Category" help="For example Drinks, Snacks"><input className="input" value={f.category} maxLength={40} onChange={set('category')} /></Field>
          </div>
          <div className="form-row">
            <Field label="Price (₹)"><input className="input" type="number" min={1} step="any" value={f.price} onChange={set('price')} required /></Field>
            <Field label="Low-stock level" help="HQ is emailed when stock falls to this"><input className="input" type="number" min={0} value={f.low_stock_level} onChange={set('low_stock_level')} required /></Field>
          </div>
          {isNew
            ? <Field label="Opening stock"><input className="input" type="number" min={0} value={f.opening_stock} onChange={set('opening_stock')} /></Field>
            : <label className="check"><input type="checkbox" checked={f.active} onChange={set('active')} /> On sale (untick to hide it from the till)</label>}
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy}>{isNew ? 'Add product' : 'Save'}</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

function StockDrawer({ product, kind, onClose, onSaved }) {
  const [run, busy] = useAction()
  const [f, setF] = useState({ quantity: '', unit_cost: '', note: '', direction: 'remove' })
  const restock = kind === 'restock'
  return (
    <Drawer open onClose={onClose} title={restock ? `Restock ${product.name}` : `Correct ${product.name}`} subtitle={`${product.stock} in stock now`}>
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          const qty = Number(f.quantity)
          const r = await run(() => (restock
            ? post(`/api/store/products/${product.id}/restock`, { quantity: qty, unit_cost: f.unit_cost === '' ? null : Number(f.unit_cost), note: f.note || null })
            : post(`/api/store/products/${product.id}/adjust`, { delta: f.direction === 'remove' ? -qty : qty, reason: f.note })), restock ? 'Stock added' : 'Stock corrected')
          if (r) { onSaved(); close() }
        }}>
          {!restock && <Seg value={f.direction} onChange={(v) => setF({ ...f, direction: v })} label="Correction" options={[{ value: 'remove', label: 'Take off' }, { value: 'add', label: 'Add' }]} />}
          <div className="form-row">
            <Field label="Units"><input className="input" type="number" min={1} value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} required /></Field>
            {restock && <Field label="Cost per unit (₹, optional)"><input className="input" type="number" min={0} step="any" value={f.unit_cost} onChange={(e) => setF({ ...f, unit_cost: e.target.value })} /></Field>}
          </div>
          <Field label={restock ? 'Note (optional)' : 'Reason'} help={restock ? 'For example supplier or bill number' : 'For example damaged, expired, stock-take difference'}>
            <input className="input" maxLength={200} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} required={!restock} />
          </Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !f.quantity}>{restock ? 'Add stock' : 'Save correction'}</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}

const MOVE = { restock: 'Restock', sale: 'Sale', adjust: 'Correction', void: 'Voided sale' }

function HistoryDrawer({ product, onClose }) {
  const { data } = useApi(`/api/store/products/${product.id}/moves`)
  return (
    <Drawer open onClose={onClose} title={product.name} subtitle="Stock history">
      {!data ? <Skeleton h={120} /> : data.map((m) => (
        <div key={m.id} style={{ padding: '8px 0', borderTop: '1px solid var(--line)' }}>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <span><b>{MOVE[m.kind] || m.kind}</b>{m.unit_cost != null ? ` · ${money(m.unit_cost)}/unit` : ''}</span>
            <span><b style={{ color: m.quantity < 0 ? 'var(--bad)' : 'var(--good)' }}>{m.quantity > 0 ? '+' : ''}{m.quantity}</b> → {m.stock_after}</span>
          </div>
          <div className="subtle" style={{ fontSize: 12.5 }}>{dateTime(m.at)}{m.by ? ` · ${m.by}` : ''}{m.note ? ` · ${m.note}` : ''}</div>
        </div>
      ))}
    </Drawer>
  )
}

// ── Sales ────────────────────────────────────────────────────────────────────
function SalesTab({ venue, hq }) {
  const [range, setRange] = useState({ from: todayISO(), to: todayISO() })
  const q = hq ? `?date_from=${range.from}&date_to=${range.to}` : ''
  const { data, error, reload } = useApi(`/api/store/${venue.id}/sales${q}`)
  const [voiding, setVoiding] = useState(null)
  const methods = useMemo(() => Object.entries(data?.totals.by_method || {}), [data])
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  return (
    <div className="stack">
      {hq && (
        <div className="row">
          <input className="input" type="date" style={{ width: 170 }} value={range.from} onChange={(e) => setRange({ ...range, from: e.target.value })} aria-label="From" />
          <input className="input" type="date" style={{ width: 170 }} value={range.to} onChange={(e) => setRange({ ...range, to: e.target.value })} aria-label="To" />
          <button className="btn btn-sm" onClick={() => setRange({ from: addDays(todayISO(), -6), to: todayISO() })}>Last 7 days</button>
        </div>
      )}
      {!data ? <Skeleton h={240} /> : (
        <>
          <div className="card card-pad row" style={{ gap: 24, flexWrap: 'wrap' }}>
            <div><div className="subtle">Revenue</div><b style={{ fontSize: 22 }}>{money(data.totals.revenue)}</b></div>
            <div><div className="subtle">Sales</div><b style={{ fontSize: 22 }}>{data.totals.sales}</b></div>
            {methods.map(([m, v]) => <div key={m}><div className="subtle">{METHOD_LABEL[m] || m}</div><b>{money(v)}</b></div>)}
            {data.totals.voided > 0 && <div><div className="subtle">Voided</div><b>{data.totals.voided}</b></div>}
          </div>
          {data.top_items.length > 0 && (
            <div className="card"><div className="card-head"><h2>Top items</h2></div>
              <div className="card-body">{data.top_items.map((t) => (
                <div key={t.name} className="row" style={{ justifyContent: 'space-between' }}><span>{t.quantity} × {t.name}</span><b>{money(t.amount)}</b></div>
              ))}</div>
            </div>
          )}
          <div className="card">
            <div className="card-head"><h2>Sales</h2></div>
            <div className="card-body table-wrap">
              {!data.sales.length ? <Empty title="No sales in this period" /> : (
                <table className="table">
                  <thead><tr><th>Receipt</th><th>When</th><th>Customer</th><th>Items</th><th>Paid</th><th className="num">Total</th>{hq && <th />}</tr></thead>
                  <tbody>{data.sales.map((s) => (
                    <tr key={s.id} style={s.status === 'voided' ? { opacity: 0.55 } : undefined}>
                      <td className="mono">{s.receipt}</td>
                      <td className="nowrap">{hq && range.from !== range.to ? `${shortDate(s.paid_at.slice(0, 10))} ` : ''}{new Date(s.paid_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}<div className="subtle" style={{ fontSize: 12 }}>{s.sold_by}</div></td>
                      <td>{s.customer.name || '—'}<div className="subtle" style={{ fontSize: 12 }}>{phoneFmt(s.customer.phone)}</div></td>
                      <td>{s.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}</td>
                      <td>{s.status === 'voided' ? <Badge tone="bad">Voided</Badge> : METHOD_LABEL[s.payment_method] || s.payment_method}</td>
                      <td className="num" style={{ fontWeight: 600 }}>{money(s.total)}</td>
                      {hq && <td className="num">{s.status !== 'voided' && <button className="btn btn-sm btn-ghost" onClick={() => setVoiding(s)}><Undo2 /> Void</button>}</td>}
                    </tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          </div>
        </>
      )}
      {voiding && <VoidDrawer sale={voiding} onClose={() => setVoiding(null)} onDone={() => { setVoiding(null); reload() }} />}
    </div>
  )
}

function VoidDrawer({ sale, onClose, onDone }) {
  const [run, busy] = useAction()
  const [reason, setReason] = useState('')
  return (
    <Drawer open onClose={onClose} title={`Void ${sale.receipt}`} subtitle={`${money(sale.total)} · ${sale.items.map((i) => `${i.quantity} × ${i.name}`).join(', ')}`}>
      {(close) => (
        <form className="form" onSubmit={async (e) => {
          e.preventDefault()
          if (await run(() => post(`/api/store/sales/${sale.id}/void`, { reason }), 'Sale voided · stock returned')) { onDone(); close() }
        }}>
          <p className="subtle">The items go back into stock and the sale stops counting in revenue and the cash balance. Any refund to the customer is handled outside Pulse.</p>
          <Field label="Reason"><input className="input" maxLength={200} value={reason} onChange={(e) => setReason(e.target.value)} required /></Field>
          <div className="row" style={{ justifyContent: 'flex-end' }}>
            <button type="button" className="btn" onClick={close}>Cancel</button>
            <button className="btn btn-primary" disabled={busy || !reason.trim()}>Void sale</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}
