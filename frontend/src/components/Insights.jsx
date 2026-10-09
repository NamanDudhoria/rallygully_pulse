import { Link } from 'react-router-dom'
import { TrendingDown, TrendingUp, Sparkles } from 'lucide-react'
import { Empty } from './ui'
import { money, num, pct, shortDate } from '../lib/format'

const fmt = (format, v) => (format === 'pct' ? pct(v) : format === 'inr' ? money(v) : num(v))

/** Explainable anomaly insights (PRD §56): what deviated, by how much, against which baseline. */
export default function Insights({ items, historyDays }) {
  if (!items?.length) {
    return (
      <Empty icon={Sparkles} title="Nothing unusual">
        {historyDays < 28
          ? `Pulse has ${historyDays} days of history; insights get sharper as history builds (≈4 same-weekday samples needed).`
          : 'All venues are within their normal historical range.'}
      </Empty>
    )
  }
  return (
    <div>
      {items.map((it, i) => {
        const down = it.direction === 'below'
        const Icon = down ? TrendingDown : TrendingUp
        return (
          <div key={i} className="insight">
            <div className={`insight-icon ${down ? 'down' : 'up'}`}><Icon aria-hidden /></div>
            <div>
              <h3>
                <Link to={`/venues/${it.venue_id}?date=${it.date}`}>{it.venue.replace('RallyGully ', '')}</Link>
                : {it.label} unusually {down ? 'low' : 'high'}
              </h3>
              <dl>
                <dt>Current</dt><dd>{fmt(it.format, it.current)} <span className="subtle">· {shortDate(it.date)}</span></dd>
                <dt>Normal range</dt><dd>{fmt(it.format, it.range_low)} – {fmt(it.format, it.range_high)} <span className="subtle">({it.basis})</span></dd>
                <dt>Deviation</dt>
                <dd>
                  {it.pct_from_median != null ? `${it.pct_from_median > 0 ? '+' : ''}${(it.pct_from_median * 100).toFixed(0)}% vs median` : 'from zero'}
                  {it.z != null && <span className="subtle"> · robust z {it.z}</span>}
                </dd>
              </dl>
              {it.correlated?.length > 0 && (
                <p className="also">
                  Also: {it.correlated.map((c) => `${c.label} ${c.pct_from_median != null ? `${c.pct_from_median > 0 ? '+' : ''}${(c.pct_from_median * 100).toFixed(0)}%` : c.direction}`).join(' · ')}
                </p>
              )}
              {it.driver && (
                <p className="also">
                  Largest facility deviation: <b>{it.driver.facility}</b> ({it.driver.sport}) at {pct(it.driver.current)} vs usual {pct(it.driver.median)}
                </p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
