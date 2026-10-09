import { ChevronLeft, ChevronRight } from 'lucide-react'
import { addDays, longDate, todayISO } from '../lib/format'

export default function DateNav({ value, onChange, maxToday = false }) {
  const today = todayISO()
  return (
    <div className="row" style={{ gap: 6 }}>
      <button className="btn btn-icon btn-sm" onClick={() => onChange(addDays(value, -1))} aria-label="Previous day"><ChevronLeft /></button>
      <input className="input" type="date" value={value} max={maxToday ? today : undefined}
        onChange={(e) => e.target.value && onChange(e.target.value)} style={{ width: 150, minHeight: 30, padding: '3px 8px' }}
        aria-label="Date" />
      <button className="btn btn-icon btn-sm" onClick={() => onChange(addDays(value, 1))} aria-label="Next day"
        disabled={maxToday && value >= today}><ChevronRight /></button>
      {value !== today && <button className="btn btn-sm" onClick={() => onChange(today)}>Today</button>}
      <span className="subtle datenav-label" style={{ fontSize: 13 }}>{longDate(value)}</span>
    </div>
  )
}
