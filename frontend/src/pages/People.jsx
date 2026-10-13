/** People & access (users.manage) and the daily VM roster (roster.manage). */
import { useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Copy, Lock, Mail, Plus, Search, X } from 'lucide-react'
import { del, patch, post, put } from '../lib/api'
import { useApi, useLocalState } from '../lib/hooks'
import { useAction, useAuth, useToast } from '../lib/useCtx'
import { addDays, dateTime, hhmm, parseISO, range12, shortDate, time12, todayISO } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, Seg, Skeleton } from '../components/ui'

const ROLE_TONE = { hq_admin: 'brand', hq: 'brand', community: 'info', academy: 'warn', vm: undefined }

function statusBadge(s) {
  if (s === 'active') return <Badge tone="good">Active</Badge>
  if (s === 'invited') return <Badge tone="info" icon={Mail}>Invited</Badge>
  return <Badge>Disabled</Badge>
}

// ── People ───────────────────────────────────────────────────────────────────
export function PeoplePage() {
  const { data, error, reload } = useApi('/api/users')
  const roles = useApi('/api/roles').data
  const venues = useApi('/api/venues').data || []
  const [edit, setEdit] = useState(null)
  const [role, setRole] = useState('all')
  const [q, setQ] = useState('')
  const rows = (data || []).filter((u) => (role === 'all' || u.role === role)
    && (!q || `${u.name} ${u.email}`.toLowerCase().includes(q.toLowerCase())))
  const label = (r) => roles?.roles.find((x) => x.value === r)?.label || r

  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>People &amp; access</h1></div>
        <button className="btn btn-primary" onClick={() => setEdit({ name: '', email: '', role: 'vm', venue_ids: [], active: true })}><Plus /> Add person</button>
      </div>
      <div className="spread">
        <div className="seg wrap" role="group" aria-label="Filter by role">
          {[['all', 'Everyone'], ...(roles?.roles || []).map((r) => [r.value, r.label])].map(([v, l]) => (
            <button key={v} type="button" aria-pressed={role === v} onClick={() => setRole(v)}>
              {l} <span className="subtle">{v === 'all' ? data?.length ?? '' : (data || []).filter((u) => u.role === v).length}</span>
            </button>
          ))}
        </div>
        <div style={{ position: 'relative', width: 240 }}>
          <Search size={15} style={{ position: 'absolute', left: 10, top: 11, color: 'var(--text-3)' }} aria-hidden />
          <input className="input" style={{ paddingLeft: 32 }} placeholder="Search name or email" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search people" />
        </div>
      </div>
      <div className="card"><div className="card-body table-wrap">
        {error ? <ErrorState error={error} onRetry={reload} /> : !data ? <Skeleton h={260} /> : !rows.length ? <Empty title="Nobody matches" /> : (
          <table className="table">
            <thead><tr><th>Person</th><th>Role</th><th>Venue team</th><th>Status</th><th>Last sign-in</th></tr></thead>
            <tbody>{rows.map((u) => (
              <tr key={u.id} className="clickable" onClick={() => setEdit(u)}>
                <td><div style={{ fontWeight: 500 }}>{u.name}</div><div className="subtle" style={{ fontSize: 12.5 }}>{u.email}</div></td>
                <td><Badge tone={ROLE_TONE[u.role]}>{u.role_label}</Badge></td>
                <td>{u.role === 'vm' ? (u.venue_ids.map((id) => venues.find((v) => v.id === id)?.code).filter(Boolean).join(', ') || <span className="subtle">No venues</span>) : <span className="subtle">—</span>}</td>
                <td>{statusBadge(u.status)}</td>
                <td className="subtle">{u.last_login_at ? dateTime(u.last_login_at) : 'Never'}</td>
              </tr>))}
            </tbody>
          </table>
        )}
      </div></div>
      {roles && <RoleMatrix roles={roles} />}
      {edit && <PersonDrawer person={edit} roles={roles} venues={venues} label={label} onClose={() => setEdit(null)} onSaved={reload} />}
    </div>
  )
}

