/** VM attendance: clock in on site (location), then a live camera photo of the venue; clock out at the end. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, CheckCircle2, Lock, LogOut, MapPin, RotateCcw, Send } from 'lucide-react'
import { post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/useCtx'
import { range12 } from '../lib/format'
import { clockTime, getFix } from '../lib/attendance'
import { ErrorState, PageSkeleton } from './ui'

const kmOrM = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`)

/** Shown instead of the venue until the VM has clocked in there today and sent the venue photo. */
export function ClockInGate({ venueId, date, onUnlocked }) {
  const st = useApi(`/api/venues/${venueId}/attendance?date=${date}`)
  const [run, busy] = useAction()
  const [locating, setLocating] = useState(false)

  if (st.error && !st.data) return <ErrorState error={st.error} onRetry={st.reload} />
  if (!st.data) return <PageSkeleton />
  const s = st.data
  if (s.clocked_in && !s.unlocked) return <VenueCamera venueId={venueId} venueName={s.venue.name} onSent={onUnlocked} />

  const clockIn = async () => {
    setLocating(true)
    const r = await run(async () => post(`/api/venues/${venueId}/clock-in`, await getFix()), 'Clocked in. Now take a photo of the venue.')
    setLocating(false)
    if (r) st.reload()
  }

  return (
    <div className="card card-pad stack rise" style={{ maxWidth: 560 }}>
      <div className="row"><Lock size={20} color="var(--brand)" aria-hidden /><h2>Clock in to open {s.venue.name}</h2></div>
      {s.shift && <p className="muted">Your shift: <b>{range12(s.shift.start_min, s.shift.end_min)}</b></p>}
      <p className="muted">
        The venue opens for you once you&apos;re at it (within {kmOrM(s.radius_m)}) and you&apos;ve sent a photo of the venue
        from your phone&apos;s camera. Operations receives the photo by email.
      </p>
      {s.blocker
        ? <div className="callout warn"><MapPin />{s.blocker}</div>
        : (
          <button className="btn btn-primary" style={{ justifySelf: 'start' }} disabled={busy || locating || !s.can_clock_in} onClick={clockIn}>
            <MapPin /> {locating ? 'Finding your location…' : "I'm at the venue — clock in"}
          </button>
        )}
      <p className="subtle" style={{ fontSize: 12.5 }}>Your phone will ask to share your location. Allow it, with GPS / precise location on.</p>
    </div>
  )
}

/** Live camera only: no gallery or file picker, so the photo is taken there and then. */
function VenueCamera({ venueId, venueName, onSent }) {
  const video = useRef(null)
  const stream = useRef(null)
  const [error, setError] = useState(null)
  const [shot, setShot] = useState(null)
  const [run, busy] = useAction()

  const [attempt, setAttempt] = useState(0)  // bump to (re)open the camera: retry, retake
  const unsupported = !window.isSecureContext || !navigator.mediaDevices?.getUserMedia
  const stop = useCallback(() => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null }, [])

  useEffect(() => {
    if (unsupported) return undefined
    let live = true
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      .then((s) => {
        if (!live) { s.getTracks().forEach((t) => t.stop()); return }  // screen closed while the camera opened
        stream.current = s
        if (video.current) video.current.srcObject = s
      })
      .catch((e) => {
        if (live) {
          setError(e.name === 'NotAllowedError'
            ? 'Camera permission is off for Pulse. Allow the camera for this site in your browser settings, then tap Try again.'
            : "Couldn't open the camera. Close other apps using it and tap Try again.")
        }
      })
    return () => { live = false; stop() }
  }, [attempt, unsupported, stop])

  // Attach the stream whenever the <video> mounts (first open, retry, retake).
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

  const send = async () => {
    const r = await run(() => post(`/api/venues/${venueId}/clock-in/photo`, { image: shot }), 'Photo sent to Operations. The venue is open.')
    if (r) onSent()
  }

  return (
    <div className="camera-sheet" role="dialog" aria-modal="true" aria-label={`Photo of ${venueName}`}>
      <div className="camera-head">
        <h2>Photo of {venueName}</h2>
        <p className="subtle">Show the venue: courts and entrance. This goes to Operations with your clock-in.</p>
      </div>
      <div className="camera-view">
        {shownError && <div className="callout warn" style={{ margin: 16 }}><Camera />{shownError}</div>}
        {!shownError && !shot && <video ref={attach} autoPlay playsInline muted />}
        {shot && <img src={shot} alt="Venue photo preview" />}
      </div>
      <div className="camera-actions">
        {error && !unsupported && <button className="btn btn-primary" onClick={() => { setError(null); setAttempt((n) => n + 1) }}><RotateCcw /> Try again</button>}
        {!shownError && !shot && <button className="btn btn-primary camera-shutter" onClick={capture}><Camera /> Take photo</button>}
        {shot && (
          <>
            <button className="btn" disabled={busy} onClick={() => { setShot(null); setAttempt((n) => n + 1) }}><RotateCcw /> Retake</button>
            <button className="btn btn-primary" disabled={busy} onClick={send}><Send /> {busy ? 'Sending…' : 'Send & open venue'}</button>
          </>
        )}
      </div>
    </div>
  )
}

/** Clock-in status and the clock-out button, for the VM's own shift on the venue page. */
export function MyShift({ venueId, attendance, onChanged }) {
  const [run, busy] = useAction()
  const [locating, setLocating] = useState(false)
  if (!attendance) return null
  if (attendance.clock_out_at) {
    return <span className="badge"><LogOut size={13} aria-hidden /> Clocked out {clockTime(attendance.clock_out_at)}</span>
  }
  const out = async () => {
    if (!window.confirm('Clock out now? You can still view the day, but this ends your shift record.')) return
    setLocating(true)
    const r = await run(async () => post(`/api/venues/${venueId}/clock-out`, await getFix()), 'Clocked out')
    setLocating(false)
    if (r) onChanged()
  }
  return (
    <span className="row" style={{ gap: 8 }}>
      <span className="badge good"><CheckCircle2 size={13} aria-hidden /> Clocked in {clockTime(attendance.clock_in_at)}</span>
      <button className="btn btn-sm" disabled={busy || locating} onClick={out}><LogOut /> {locating ? 'Finding location…' : 'Clock out'}</button>
    </span>
  )
}
