import { Seg } from './ui'
import { WINDOWS } from './badges'
import { addDays, todayISO } from '../lib/format'

/** Prev day / Same weekday / 7d / 30d / 90d / MTD / Custom, with the custom range inline. */
export default function BaselinePicker({ baseline }) {
  const { win, setWin, range, setRange } = baseline
  const pick = (v) => {
    if (v === 'custom' && !range) setRange({ from: addDays(todayISO(), -14), to: addDays(todayISO(), -8) })
    setWin(v)
  }
  return (
    <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
      <Seg value={win} onChange={pick} options={WINDOWS} label="Comparison baseline" />
      {win === 'custom' && range && (
        <div className="row" style={{ gap: 6 }}>
          <input className="input" type="date" aria-label="Compare from" value={range.from} max={range.to}
            onChange={(e) => e.target.value && setRange({ ...range, from: e.target.value })} style={{ width: 150, minHeight: 32, padding: '3px 8px' }} />
          <span className="subtle">to</span>
          <input className="input" type="date" aria-label="Compare to" value={range.to} min={range.from}
            onChange={(e) => e.target.value && setRange({ ...range, to: e.target.value })} style={{ width: 150, minHeight: 32, padding: '3px 8px' }} />
        </div>
      )}
    </div>
  )
}