function RoleMatrix({ roles }) {
  return (
    <div className="card">
      <div className="card-head"><h2>What each role can access</h2><span className="hint" /></div>
      <div className="card-body table-wrap">
        <table className="table">
          <thead><tr><th>Access</th>{roles.roles.map((r) => <th key={r.value} style={{ textAlign: 'center' }}>{r.label}</th>)}</tr></thead>
          <tbody>{Object.entries(roles.permissions).map(([p, desc]) => (
            <tr key={p}>
              <td>{desc}</td>
              {roles.roles.map((r) => (
                <td key={r.value} style={{ textAlign: 'center' }}>
                  {r.permissions.includes(p)
                    ? <span aria-label="Yes" style={{ color: 'var(--good)', fontWeight: 700 }}>✓{r.value === 'vm' && p === 'venue.operate' ? <span className="subtle" style={{ fontWeight: 400, fontSize: 11.5 }}> rostered days</span> : ''}</span>
                    : <span aria-label="No" className="subtle">—</span>}
                </td>
              ))}
            </tr>))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function PersonDrawer({ person, roles, venues, onClose, onSaved }) {
  const { user: me } = useAuth()
  const toast = useToast()
  const [run, busy] = useAction()
  const [f, setF] = useState({ ...person })
  const [link, setLink] = useState(null)
  const isNew = !person.id
  const self = person.id === me.id
  const roleInfo = roles?.roles.find((r) => r.value === f.role)
  const handleLink = (res) => {
    if (res.link) setLink(res)
    else toast(res.purpose === 'invite' ? `Invite emailed to ${f.email}` : `Reset link emailed to ${f.email}`)
  }
  return (
    <Drawer open onClose={onClose} width={520} title={isNew ? 'Add person' : person.name}
      subtitle={isNew ? 'They get an email invite to set their own password' : `${person.email} · ${person.role_label}`}>
      {(close) => (
        <>
          {link && (
            <div className="callout info" style={{ display: 'grid', gap: 8 }}>
              <span><b>Email isn&apos;t configured on this server</b>, so share this {link.purpose === 'invite' ? 'invite' : 'reset'} link with {f.name} directly (it&apos;s single-use and expires in {link.purpose === 'invite' ? '72 hours' : '1 hour'}):</span>
              <div className="row">
                <input className="input mono" readOnly value={link.link} onFocus={(e) => e.target.select()} aria-label="Link" style={{ flex: 1 }} />
                <button type="button" className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(link.link).then(() => toast('Link copied'))}><Copy /> Copy</button>
              </div>
            </div>
          )}
          <form className="form" onSubmit={async (e) => {
            e.preventDefault()
            const body = { name: f.name, email: f.email, role: f.role, venue_ids: f.role === 'vm' ? f.venue_ids : [], active: f.active }
            const r = await run(() => (isNew ? post('/api/users', body) : put(`/api/users/${person.id}`, body)), isNew ? null : 'Saved')
            if (!r) return
            onSaved()
            if (isNew) { setF({ ...r }); handleLink(r.invite); if (!r.invite.link) close() } else close()
          }}>
            <Field label="Name"><input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} required /></Field>
            <Field label="Work email" help={isNew ? 'This is their sign-in. It can’t be changed later.' : null}>
              <input className="input" type="email" value={f.email} disabled={!isNew} onChange={(e) => setF({ ...f, email: e.target.value })} required />
            </Field>
            <Field label="Role" help={self ? "You can't change your own role." : roleInfo ? `Can: ${roleInfo.permissions.map((p) => roles.permissions[p].split(':')[0]).join(' · ')}` : null}>
              <select className="select" value={f.role} disabled={self} onChange={(e) => setF({ ...f, role: e.target.value })}>
                {(roles?.roles || []).map((r) => <option key={r.value} value={r.value}>{r.label}</option>)}
              </select>
            </Field>
            {f.role === 'vm' && (
              <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
                <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--text-2)' }}>Venue team</span>
                <span className="help subtle" style={{ fontSize: 12 }}>Venues this VM can be rostered at. Access itself follows the daily roster.</span>
                <div className="row">
                  {venues.map((v) => (
                    <label key={v.id} className="check badge" style={{ height: 32, padding: '0 10px' }}>
                      <input type="checkbox" checked={f.venue_ids.includes(v.id)}
                        onChange={(e) => setF({ ...f, venue_ids: e.target.checked ? [...f.venue_ids, v.id] : f.venue_ids.filter((x) => x !== v.id) })} />
                      {v.name.replace('RallyGully ', '')}
                    </label>
                  ))}
                </div>
              </fieldset>
            )}
            {!isNew && !self && (
              <label className="check"><input type="checkbox" checked={f.active} onChange={(e) => setF({ ...f, active: e.target.checked })} /> Active — unticking signs them out immediately</label>
            )}
            <div className="row" style={{ justifyContent: 'space-between' }}>
              {!isNew && person.active ? (
                <button type="button" className="btn" disabled={busy} onClick={async () => {
                  const r = await run(() => post(`/api/users/${person.id}/send-link`))
                  if (r) handleLink(r)
                }}><Mail /> {person.status === 'invited' ? 'Resend invite' : 'Send password reset'}</button>
              ) : <span />}
              <button className="btn btn-primary" disabled={busy}>{isNew ? 'Create & invite' : 'Save'}</button>
            </div>
          </form>
        </>
      )}
    </Drawer>
  )
}

