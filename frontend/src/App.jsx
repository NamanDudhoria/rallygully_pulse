import { Navigate, Route, Routes } from 'react-router-dom'
import { useAuth } from './lib/useCtx'
import Shell from './components/Shell'
import Login from './pages/Login'
import Portfolio from './pages/Portfolio'
import VenueDay from './pages/VenueDay'
import Analytics from './pages/Analytics'
import Bookings from './pages/Bookings'
import { AcademyPage, CommunityPage, EventsPage } from './pages/Programs'
import { AuditPage, ConfigPage, CustomersPage, SystemPage } from './pages/Admin'
import { useApi } from './lib/hooks'
import { PageSkeleton } from './components/ui'

function VmHome() {
  const { data } = useApi('/api/venues')
  if (!data) return <PageSkeleton />
  return data.length ? <Navigate to={`/venues/${data[0].id}`} replace /> : <p>No venue assigned yet — contact HQ.</p>
}

export default function App() {
  const { user, isHQ } = useAuth()
  if (user === undefined) return null
  if (!user) return <Login />
  const hq = (el) => (isHQ ? el : <Navigate to="/" replace />)
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={isHQ ? <Portfolio /> : <VmHome />} />
        <Route path="venues/:id" element={<VenueDay />} />
        <Route path="bookings" element={<Bookings />} />
        <Route path="analytics" element={hq(<Analytics />)} />
        <Route path="community" element={hq(<CommunityPage />)} />
        <Route path="academy" element={hq(<AcademyPage />)} />
        <Route path="events" element={hq(<EventsPage />)} />
        <Route path="customers" element={hq(<CustomersPage />)} />
        <Route path="config" element={hq(<ConfigPage />)} />
        <Route path="audit" element={hq(<AuditPage />)} />
        <Route path="system" element={hq(<SystemPage />)} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  )
}
