import { useEffect, useMemo, useRef, useState } from 'react'
import { KIND, KINDS, money, pct, shortDate } from '../lib/format'

const PAD = { l: 46, r: 12, t: 12, b: 26 }

/**
 * Line chart on one y-axis (never dual-axis). Series: [{key, label, color, dashed}].
 * Hover: crosshair + tooltip; hit target is the full column, larger than the mark.
 */
export function LineChart({ data, series, x = 'date', height = 220, format = (v) => v, xFormat = shortDate,
  yFormat, area = true, ariaLabel }) {
  const ref = useRef(null)
  const box = useRef(null)
  const [hover, setHover] = useState(null)
  const W = useWidth(box)
  const pad = PAD
  const { maxY, pts } = useMemo(() => {
    const vals = data.flatMap((d) => series.map((s) => d[s.key])).filter((v) => v != null)
    const raw = Math.max(1e-9, ...vals)
    const step = niceStep(raw / 4)
    const max = Math.ceil(raw / step) * step || 1
    const n = Math.max(1, data.length - 1)
    const pts2 = series.map((s) => data.map((d, i) => d[s.key] == null ? null : [
      pad.l + (i / n) * (W - pad.l - pad.r),
      pad.t + (1 - d[s.key] / max) * (height - pad.t - pad.b),
    ]))
    return { maxY: max, pts: pts2 }
  }, [data, series, height, W, pad])
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * maxY)
  const fmtY = yFormat || format
  const n = Math.max(1, data.length - 1)
  const labelEvery = Math.ceil(data.length / 7)

  const onMove = (e) => {
    const r = ref.current.getBoundingClientRect()
    const px = ((e.clientX - r.left) / r.width) * W
    const i = Math.round(((px - pad.l) / (W - pad.l - pad.r)) * n)
    setHover(Math.max(0, Math.min(data.length - 1, i)))
  }
  const hx = hover != null ? pad.l + (hover / n) * (W - pad.l - pad.r) : null

  return (
    <div className="chart" ref={box} onMouseLeave={() => setHover(null)}>
      <svg ref={ref} viewBox={`0 0 ${W} ${height}`} role="img" aria-label={ariaLabel} onMouseMove={onMove}
        style={{ height }}>
        {ticks.map((t) => {
          const y = pad.t + (1 - t / maxY) * (height - pad.t - pad.b)
          return (
            <g key={t}>
              <line className="gridline" x1={pad.l} x2={W - pad.r} y1={y} y2={y} />
              <text className="axis" x={pad.l - 8} y={y + 4} textAnchor="end">{fmtY(t)}</text>
            </g>
          )
        })}
        {data.map((d, i) => (i % labelEvery === 0 || i === data.length - 1) && (
          <text key={i} className="axis" x={pad.l + (i / n) * (W - pad.l - pad.r)} y={height - 6} textAnchor="middle">
            {xFormat(d[x])}
          </text>
        ))}
        {series.map((s, si) => {
          const segs = toPath(pts[si])
          return (
            <g key={s.key}>
              {area && !s.dashed && segs.area && (
                <path d={segs.area(height - pad.b)} fill={s.color} opacity={0.08} />
              )}
              <path d={segs.line} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round"
                strokeLinecap="round" strokeDasharray={s.dashed ? '5 4' : undefined} />
            </g>
          )
        })}
        {hx != null && (
          <g>
            <line x1={hx} x2={hx} y1={pad.t} y2={height - pad.b} stroke="var(--border-strong)" />
            {series.map((s, si) => pts[si][hover] && (
              <circle key={s.key} cx={pts[si][hover][0]} cy={pts[si][hover][1]} r={4} fill={s.color}
                stroke="var(--surface)" strokeWidth={2} />
            ))}
          </g>
        )}
      </svg>
      {hover != null && (
        <div className="chart-tip" style={{ left: `clamp(0px, calc(${(hx / W) * 100}% - 70px), calc(100% - 160px))`, top: 4 }}>
          <div className="t-date">{xFormat(data[hover][x])}</div>
          {series.map((s) => (
            <div key={s.key} className="t-row">
              <span className="row" style={{ gap: 6, color: s.color }}>
                <span className={`swatch ${s.dashed ? 'dash' : ''}`} style={{ background: s.color }} />
                <span style={{ color: 'var(--text-2)' }}>{s.label}</span>
              </span>
              <b>{data[hover][s.key] == null ? '—' : format(data[hover][s.key])}</b>
            </div>
          ))}
        </div>
      )}
      {series.length > 1 && (
        <div className="legend" style={{ marginTop: 8 }}>
          {series.map((s) => (
            <span key={s.key} style={{ color: s.color }}>
              <span className={`swatch ${s.dashed ? 'dash' : ''}`} style={{ background: s.color, width: 14 }} />
              <span style={{ color: 'var(--text-2)' }}>{s.label}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function useWidth(ref) {
  const [w, setW] = useState(640)
  useEffect(() => {
    if (!ref.current) return undefined
    const ro = new ResizeObserver(([e]) => setW(Math.max(240, Math.round(e.contentRect.width))))
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [ref])
  return w
}

function toPath(points) {
  let line = ''
  let started = false
  const valid = []
  points.forEach((p) => {
    if (!p) { started = false; return }
    line += `${started ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`
    started = true
    valid.push(p)
  })
  const area = valid.length > 1 ? (base) =>
    `M${valid[0][0]},${base}` + valid.map((p) => `L${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('') + `L${valid[valid.length - 1][0]},${base}Z`
    : null
  return { line, area }
}

function niceStep(raw) {
  const p = 10 ** Math.floor(Math.log10(raw || 1))
  const m = raw / p
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10) * p
}

/** Channel mix: one stacked bar with 2px gaps + labelled rows (identity never colour-alone). */
export function ChannelMix({ values, format = money, title }) {
  const total = KINDS.reduce((a, k) => a + Math.max(0, values?.[k] || 0), 0)
  return (
    <div className="mix" aria-label={title}>
      <div className="mix-bar" role="img" aria-label={`${title}: ` + KINDS.map((k) => `${KIND[k].label} ${pct(total ? (values?.[k] || 0) / total : 0)}`).join(', ')}>
        {KINDS.map((k) => total > 0 && values?.[k] > 0 && (
          <span key={k} style={{ width: `${(values[k] / total) * 100}%`, background: KIND[k].color }} title={`${KIND[k].label}: ${format(values[k])}`} />
        ))}
      </div>
      <div className="mix-rows">
        {KINDS.map((k) => (
          <div key={k} className="mix-row">
            <span className="kind-label"><span className="kind-dot" style={{ background: KIND[k].color }} />{KIND[k].label}</span>
            <span className="v">{format(values?.[k] || 0)}</span>
            <span className="p">{total ? pct((values?.[k] || 0) / total) : '—'}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

export function Sparkline({ values, color = 'var(--brand)', height = 32 }) {
  const v = values.filter((x) => x != null)
  if (v.length < 2) return null
  const max = Math.max(...v)
  const min = Math.min(...v)
  const W = 120
  const d = values.map((x, i) => x == null ? null : [
    (i / (values.length - 1)) * W, height - 3 - ((x - min) / (max - min || 1)) * (height - 6)]).filter(Boolean)
  return (
    <svg viewBox={`0 0 ${W} ${height}`} width={W} height={height} aria-hidden style={{ display: 'block' }}>
      <path d={d.map((p, i) => `${i ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`).join('')} fill="none"
        stroke={color} strokeWidth={1.75} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}
