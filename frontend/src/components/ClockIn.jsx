/** VM attendance: clock in on site (location), then three live camera photos (venue, branding, toilets) and an optional fourth. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, CheckCircle2, Lock, LogOut, MapPin, RotateCcw, Send } from 'lucide-react'
import { post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction } from '../lib/useCtx'
import { range12 } from '../lib/format'
import { clockTime, getFix } from '../lib/attendance'
import { ErrorState, PageSkeleton } from './ui'

const kmOrM = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`)

/** Shown instead of the venue until the VM has clocked in there today and sent the photos. */
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
    const r = await run(async () => post(`/api/venues/${venueId}/clock-in`, await getFix()), 'Clocked in. Now take the three photos.')
    setLocating(false)
    if (r) st.reload()
  }

  return (
    <div className="card card-pad stack rise" style={{ maxWidth: 560 }}>
      <div className="row"><Lock size={20} color="var(--brand)" aria-hidden /><h2>Clock in to open {s.venue.name}</h2></div>
      {s.shift && <p className="muted">Your shift: <b>{range12(s.shift.start_min, s.shift.end_min)}</b></p>}
      <p className="muted">
        The venue opens for you once you&apos;re at it (within {kmOrM(s.radius_m)}) and you&apos;ve sent three photos from
        your phone&apos;s camera: the venue, the branding and the toilets (a fourth is optional). Operations receives them by email.
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

const PHOTO_STEPS = [
  { kind: 'venue', title: 'Venue', hint: 'The courts and the entrance.' },
  { kind: 'branding', title: 'Branding', hint: 'RallyGully banners and signage.' },
  { kind: 'toilets', title: 'Toilets', hint: 'Inside the toilets.' },
]
const OTHER_STEP = { kind: 'other', title: 'Other', hint: 'Anything else Operations should see (optional).' }

/** Live camera only: no gallery or file picker, so each photo is taken there and then. Three photos are compulsory
 *  (venue, branding, toilets), a fourth is optional; they are sent together. */
function VenueCamera({ venueId, venueName, onSent }) {
  const video = useRef(null)
  const stream = useRef(null)
  const [error, setError] = useState(null)
  const [shots, setShots] = useState({})
  const [kind, setKind] = useState('venue')
  const [phase, setPhase] = useState('shoot') // shoot | ask (offer the optional fourth photo)
  const [run, busy] = useAction()

  const [attempt, setAttempt] = useState(0)  // bump to (re)open the camera: retry, retake, next photo
  const unsupported = !window.isSecureContext || !navigator.mediaDevices?.getUserMedia
  const step = kind === 'other' ? OTHER_STEP : PHOTO_STEPS.find((x) => x.kind === kind)
  const shot = shots[kind]
  const live = phase === 'shoot' && !shot
  const stop = useCallback(() => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null }, [])

  useEffect(() => {
    if (unsupported || !live) return undefined
    let alive = true
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      .then((s) => {
        if (!alive) { s.getTracks().forEach((t) => t.stop()); return }  // screen closed while the camera opened
        stream.current = s
        if (video.current) video.current.srcObject = s
      })
      .catch((e) => {
        if (alive) {
          setError(e.name === 'NotAllowedError'
            ? 'Camera permission is off for Pulse. Allow the camera for this site in your browser settings, then tap Try again.'
            : "Couldn't open the camera. Close other apps using it and tap Try again.")
        }
      })
    return () => { alive = false; stop() }
  }, [attempt, unsupported, live, stop])

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
    setShots((x) => ({ ...x, [kind]: c.toDataURL('image/jpeg', 0.75) }))
    stop()
  }
  const retake = () => { setShots((x) => { const n = { ...x }; delete n[kind]; return n }); setAttempt((n) => n + 1) }
  const next = () => {
    const i = PHOTO_STEPS.findIndex((x) => x.kind === kind)
    if (i >= 0 && i < PHOTO_STEPS.length - 1) { setKind(PHOTO_STEPS[i + 1].kind); setAttempt((n) => n + 1) } else setPhase('ask')
  }
  const addOther = () => { setKind('other'); setPhase('shoot'); setAttempt((n) => n + 1) }
  const send = async () => {
    const r = await run(() => post(`/api/venues/${venueId}/clock-in/photos`, shots), 'Photos sent to Operations. The venue is open.')
    if (r) onSent()
  }
  const count = Object.keys(shots).length
  const required = PHOTO_STEPS.findIndex((x) => x.kind === kind) + 1

  if (phase === 'ask') {
    return (
      <div className="camera-sheet" role="dialog" aria-modal="true" aria-label={`Photos of ${venueName}`}>
        <div className="camera-head">
          <h2>All three photos taken</h2>
          <p className="subtle">Venue, branding and toilets are ready. Add another photo if there's anything else Operations should see, or send these.</p>
        </div>
        <div className="camera-view">
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 6, padding: 12 }}>
            {PHOTO_STEPS.map((x) => <img key={x.kind} src={shots[x.kind]} alt={`${x.title} photo`} style={{ width: '100%', borderRadius: 8 }} />)}
          </div>
        </div>
        <div className="camera-actions">
          <button className="btn" disabled={busy} onClick={addOther}><Camera /> Add another photo</button>
          <button className="btn btn-primary" disabled={busy} onClick={send}><Send /> {busy ? 'Sending…' : `Send ${count} photos & open venue`}</button>
        </div>
      </div>
    )
  }

  return (
    <div className="camera-sheet" role="dialog" aria-modal="true" aria-label={`Photo of ${venueName}`}>
      <div className="camera-head">
        <h2>{kind === 'other' ? 'Optional photo' : `Photo ${required} of 3 · ${step.title}`}</h2>
        <p className="subtle">{step.hint} {kind === 'other' ? '' : 'All three photos are compulsory and go to Operations with your clock-in.'}</p>
      </div>
      <div className="camera-view">
        {shownError && <div className="callout warn" style={{ margin: 16 }}><Camera />{shownError}</div>}
        {!shownError && !shot && <video ref={attach} autoPlay playsInline muted />}
        {shot && <img src={shot} alt={`${step.title} photo preview`} />}
      </div>
      <div className="camera-actions">
        {error && !unsupported && <button className="btn btn-primary" onClick={() => { setError(null); setAttempt((n) => n + 1) }}><RotateCcw /> Try again</button>}
        {!shownError && !shot && <button className="btn btn-primary camera-shutter" onClick={capture}><Camera /> Take photo</button>}
        {shot && (
          <>
            <button className="btn" disabled={busy} onClick={retake}><RotateCcw /> Retake</button>
            {kind === 'other'
              ? <button className="btn btn-primary" disabled={busy} onClick={send}><Send /> {busy ? 'Sending…' : `Send ${count} photos & open venue`}</button>
              : <button className="btn btn-primary" disabled={busy} onClick={next}>{kind === 'toilets' ? 'Done' : `Next: ${PHOTO_STEPS[required].title}`}</button>}
          </>
        )}
      </div>
    </div>
  )
}

/** Clock-in status for the VM's own shift. There is no clock-out button: closing the day is the out-attendance. */
export function MyShift({ attendance }) {
  if (!attendance) return null
  if (attendance.clock_out_at) {
    return <span className="badge"><LogOut size={13} aria-hidden /> Clocked out {clockTime(attendance.clock_out_at)}</span>
  }
  return (
    <span className="badge good" title="Closing the day, after your shift ends, clocks you out.">
      <CheckCircle2 size={13} aria-hidden /> Clocked in {clockTime(attendance.clock_in_at)}
    </span>
  )
}
