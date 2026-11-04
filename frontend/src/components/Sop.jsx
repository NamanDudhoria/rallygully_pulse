/** Daily SOP: photos the VM takes during the day in four categories, with the live camera only (never the gallery).
 *  Venue, branding and toilets are compulsory before the day can be closed; "other" is optional. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, Check, CheckCircle2, RotateCcw, Trash2 } from 'lucide-react'
import { apiBlobUrl, del, post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { ErrorState, Skeleton } from './ui'

/** Live camera only, as at clock-in: no gallery or file picker. Each photo is uploaded as soon as it is accepted,
 *  and the camera stays open for the next one until the VM taps Done. */
function SopCamera({ venueId, cat, onClose, onChanged }) {
  const video = useRef(null)
  const stream = useRef(null)
  const [error, setError] = useState(null)
  const [shot, setShot] = useState(null)
  const [sent, setSent] = useState(0)
  const [attempt, setAttempt] = useState(0)
  const [run, busy] = useAction()
  const unsupported = !window.isSecureContext || !navigator.mediaDevices?.getUserMedia
  const stop = useCallback(() => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null }, [])

  useEffect(() => {
    if (unsupported || shot) return undefined
    let alive = true
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      .then((st) => {
        if (!alive) { st.getTracks().forEach((t) => t.stop()); return }
        stream.current = st
        if (video.current) video.current.srcObject = st
      })
      .catch((e) => {
        if (alive) {
          setError(e.name === 'NotAllowedError'
            ? 'Camera permission is off for Pulse. Allow the camera for this site in your browser settings, then tap Try again.'
            : "Couldn't open the camera. Close other apps using it and tap Try again.")
        }
      })
    return () => { alive = false; stop() }
  }, [attempt, unsupported, shot, stop])

  const attach = (el) => {
    video.current = el
    if (el && stream.current && el.srcObject !== stream.current) el.srcObject = stream.current
  }
  const shownError = unsupported ? 'The camera needs the secure (https) Pulse address on a phone browser such as Chrome or Safari.' : error

  const capture = () => {
    const v = video.current
    if (!v || !v.videoWidth) return
    const scale = Math.min(1, 1280 / Math.max(v.videoWidth, v.videoHeight))
    const c = document.createElement('canvas')
    c.width = Math.round(v.videoWidth * scale)
    c.height = Math.round(v.videoHeight * scale)
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
    setShot(c.toDataURL('image/jpeg', 0.75))
    stop()
  }
  const keep = async () => {
    const r = await run(() => post(`/api/venues/${venueId}/sop/photos`, { kind: cat.kind, image: shot }))
    if (r) { onChanged(r); setSent((n) => n + 1); setShot(null); setAttempt((n) => n + 1) }
  }
  const done = () => { stop(); onClose() }

  return (
    <div className="camera-sheet" role="dialog" aria-modal="true" aria-label={`${cat.label} photos`}>
      <div className="camera-head">
        <h2>{cat.label} photos{sent ? ` · ${sent} added` : ''}</h2>
        <p className="subtle">Take as many as you need. Each one is saved as soon as you tap Use photo.</p>
      </div>
      <div className="camera-view">
        {shownError && <div className="callout warn" style={{ margin: 16 }}><Camera />{shownError}</div>}
        {!shownError && !shot && <video ref={attach} autoPlay playsInline muted />}
        {shot && <img src={shot} alt={`${cat.label} photo preview`} />}
      </div>
      <div className="camera-actions">
        {error && !unsupported && <button className="btn btn-primary" onClick={() => { setError(null); setAttempt((n) => n + 1) }}><RotateCcw /> Try again</button>}
        {!shot && <button className="btn" onClick={done}><Check /> Done</button>}
        {!shownError && !shot && <button className="btn btn-primary camera-shutter" onClick={capture}><Camera /> Take photo</button>}
        {shot && (
          <>
            <button className="btn" disabled={busy} onClick={() => { setShot(null); setAttempt((n) => n + 1) }}><RotateCcw /> Retake</button>
            <button className="btn btn-primary" disabled={busy} onClick={keep}>{busy ? 'Saving…' : 'Use photo'}</button>
          </>
        )}
      </div>
    </div>
  )
}