// ── Roster ───────────────────────────────────────────────────────────────────
function mondayOf(iso) {
  const d = parseISO(iso)
  return addDays(iso, -((d.getDay() + 6) % 7))
}

const vname = (n) => n.replace('RallyGully ', '')
const short12 = (m) => time12(m).replace(':00', '').replace(' AM', 'a').replace(' PM', 'p')
const shiftLabel = (e) => (e.start_min == null ? '' : `${short12(e.start_min)}–${short12(e.end_min)}`)

export function RosterPage() {
  const [start, setStart] = useState(mondayOf(todayISO()))
  const [view, setView] = useLocalState('pulse.roster.view', 'venue')
  const [only, setOnly] = useLocalState('pulse.roster.vms', [])
  const [shift, setShift] = useState(null)
  const end = addDays(start, 6)
  const { data, error, reload } = useApi(`/api/roster?date_from=${start}&date_to=${end}`)
  const [run, busy] = useAction()
  const today = todayISO()
  const act = async (fn, msg) => { if (await run(fn, msg)) reload() }
  const vms = data?.vms || []
  const picked = only.filter((id) => vms.some((m) => m.id === id))
  const shown = (id) => !picked.length || picked.includes(id)
  const toggle = (id) => setOnly(picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id])
  // Every new roster entry goes straight to its shift times.
  const addTo = async (venue, userId, d) => {
    const vm = vms.find((m) => m.id === userId)
    const joining = !venue.team.some((t) => t.id === userId)
    const r = await run(() => post('/api/roster', { venue_id: venue.id, user_id: userId, date: d }),
      joining ? `${vm.name} rostered and added to ${vname(venue.name)}'s team` : null)
    if (!r) return
    reload()
    setShift({ entry: { id: r.id, venue_id: venue.id, user_id: userId, name: vm.name, date: d, custom: false }, venue })
  }
  const openShift = (e) => setShift({ entry: e, venue: data.venues.find((v) => v.id === e.venue_id) })

  return (
    <div className="stack rise">
      <div className="page-head">
        <div><h1>VM roster</h1></div>
        <div className="row">
          <button className="btn btn-icon btn-sm" onClick={() => setStart(addDays(start, -7))} aria-label="Previous week"><ChevronLeft /></button>
          <span style={{ minWidth: 150, textAlign: 'center', fontWeight: 500 }}>{shortDate(start)} – {shortDate(end)}</span>
          <button className="btn btn-icon btn-sm" onClick={() => setStart(addDays(start, 7))} aria-label="Next week"><ChevronRight /></button>
          {start !== mondayOf(today) && <button className="btn btn-sm" onClick={() => setStart(mondayOf(today))}>This week</button>}
          <button className="btn btn-sm" disabled={busy} onClick={() => act(() => post('/api/roster/copy', { from_date: addDays(start, -7), to_date: start, days: 7 }),
            'Copied last week')} title="Copy the previous week's roster into this week (keeps existing entries)"><Copy /> Copy last week</button>
        </div>
      </div>
      <div className="spread" style={{ gap: 12, flexWrap: 'wrap' }}>
        <Seg value={view} onChange={setView} label="Roster view" options={[{ value: 'venue', label: 'By venue' }, { value: 'vm', label: 'By VM' }]} />
        <div className="row" style={{ gap: 6, flexWrap: 'wrap' }} role="group" aria-label="Filter by VM">
          <span className="subtle" style={{ fontSize: 12.5 }}>VMs</span>
          <button className={`btn btn-sm ${!picked.length ? 'btn-primary' : ''}`} aria-pressed={!picked.length} onClick={() => setOnly([])}>All</button>
          {vms.map((m) => (
            <button key={m.id} className={`btn btn-sm ${picked.includes(m.id) ? 'btn-primary' : ''}`} aria-pressed={picked.includes(m.id)}
              onClick={() => toggle(m.id)}>{m.name}</button>
          ))}
        </div>
      </div>
      {data?.tomorrow_gaps?.length > 0 && (
        <div className="callout bad"><AlertTriangle />
          <span><b>No VM rostered for tomorrow</b> at {data.tomorrow_gaps.map(vname).join(', ')}. Nobody will be able to operate {data.tomorrow_gaps.length > 1 ? 'them' : 'it'} in Pulse; Operations is emailed from 8 PM if this is still open.</span>
        </div>
      )}
      {error ? <ErrorState error={error} onRetry={reload} /> : !data ? <Skeleton h={300} /> : view === 'vm'
        ? <RosterByVm data={data} today={today} shown={shown} busy={busy} onAdd={addTo} onShift={openShift} />
        : <RosterByVenue data={data} today={today} shown={shown} filtered={picked.length > 0} busy={busy} onAdd={addTo} onShift={openShift}
          onRemove={(e) => act(() => del(`/api/roster/${e.id}`), `${e.name} removed`)} />}
      {shift && <ShiftDrawer {...shift} onClose={() => setShift(null)} onDone={reload} />}
    </div>
  )
}

