/** Daily SOP: photos the VM uploads during the day in four categories. Venue, branding and toilets are compulsory
 *  before the day can be closed; "other" is optional. */
import { useEffect, useRef, useState } from 'react'
import { Camera, CheckCircle2, Trash2 } from 'lucide-react'
import { apiBlobUrl, del, post } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction, useAuth } from '../lib/useCtx'
import { ErrorState, Skeleton } from './ui'

/** Shrink a phone photo to at most 1280 px and re-encode it as JPEG, as the clock-in camera does. */
function toJpeg(file) {
  return new Promise((resolve, reject) => {
    const url = URL.createObjectURL(file)
    const img = new Image()
    img.onload = () => {
      const scale = Math.min(1, 1280 / Math.max(img.width, img.height))
      const c = document.createElement('canvas')
      c.width = Math.round(img.width * scale)
      c.height = Math.round(img.height * scale)
      c.getContext('2d').drawImage(img, 0, 0, c.width, c.height)
      URL.revokeObjectURL(url)
      resolve(c.toDataURL('image/jpeg', 0.75))
    }
    img.onerror = () => { URL.revokeObjectURL(url); reject(new Error("That file isn't a photo.")) }
    img.src = url
  })
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
  const input = useRef(null)
  const [run, busy] = useAction()
  const [sending, setSending] = useState(0)
  const upload = async (files) => {
    const list = [...files]
    setSending(list.length)
    let last = null
    for (const f of list) {
      const r = await run(async () => post(`/api/venues/${venueId}/sop/photos`, { kind: cat.kind, image: await toJpeg(f) }))
      if (r) last = r
      setSending((n) => n - 1)
    }
    if (input.current) input.current.value = ''
    if (last) onChanged(last)
  }
  const done = cat.photos.length > 0
  return (
    <div className="card">
      <div className="card-head">
        <h2 className="row" style={{ gap: 8 }}>
          {done ? <CheckCircle2 size={18} color="var(--good)" aria-hidden /> : null}
          {cat.label} <span className="subtle" style={{ fontWeight: 400, fontSize: 13 }}>{cat.required ? 'compulsory' : 'optional'} · {cat.photos.length}</span>
        </h2>
        {editable && (
          <>
            <input ref={input} type="file" accept="image/*" capture="environment" multiple hidden
              onChange={(e) => e.target.files.length && upload(e.target.files)} />
            <button className="btn btn-sm btn-primary" disabled={busy} onClick={() => input.current?.click()}>
              <Camera /> {sending ? `Uploading ${sending}…` : 'Add photos'}
            </button>
          </>
        )}
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
