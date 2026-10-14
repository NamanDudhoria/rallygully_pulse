import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useAuth } from './lib/useCtx'
import Shell from './components/Shell'
import Login from './pages/Login'
import SetPassword from './pages/SetPassword'
import Portfolio from './pages/Portfolio'
import VenueDay from './pages/VenueDay'
import Analytics from './pages/Analytics'
import Bookings from './pages/Bookings'
import History from './pages/History'
import { AcademyPage, CommunityPage, EventsPage } from './pages/Programs'
import { AuditPage, ConfigPage, CustomersPage, SystemPage } from './pages/Admin'
import { PeoplePage, RosterPage } from './pages/People'
import { range12, todayISO } from './lib/format'

/** Landing page per role: analytics roles see the portfolio; VMs go to today's rostered venue. */
function Home() {
  const { user, can } = useAuth()
  if (can('portfolio.view')) return <Portfolio />
  const today = todayISO()
  const shift = (user.roster || []).find((r) => r.date === today)
  if (shift) return <Navigate to={`/venues/${shift.venue_id}`} replace />
  const next = (user.roster || []).find((r) => r.date > today)
  return (
    <div className="card card-pad stack" style={{ maxWidth: 520 }}>
      <h1>You&apos;re not on today&apos;s roster</h1>
      <p className="muted">
        Venue access opens on the days HQ rosters you.{' '}
        {next ? <>Your next shift is <b>{next.venue.replace('RallyGully ', '')}</b> on {next.date}{next.start_min != null && <>, {range12(next.start_min, next.end_min)}</>}.</> : 'No upcoming shifts yet.'}
      </p>
    </div>
  )
}

export default function App() {
  const { user, can } = useAuth()
  const loc = useLocation()
  if (loc.pathname === '/set-password') return <SetPassword />
  if (user === undefined) return null
  if (!user) return <Login />
  const guard = (perms, el) => (can(...perms) ? el : <Navigate to="/" replace />)
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Home />} />
        <Route path="venues/:id" element={guard(['venue.view', 'venue.operate'], <VenueDay />)} />
        <Route path="bookings" element={guard(['bookings.view', 'venue.operate'], <Bookings />)} />
        <Route path="analytics" element={guard(['portfolio.view'], <Analytics />)} />
        <Route path="history" element={guard(['portfolio.view'], <History />)} />
        <Route path="community" element={guard(['community.manage'], <CommunityPage />)} />
        <Route path="academy" element={guard(['academy.manage'], <AcademyPage />)} />
        <Route path="events" element={guard(['events.manage'], <EventsPage />)} />
        <Route path="customers" element={guard(['customers.view'], <CustomersPage />)} />
        <Route path="roster" element={guard(['roster.manage'], <RosterPage />)} />
        <Route path="people" element={guard(['users.manage'], <PeoplePage />)} />
        <Route path="config" element={guard(['config.manage'], <ConfigPage />)} />
        <Route path="audit" element={guard(['audit.view'], <AuditPage />)} />
        <Route path="system" element={guard(['system.view'], <SystemPage />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
