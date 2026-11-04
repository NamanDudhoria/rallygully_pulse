/** One live-camera photo, never the gallery: take it, retake it, or use it. Used for bank receipts and expense proof. */
import { useCallback, useEffect, useRef, useState } from 'react'
import { Camera, RotateCcw, X } from 'lucide-react'

export function PhotoCamera({ title, hint, busy, useLabel = 'Use photo', onUse, onClose }) {
  const video = useRef(null)
  const stream = useRef(null)
  const [error, setError] = useState(null)
  const [shot, setShot] = useState(null)
  const [attempt, setAttempt] = useState(0)
  const unsupported = !window.isSecureContext || !navigator.mediaDevices?.getUserMedia
  const stop = useCallback(() => { stream.current?.getTracks().forEach((t) => t.stop()); stream.current = null }, [])

  useEffect(() => {
    if (unsupported || shot) return undefined
    let alive = true
    navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false })
      .then((s) => {
        if (!alive) { s.getTracks().forEach((t) => t.stop()); return }
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
  }, [attempt, unsupported, shot, stop])

  const attach = (el) => {
    video.current = el
    if (el && stream.current && el.srcObject !== stream.current) el.srcObject = stream.current
  }
  const capture = () => {
    const v = video.current
    if (!v || !v.videoWidth) return
    const scale = Math.min(1, 1280 / Math.max(v.videoWidth, v.videoHeight))
    const c = document.createElement('canvas')
    c.width = Math.round(v.videoWidth * scale)
    c.height = Math.round(v.videoHeight * scale)
    c.getContext('2d').drawImage(v, 0, 0, c.width, c.height)
    setShot(c.toDataURL('image/jpeg', 0.8))
    stop()
  }
  const shownError = unsupported ? 'The camera needs the secure (https) Pulse address on a phone browser such as Chrome or Safari.' : error
  return (
    <div className="camera-sheet" role="dialog" aria-modal="true" aria-label={title}>
      <div className="camera-head">
        <h2>{title}</h2>
        {hint && <p className="subtle">{hint}</p>}
      </div>
      <div className="camera-view">
        {shownError && <div className="callout warn" style={{ margin: 16 }}><Camera />{shownError}</div>}
        {!shownError && !shot && <video ref={attach} autoPlay playsInline muted />}
        {shot && <img src={shot} alt="Photo preview" />}
      </div>
      <div className="camera-actions">
        <button className="btn" onClick={() => { stop(); onClose() }}><X /> Cancel</button>
        {error && !unsupported && <button className="btn btn-primary" onClick={() => { setError(null); setAttempt((n) => n + 1) }}><RotateCcw /> Try again</button>}
        {!shownError && !shot && <button className="btn btn-primary camera-shutter" onClick={capture}><Camera /> Take photo</button>}
        {shot && (
          <>
            <button className="btn" disabled={busy} onClick={() => { setShot(null); setAttempt((n) => n + 1) }}><RotateCcw /> Retake</button>
            <button className="btn btn-primary" disabled={busy} onClick={() => onUse(shot)}>{useLabel}</button>
          </>
        )}
      </div>
    </div>
  )
}