function DayHead({ d, today }) {
  const tomorrow = addDays(today, 1)
  return (
    <th style={{ color: d === today ? 'var(--brand)' : undefined }}>
      {parseISO(d).toLocaleDateString('en-IN', { weekday: 'short' })} {shortDate(d)}{d === today ? ' · Today' : d === tomorrow ? ' · Tmrw' : ''}
    </th>
  )
}

function ShiftChip({ e, label, onShift, onRemove, busy }) {
  return (
    <span className="badge brand roster-chip">
      <button type="button" className="roster-chip-main" onClick={() => onShift(e)} title="Set shift">
        <span>{label}</span>
        <span className="roster-chip-time">{e.custom ? shiftLabel(e) : 'Full day'}</span>
      </button>
      {onRemove && (
        <button type="button" className="roster-chip-x" aria-label={`Remove ${e.name}`} disabled={busy} onClick={() => onRemove(e)}><X size={13} /></button>
      )}
    </span>
  )
}

function RosterByVenue({ data, today, shown, filtered, busy, onAdd, onShift, onRemove }) {
  const venues = data.venues.filter((v) => !filtered || v.team.some((m) => shown(m.id))
    || data.entries.some((e) => e.venue_id === v.id && shown(e.user_id)))
  if (!venues.length) return <Empty title="No venues for the selected VMs this week" />
  return (
    <div className="card"><div className="card-body table-wrap">
      <table className="table" style={{ tableLayout: 'fixed', minWidth: 980 }}>
        <thead><tr><th style={{ width: 150 }}>Venue</th>{data.days.map((d) => <DayHead key={d} d={d} today={today} />)}</tr></thead>
        <tbody>{venues.map((v) => (
          <tr key={v.id}>
            <td style={{ verticalAlign: 'top' }}>
              <div style={{ fontWeight: 600 }}>{vname(v.name)}</div>
              <div className="subtle" style={{ fontSize: 12 }}>{v.team.length} on team</div>
            </td>
            {data.days.map((d) => {
              const on = data.entries.filter((e) => e.venue_id === v.id && e.date === d)
              const hrs = v.hours[d]
              const isOn = (m) => on.some((e) => e.user_id === m.id)
              const free = v.team.filter((m) => !isOn(m))
              const others = (data.vms || []).filter((m) => !isOn(m) && !v.team.some((t) => t.id === m.id))
              return (
                <td key={d} style={{ verticalAlign: 'top', background: !hrs ? 'var(--surface-sunk)' : undefined }}>
                  {!hrs ? <span className="subtle" style={{ fontSize: 12 }}>Closed</span> : (
                    <div style={{ display: 'grid', gap: 4 }}>
                      {on.map((e) => (
                        <div key={e.id} style={{ opacity: shown(e.user_id) ? 1 : 0.35 }}>
                          <ShiftChip e={e} label={e.name.split(' ')[0]} onShift={onShift} onRemove={onRemove} busy={busy} />
                        </div>
                      ))}
                      {!on.length && <span className="badge bad" title="No VM can open this venue in Pulse on this day"><Lock /> No VM</span>}
                      {free.length + others.length > 0 && (
                        <select className="select" value="" style={{ minHeight: 28, padding: '2px 6px', fontSize: 12.5 }}
                          aria-label={`Add VM to ${v.name} on ${d}`} disabled={busy}
                          onChange={(ev) => { const id = Number(ev.target.value); if (id) onAdd(v, id, d) }}>
                          <option value="">+ Add…</option>
                          {free.length > 0 && <optgroup label="Venue team">{free.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>}
                          {others.length > 0 && <optgroup label="Other VMs (joins team)">{others.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}</optgroup>}
                        </select>
                      )}
                    </div>
                  )}
                </td>
              )
            })}
          </tr>))}
        </tbody>
      </table>
    </div></div>
  )
}

/** Where each VM is positioned, day by day. */
function RosterByVm({ data, today, shown, busy, onAdd, onShift }) {
  const vms = (data.vms || []).filter((m) => shown(m.id))
  if (!vms.length) return <Empty title="No Venue Managers" />
  return (
    <div className="card"><div className="card-body table-wrap">
      <table className="table" style={{ tableLayout: 'fixed', minWidth: 980 }}>
        <thead><tr><th style={{ width: 150 }}>VM</th>{data.days.map((d) => <DayHead key={d} d={d} today={today} />)}</tr></thead>
        <tbody>{vms.map((m) => {
          const mine = data.entries.filter((e) => e.user_id === m.id)
          const days = new Set(mine.map((e) => e.date)).size
          return (
            <tr key={m.id}>
              <td style={{ verticalAlign: 'top' }}>
                <div style={{ fontWeight: 600 }}>{m.name}</div>
                <div className="subtle" style={{ fontSize: 12 }}>{days ? `${days} day${days > 1 ? 's' : ''} this week` : 'Not rostered'}</div>
              </td>
              {data.days.map((d) => {
                const on = mine.filter((e) => e.date === d)
                  .sort((a, b) => (a.start_min ?? 0) - (b.start_min ?? 0))
                const addable = data.venues.filter((v) => v.hours[d] && !on.some((e) => e.venue_id === v.id))
                return (
                  <td key={d} style={{ verticalAlign: 'top', background: d === today ? 'var(--brand-soft)' : undefined }}>
                    <div style={{ display: 'grid', gap: 4 }}>
                      {on.map((e) => (
                        <ShiftChip key={e.id} e={e} label={vname(data.venues.find((v) => v.id === e.venue_id)?.code || '')} onShift={onShift} />
                      ))}
                      {!on.length && <span className="subtle" style={{ fontSize: 12 }}>Off</span>}
                      {addable.length > 0 && (
                        <select className="select" value="" style={{ minHeight: 28, padding: '2px 6px', fontSize: 12.5 }}
                          aria-label={`Roster ${m.name} on ${d}`} disabled={busy}
                          onChange={(ev) => { const v = data.venues.find((x) => x.id === Number(ev.target.value)); if (v) onAdd(v, m.id, d) }}>
                          <option value="">+ Venue…</option>
                          {addable.map((v) => <option key={v.id} value={v.id}>{vname(v.name)}</option>)}
                        </select>
                      )}
                    </div>
                  </td>
                )
              })}
            </tr>
          )
        })}
        </tbody>
      </table>
    </div></div>
  )
}

function ShiftDrawer({ entry, venue, onClose, onDone }) {
  const hrs = venue.hours[entry.date]
  const [f, setF] = useState({ custom: true, start: entry.custom ? hhmm(entry.start_min) : '', end: entry.custom ? hhmm(entry.end_min) : '' })
  const [run, busy] = useAction()
  const toMin = (v) => { const [h, m] = v.split(':').map(Number); return h * 60 + m }
  const overnight = f.start && f.end && toMin(f.end) < toMin(f.start)
  const invalid = f.custom && (!f.start || !f.end || f.start === f.end)
  const save = async (close, body) => {
    if (await run(() => patch(`/api/roster/${entry.id}`, body), 'Shift saved')) { onDone(); close() }
  }
  return (
    <Drawer open onClose={onClose} title={`${entry.name} · ${vname(venue.name)}`}
      subtitle={`${parseISO(entry.date).toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'short' })}${hrs ? ` · venue open ${range12(hrs[0], hrs[1])}` : ''}`}>
      {(close) => (
        <form className="form" onSubmit={(e) => { e.preventDefault(); save(close, f.custom ? { start: f.start, end: f.end } : {}) }}>
          <Seg value={f.custom ? 'custom' : 'full'} onChange={(v) => setF({ ...f, custom: v === 'custom' })} label="Shift"
            options={[{ value: 'custom', label: 'Custom shift' }, { value: 'full', label: 'Full day' }]} />
          {f.custom && (
            <div className="form-row">
              <Field label="Starts">
                <input className="input" type="time" value={f.start} onChange={(e) => setF({ ...f, start: e.target.value })} required autoFocus />
              </Field>
              <Field label="Ends" help={overnight ? 'Ends after midnight' : null} error={f.start && f.start === f.end ? 'Start and end are the same' : null}>
                <input className="input" type="time" value={f.end} onChange={(e) => setF({ ...f, end: e.target.value })} required />
              </Field>
            </div>
          )}
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <button type="button" className="btn btn-ghost" onClick={close}>{entry.custom ? 'Cancel' : 'Keep full day'}</button>
            <button className="btn btn-primary" disabled={busy || invalid}>Save shift</button>
          </div>
        </form>
      )}
    </Drawer>
  )
}
