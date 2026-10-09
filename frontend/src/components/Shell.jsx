import { useEffect, useState } from 'react'
import { NavLink, Outlet } from 'react-router-dom'
import {
  Activity, BarChart3, BookOpen, Building2, CalendarDays, ClipboardList, GraduationCap, LayoutDashboard, LogOut,
  Menu, Monitor, Moon, Settings, ShieldCheck, Sun, Users, Briefcase, Plug,
} from 'lucide-react'
import logo from '../assets/logo.svg'
import { useAuth } from '../lib/useCtx'
import { useApi, useLocalState } from '../lib/hooks'
import { initials } from '../lib/format'

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
  const { user, logout, isHQ } = useAuth()
  const { data: venues } = useApi('/api/venues')
  const [open, setOpen] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const [theme, setTheme] = useTheme()
  useEffect(() => {
    const on = () => setScrolled(window.scrollY > 4)
    window.addEventListener('scroll', on, { passive: true })
    return () => window.removeEventListener('scroll', on)
  }, [])
  const nextTheme = { system: 'light', light: 'dark', dark: 'system' }
  const ThemeIcon = theme === 'dark' ? Moon : theme === 'light' ? Sun : Monitor

  const link = (to, Icon, label, end) => (
    <NavLink key={to} to={to} end={end} onClick={() => setOpen(false)} className={({ isActive }) => `nav-link ${isActive ? 'active' : ''}`}>
      <Icon aria-hidden />{label}
    </NavLink>
  )

  return (
    <div className="shell">
      {open && <div className="scrim" onClick={() => setOpen(false)} aria-hidden />}
      <nav className={`sidebar ${open ? 'open' : ''}`} aria-label="Main">
        <div className="brand">
          <img src={logo} alt="RallyGully" />
          <span className="brand-tag">PULSE</span>
        </div>
        {isHQ ? (
          <>
            <div className="nav-section">
              <div className="nav-title">Overview</div>
              {link('/', LayoutDashboard, 'Portfolio', true)}
              {link('/analytics', BarChart3, 'Performance')}
            </div>
            <div className="nav-section">
              <div className="nav-title">Venues</div>
              {(venues || []).map((v) => link(`/venues/${v.id}`, Building2, v.name.replace('RallyGully ', '')))}
            </div>
            <div className="nav-section">
              <div className="nav-title">Operations</div>
              {link('/bookings', ClipboardList, 'Bookings')}
              {link('/community', Users, 'Community Games')}
              {link('/academy', GraduationCap, 'Academy')}
              {link('/events', Briefcase, 'Corporate / Private')}
            </div>
            <div className="nav-section">
              <div className="nav-title">Intelligence</div>
              {link('/customers', Activity, 'Customers')}
            </div>
            <div className="nav-section">
              <div className="nav-title">System</div>
              {link('/config', Settings, 'Configuration')}
              {link('/audit', ShieldCheck, 'Audit trail')}
              {link('/system', Plug, 'Integrations')}
            </div>
          </>
        ) : (
          <div className="nav-section">
            <div className="nav-title">My venue</div>
            {(venues || []).map((v) => link(`/venues/${v.id}`, CalendarDays, v.name.replace('RallyGully ', '')))}
            {link('/bookings', BookOpen, 'Bookings')}
          </div>
        )}
        <div className="sidebar-foot">
          <div className="who">
            <div className="avatar" aria-hidden>{initials(user?.name)}</div>
            <div style={{ minWidth: 0 }}>
              <div className="who-name">{user?.name}</div>
              <div className="who-role">{isHQ ? 'RallyGully HQ' : 'Venue Manager'}</div>
            </div>
          </div>
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
    </div>
  )
}
