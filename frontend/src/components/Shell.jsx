import { useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import {
  Activity, BarChart3, BookOpen, Building2, CalendarDays, CalendarRange, ClipboardList, GraduationCap, KeyRound,
  History, LayoutDashboard, LogOut, Menu, Monitor, Moon, Settings, ShieldCheck, Sun, Users, UsersRound, Briefcase, Plug,
} from 'lucide-react'
import logo from '../assets/logo.svg'
import { post } from '../lib/api'
import { useAction, useAuth } from '../lib/useCtx'
import { useApi, useLocalState } from '../lib/hooks'
import { initials, range12, shortDate, todayISO } from '../lib/format'
import { Drawer, Field } from './ui'

function useTheme() {
  const [theme, setTheme] = useLocalState('pulse.theme', 'system')
  useEffect(() => {
    const el = document.documentElement
    if (theme === 'system') el.removeAttribute('data-theme')
    else el.setAttribute('data-theme', theme)
  }, [theme])
  return [theme, setTheme]
}

export default function Shell() {
  const { user, logout, can } = useAuth()
  const { data: venues } = useApi('/api/venues')
  const [open, setOpen] = useState(false)
  const [account, setAccount] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [theme, setTheme] = useTheme()
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 4)
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [])
  const nextTheme = { system: 'light', light: 'dark', dark: 'system' }
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor
  const isVM = user?.role === 'vm'
  const today = todayISO()
  const loc = useLocation()
  const openDate = new URLSearchParams(loc.search).get('date') || today
  // Roster links differ only by ?date=, which NavLink ignores — match venue and date explicitly.
  const shiftLink = (r) => {
    const active = loc.pathname === `/venues/${r.venue_id}` && openDate === r.date
    return (
      <Link key={`${r.venue_id}-${r.date}`} to={`/venues/${r.venue_id}${r.date === today ? '' : `?date=${r.date}`}`}
        onClick={() => setOpen(false)} className={`nav-link ${active ? 'active' : ''}`} aria-current={active ? 'page' : undefined}>
        <CalendarDays aria-hidden />
        <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {r.venue.replace('RallyGully ', '')}
          <span className="subtle" style={{ display: 'block', fontSize: 11.5 }}>
            {r.date === today ? 'Today' : shortDate(r.date)}{r.start_min != null && ` · ${range12(r.start_min, r.end_min)}`}
          </span>
        </span>
      </Link>
    )
  }

  const link = (to, Icon, label, end) => (
    <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)}
      className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
      <Icon aria-hidden /><span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{label}</span>
    </NavLink>
  )
  const section = (title, items) => {
    const shown = items.filter(Boolean)
    return shown.length ? <div className="nav-section"><div className="nav-title">{title}</div>{shown}</div> : null
  }

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <nav className={`sidebar ${open ? 'open' : ''}`} aria-label="Main">
        <div className="brand">
          <img src={logo} alt="RallyGully" />
          <span className="brand-tag">PULSE</span>
        </div>
        {isVM ? (
          <>
            {section('My roster', (user.roster || []).filter((r) => r.date >= today).slice(0, 8).map(shiftLink))}
            {!(user.roster || []).some((r) => r.date >= today) && (
              <p className="subtle" style={{ fontSize: 12.5, padding: '8px 10px' }}>Not on the roster yet.</p>
            )}
            {section('Records', [link('/bookings', BookOpen, 'Bookings')])}
          </>
        ) : (
          <>
            {section('Overview', [
              can('portfolio.view') && link('/', LayoutDashboard, 'Portfolio', true),
              can('portfolio.view') && link('/analytics', BarChart3, 'Performance'),
              can('portfolio.view') && link('/history', History, 'Sales history'),
            ])}
            {section('Venues', can('venue.view', 'venue.operate')
              ? (venues || []).map((v) => link(`/venues/${v.id}`, Building2, v.name.replace('RallyGully ', '')))
              : [])}
            {section('Operations', [
              can('bookings.view') && link('/bookings', ClipboardList, 'Bookings'),
              can('community.manage') && link('/community', Users, 'Community Games'),
              can('academy.manage') && link('/academy', GraduationCap, 'Academy'),
              can('events.manage') && link('/events', Briefcase, 'Corporate / Private'),
              can('roster.manage') && link('/roster', CalendarRange, 'VM roster'),
            ])}
            {section('Intelligence', [can('customers.view') && link('/customers', Activity, 'Customers')])}
            {section('System', [
              can('users.manage') && link('/people', UsersRound, 'People & access'),
              can('config.manage') && link('/config', Settings, 'Configuration'),
              can('audit.view') && link('/audit', ShieldCheck, 'Audit trail'),
              can('system.view') && link('/system', Plug, 'Integrations'),
            ])}
          </>
        )}
        <div className="sidebar-foot">
          <button className="who" onClick={() => setAccount(true)} style={{ background: 'none', border: 0, cursor: 'pointer', textAlign: 'left', borderRadius: 8 }}
            aria-label="Account settings">
            <div className="avatar" aria-hidden>{initials(user?.name)}</div>
            <div style={{ minWidth: 0 }}>
              <div className="who-name">{user?.name}</div>
              <div className="who-role">{user?.role_label}</div>
            </div>
          </button>
          <div className="row">
            <button className="btn btn-sm btn-ghost" onClick={() => setTheme(nextTheme[theme])} aria-label={`Theme: ${theme}. Switch to ${nextTheme[theme]}`}>
              <ThemeIcon /> {theme === 'system' ? 'Auto theme' : theme === 'dark' ? 'Dark' : 'Light'}
            </button>
            <button className="btn btn-sm btn-ghost" onClick={logout}><LogOut /> Sign out</button>
          </div>
        </div>
      </nav>
      <div className="main">
        <div className={`topbar ${scrolled ? 'scrolled' : ''}`}>
          <button className="btn btn-ghost btn-icon" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></button>
          <img src={logo} alt="RallyGully" style={{ height: 18 }} />
          <span className="brand-tag">PULSE</span>
        </div>
        <main className="page">
          <Outlet />
        </main>
      </div>
      {account && <AccountDrawer onClose={() => setAccount(false)} />}
    </div>
  )
}

