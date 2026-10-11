/** People & access (users.manage) and the daily VM roster (roster.manage). */
import { useState } from 'react'
import { AlertTriangle, CalendarRange, ChevronLeft, ChevronRight, Copy, Lock, Mail, Plus, Search, X } from 'lucide-react'
import { del, post, put } from '../lib/api'
import { useApi } from '../lib/hooks'
import { useAction, useAuth, useToast } from '../lib/useCtx'
import { addDays, dateTime, parseISO, shortDate, todayISO } from '../lib/format'
import { Badge, Drawer, Empty, ErrorState, Field, Skeleton } from '../components/ui'

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

export function RosterPage() {
  const [start, setStart] = useState(mondayOf(todayISO()))
  const end = addDays(start, 6)
  const { data, error, reload } = useApi(`/api/roster?date_from=${start}&date_to=${end}`)
  const [run, busy] = useAction()
  const today = todayISO()
  const tomorrow = addDays(today, 1)
  const act = async (fn, msg) => { if (await run(fn, msg)) reload() }

  return (
    <div className="stack rise">
      <div className="page-head">
        <div>
          <h1>VM roster</h1>
        </div>
        <div className="row">
          <button className="btn btn-icon btn-sm" onClick={() => setStart(addDays(start, -7))} aria-label="Previous week"><ChevronLeft /></button>
          <span style={{ minWidth: 150, textAlign: 'center', fontWeight: 500 }}>{shortDate(start)} – {shortDate(end)}</span>
          <button className="btn btn-icon btn-sm" onClick={() => setStart(addDays(start, 7))} aria-label="Next week"><ChevronRight /></button>
          {start !== mondayOf(today) && <button className="btn btn-sm" onClick={() => setStart(mondayOf(today))}>This week</button>}
          <button className="btn btn-sm" disabled={busy} onClick={() => act(() => post('/api/roster/copy', { from_date: addDays(start, -7), to_date: start, days: 7 }),
            'Copied last week')} title="Copy the previous week's roster into this week (keeps existing entries)"><Copy /> Copy last week</button>
        </div>
      </div>
      {data?.tomorrow_gaps?.length > 0 && (
        <div className="callout bad"><AlertTriangle />
          <span><b>No VM rostered for tomorrow</b> at {data.tomorrow_gaps.map((n) => n.replace('RallyGully ', '')).join(', ')}. Nobody will be able to operate {data.tomorrow_gaps.length > 1 ? 'them' : 'it'} in Pulse; Operations is emailed from 8 PM if this is still open.</span>
        </div>
      )}
      {error ? <ErrorState error={error} onRetry={reload} /> : !data ? <Skeleton h={300} /> : (
        <div className="card"><div className="card-body table-wrap">
          <table className="table" style={{ tableLayout: 'fixed', minWidth: 900 }}>
            <thead><tr><th style={{ width: 150 }}>Venue</th>{data.days.map((d) => (
              <th key={d} style={{ color: d === today ? 'var(--brand)' : undefined }}>
                {parseISO(d).toLocaleDateString('en-IN', { weekday: 'short' })} {shortDate(d)}{d === today ? ' · Today' : d === tomorrow ? ' · Tmrw' : ''}
              </th>))}</tr></thead>
            <tbody>{data.venues.map((v) => (
              <tr key={v.id}>
                <td style={{ verticalAlign: 'top' }}>
                  <div style={{ fontWeight: 600 }}>{v.name.replace('RallyGully ', '')}</div>
                  <div className="subtle" style={{ fontSize: 12 }}>{v.team.length} on team</div>
                </td>
                {data.days.map((d) => {
                  const on = data.entries.filter((e) => e.venue_id === v.id && e.date === d)
                  const operating = v.operating[d]
                  const free = v.team.filter((m) => !on.some((e) => e.user_id === m.id))
                  return (
                    <td key={d} style={{ verticalAlign: 'top', background: !operating ? 'var(--surface-sunk)' : undefined }}>
                      {!operating ? <span className="subtle" style={{ fontSize: 12 }}>Closed</span> : (
                        <div style={{ display: 'grid', gap: 4 }}>
                          {on.map((e) => (
                            <span key={e.id} className="badge brand" style={{ justifyContent: 'space-between', height: 26 }}>
                              {e.name.split(' ')[0]}
                              <button className="btn-ghost" style={{ border: 0, background: 'none', cursor: 'pointer', padding: 0, display: 'inline-flex', color: 'inherit' }}
                                aria-label={`Remove ${e.name} from ${v.name} on ${d}`} disabled={busy}
                                onClick={() => act(() => del(`/api/roster/${e.id}`), `${e.name} removed`)}><X size={13} /></button>
                            </span>
                          ))}
                          {!on.length && <span className="badge bad" title="No VM can open this venue in Pulse on this day"><Lock /> No VM</span>}
                          {free.length > 0 && (
                            <select className="select" value="" style={{ minHeight: 28, padding: '2px 6px', fontSize: 12.5 }}
                              aria-label={`Add VM to ${v.name} on ${d}`} disabled={busy}
                              onChange={(e) => e.target.value && act(() => post('/api/roster', { venue_id: v.id, user_id: Number(e.target.value), date: d }), 'Rostered')}>
                              <option value="">+ Add…</option>
                              {free.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
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
          {data.venues.some((v) => !v.team.length) && (
            <p className="subtle" style={{ fontSize: 12.5, marginTop: 10 }}><CalendarRange size={13} style={{ verticalAlign: -2 }} /> Venues without a team: add VMs to the venue under People &amp; access first.</p>
          )}
        </div></div>
      )}
    </div>
  )
}