function Thumb({ venueId, photo, canRemove, onRemove }) {
  const [src, setSrc] = useState(null)
  useEffect(() => {
    let url
    apiBlobUrl(`/api/venues/${venueId}/sop/photos/${photo.id}`).then((u) => { url = u; setSrc(u) }).catch(() => {})
    return () => { if (url) URL.revokeObjectURL(url) }
  }, [venueId, photo.id])
  return (
    <figure style={{ margin: 0, position: 'relative' }}>
      <a href={src || undefined} target="_blank" rel="noreferrer">
        {src ? <img src={src} alt={`Photo by ${photo.by || 'staff'}`} style={{ width: 110, height: 110, objectFit: 'cover', borderRadius: 8, display: 'block' }} />
          : <Skeleton h={110} w={110} />}
      </a>
      <figcaption className="subtle" style={{ fontSize: 11.5 }}>
        {new Date(photo.taken_at).toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}{photo.by ? ` · ${photo.by}` : ''}
      </figcaption>
      {canRemove && (
        <button type="button" className="btn btn-sm btn-icon" aria-label="Remove photo" onClick={onRemove}
          style={{ position: 'absolute', top: 4, right: 4 }}><Trash2 /></button>
      )}
    </figure>
  )
}

function Category({ venueId, cat, editable, onChanged, userName, isHQ }) {
  const [run] = useAction()
  const [shooting, setShooting] = useState(false)
  const done = cat.photos.length > 0
  return (
    <div className="card">
      <div className="card-head">
        <h2 className="row" style={{ gap: 8 }}>
          {done ? <CheckCircle2 size={18} color="var(--good)" aria-hidden /> : null}
          {cat.label} <span className="subtle" style={{ fontWeight: 400, fontSize: 13 }}>{cat.required ? 'compulsory' : 'optional'} · {cat.photos.length}</span>
        </h2>
        {editable && <button className="btn btn-sm btn-primary" onClick={() => setShooting(true)}><Camera /> Take photos</button>}
      </div>
      <div className="card-body">
        {cat.photos.length ? (
          <div className="row" style={{ gap: 10, flexWrap: 'wrap', alignItems: 'flex-start' }}>
            {cat.photos.map((p) => (
              <Thumb key={p.id} venueId={venueId} photo={p} canRemove={editable && (isHQ || p.by === userName)}
                onRemove={async () => {
                  if (!window.confirm('Remove this photo?')) return
                  const r = await run(() => del(`/api/venues/${venueId}/sop/photos/${p.id}`), 'Photo removed')
                  if (r) onChanged(r)
                }} />
            ))}
          </div>
        ) : <p className="subtle">{cat.required ? 'No photo yet.' : 'Nothing added.'}</p>}
      </div>
      {shooting && <SopCamera venueId={venueId} cat={cat} onClose={() => setShooting(false)} onChanged={onChanged} />}
    </div>
  )
}

export function SopTab({ venueId, date, canOperate, onChanged }) {
  const { user, isHQ } = useAuth()
  const { data, error, reload } = useApi(`/api/venues/${venueId}/sop?date=${date}`)
  const [st, setSt] = useState(null)
  const s = st && st.date === data?.date ? st : data
  if (error && !data) return <ErrorState error={error} onRetry={reload} />
  if (!s) return <Skeleton h={300} />
  const editable = s.editable && canOperate
  const changed = (next) => { setSt(next); onChanged?.() }
  return (
    <div className="stack">
      {!s.due ? <div className="callout info">No VM is rostered here on this day, so the daily SOP isn&apos;t due.</div>
        : s.complete ? <div className="callout good"><CheckCircle2 />Daily SOP done. Keep adding photos through the day if anything changes.</div>
          : <div className="callout warn">Still needed today: {s.missing.join(', ')}. Take them any time before closing the day; the day can&apos;t be closed without them.</div>}
      {s.categories.map((c) => (
        <Category key={c.kind} venueId={venueId} cat={c} editable={editable} onChanged={changed} userName={user?.name} isHQ={isHQ} />
      ))}
    </div>
  )
}