function AccountDrawer({ onClose }) {
  const { user, adopt, logout } = useAuth()
  const [run, busy] = useAction()
  const [f, setF] = useState({ current_password: '', new_password: '', confirm: '' })
  const mismatch = f.confirm && f.confirm !== f.new_password
  return (
    <Drawer open onClose={onClose} title="Your account" subtitle={`${user.email} · ${user.role_label}`}>
      {(close) => (
        <>
          <form className="form" onSubmit={async (e) => {
            e.preventDefault()
            const r = await run(() => post('/api/auth/change-password', { current_password: f.current_password, new_password: f.new_password }),
              'Password changed — other devices were signed out')
            if (r) { adopt(r); close() }
          }}>
            <div className="section-title"><KeyRound size={13} style={{ verticalAlign: -2 }} /> Change password</div>
            <Field label="Current password"><input className="input" type="password" autoComplete="current-password" value={f.current_password} onChange={(e) => setF({ ...f, current_password: e.target.value })} required /></Field>
            <Field label="New password" help="At least 8 characters. A short phrase is easier to remember and harder to guess.">
              <input className="input" type="password" autoComplete="new-password" minLength={8} value={f.new_password} onChange={(e) => setF({ ...f, new_password: e.target.value })} required />
            </Field>
            <Field label="Confirm new password" error={mismatch ? "Passwords don't match" : null}>
              <input className="input" type="password" autoComplete="new-password" value={f.confirm} aria-invalid={!!mismatch} onChange={(e) => setF({ ...f, confirm: e.target.value })} required />
            </Field>
            <div><button className="btn btn-primary" disabled={busy || mismatch}>Update password</button></div>
          </form>
          <div className="divider" />
          <div>
            <div className="section-title">Sessions</div>
            <button className="btn" disabled={busy} onClick={async () => {
              if (await run(() => post('/api/auth/logout-everywhere'), 'Signed out everywhere')) logout()
            }}><LogOut /> Sign out everywhere</button>
          </div>
        </>
      )}
    </Drawer>
  )
}
