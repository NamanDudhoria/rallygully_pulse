import { useEffect, useRef, useState } from 'react'
import { KIND, hhmm, money, time12 } from '../lib/format'
import { Empty } from './ui'
import { CalendarOff } from 'lucide-react'

const MIN_SLOT_W = 56 // px per 30 minutes (stretches to fill wide screens)
const FAC_W = 148

/**
 * Facility × 30-minute grid. Availability is never stored: free cells are simply the
 * gaps between active allocations inside operating hours.
 */
export default function AvailabilityGrid({ grid, onFree, onOpen, readOnly }) {
  const box = useRef(null)
  const [boxW, setBoxW] = useState(0)
  const hasHours = !!grid.hours
  useEffect(() => {
    if (!box.current) return undefined // closed day: nothing rendered to measure
    const ro = new ResizeObserver(([e]) => setBoxW(e.contentRect.width))
    ro.observe(box.current)
    return () => ro.disconnect()
  }, [hasHours])
  const nSlots = grid.hours ? (grid.hours.close_min - grid.hours.open_min) / 30 : 1
  const SLOT_W = Math.max(MIN_SLOT_W, Math.floor((boxW - FAC_W - 2) / nSlots) || 0)
  const anchor = grid.hours && grid.now_min != null ? grid.now_min - grid.hours.open_min : null
  useEffect(() => {
    // Bring "now" into view once, an hour from the left edge.
    if (box.current && anchor != null && anchor > 60) box.current.scrollLeft = ((anchor - 60) / 30) * SLOT_W
  }, [anchor, SLOT_W])
  if (!grid.hours) return <Empty icon={CalendarOff} title="Venue closed">No operating hours on this date.</Empty>
  const { open_min: open, close_min: close } = grid.hours
  const slots = []
  for (let m = open; m < close; m += 30) slots.push(m)
  const width = slots.length * SLOT_W
  const x = (min) => ((min - open) / 30) * SLOT_W
  const byFac = {}
  grid.cells.forEach((c) => { (byFac[c.facility_id] ||= []).push(c) })
  // Unassigned District bookings hold venue capacity: when free courts in a slot don't
  // exceed that demand, the remaining free cells are held for District arrivals.
  const held = new Set()
  for (const m of slots) {
    const demand = grid.unassigned_district.filter((b) => b.start_min <= m && m < b.end_min).length
    if (!demand) continue
    const busy = grid.facilities.filter((f) => (byFac[f.id] || []).some((c) => c.start_min <= m && m < c.end_min)).length
    if (grid.facilities.length - busy <= demand) held.add(m)
  }
  const nowX = grid.now_min != null && grid.now_min >= open && grid.now_min <= close ? x(grid.now_min) : null

  return (
    <div className="avail" ref={box} role="region" aria-label="Availability grid" tabIndex={0}>
      <div className="avail-inner" style={{ gridTemplateColumns: `${FAC_W}px ${width}px` }}>
        <div className="avail-corner">Facility</div>
        <div style={{ position: 'relative', display: 'flex' }}>
          {slots.map((m) => (
            <div key={m} className="avail-hour" style={{ width: SLOT_W, borderLeftStyle: m % 60 ? 'dashed' : 'solid' }}>
              {m % 60 === 0 ? time12(m).replace(':00', '') : ''}
            </div>
          ))}
        </div>
        {grid.facilities.map((f) => (
          <Lane key={f.id} f={f} slots={slots} x={x} w={SLOT_W} cells={byFac[f.id] || []} nowMin={grid.now_min} held={held}
            onFree={onFree} onOpen={onOpen} readOnly={readOnly} />
        ))}
        {nowX != null && (
          <div className="now-line" style={{ left: FAC_W + nowX }} aria-label={`Now ${hhmm(grid.now_min)}`} />
        )}
      </div>
    </div>
  )
}

function Lane({ f, slots, x, w, cells, nowMin, held, onFree, onOpen, readOnly }) {
  const busy = new Set()
  cells.forEach((c) => { for (let m = c.start_min; m < c.end_min; m += 30) busy.add(m) })
  return (
    <>
      <div className="avail-fac">
        <strong>{f.name}</strong>
        <span>{f.sport}</span>
      </div>
      <div className="avail-lane">
        {slots.map((m) => !busy.has(m) && (
          <button
            key={m} className={`avail-slot ${m % 60 === 0 ? 'hour' : ''} ${held.has(m) ? 'held' : ''}`} style={{ left: x(m), width: w }}
            disabled={readOnly || held.has(m) || (nowMin != null && m + 30 <= nowMin)}
            title={held.has(m) ? 'Held for District arrivals (court not yet assigned)' : undefined}
            onClick={() => onFree?.(f, m)}
            aria-label={held.has(m) ? `${f.name} ${time12(m)} held for District arrivals` : `${f.name} ${time12(m)} available — book`}
          />
        ))}
        {cells.map((c) => <Alloc key={c.allocation_id} c={c} left={x(c.start_min)} width={x(c.end_min) - x(c.start_min)} onOpen={onOpen} />)}
      </div>
    </>
  )
}

function Alloc({ c, left, width, onOpen }) {
  const k = KIND[c.kind]
  const flag = c.payment_status === 'overdue' || (c.kind !== 'block' && c.payment_status === 'to_collect' && c.attendance === 'attended')
  let meta = c.time
  if (c.kind === 'district' || c.kind === 'direct') {
    meta = `${money(c.amount)} · ${PAY[c.payment_status] || ''}${c.attendance === 'attended' ? ' · In' : ''}`
  } else if (c.kind === 'community') {
    meta = `${c.participants}/${c.capacity} · ${money(c.per_person)}/person`
  } else if (c.kind === 'academy' || c.kind === 'corporate') {
    meta = money(c.amount)
  } else if (c.kind === 'block') {
    meta = c.note || c.time
  }
  return (
    <button
      className={`alloc ${c.kind === 'block' ? 'block' : ''}`}
      style={{ left: left + 3, width: width - 6, '--k': k.color }}
      onClick={() => onOpen?.(c)}
      aria-label={`${k.label}: ${c.title}, ${c.time}${flag ? ', needs attention' : ''}`}
    >
      <span className="alloc-type">{k.label}{c.moved ? ' · moved' : ''}</span>
      <span className="alloc-title">{c.title}</span>
      {width > 90 && <span className="alloc-meta">{meta}</span>}
      {flag && <span className="flag" aria-hidden />}
    </button>
  )
}

const PAY = { paid: 'Paid', to_collect: 'To collect', overdue: 'Overdue' }

export function GridLegend() {
  return (
    <div className="legend">
      {Object.entries(KIND).map(([k, v]) => (
        <span key={k}><span className="kind-dot" style={{ background: v.color }} />{v.label}</span>
      ))}
      <span><span className="kind-dot" style={{ background: 'color-mix(in srgb, var(--k-district) 30%, var(--surface))' }} />Held for District</span>
      <span><span className="kind-dot" style={{ background: 'var(--bad)', borderRadius: '50%' }} />Needs attention</span>
    </div>
  )
}
