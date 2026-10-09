/** Status → badge helpers (plain functions, kept apart from components for fast refresh). */
import { AlertTriangle, Check, CheckCircle2, CircleDashed, Clock, MapPin, UserCheck, UserX, Wallet, XCircle } from 'lucide-react'
import { Badge } from './ui'

export const WINDOWS = [
  { value: 'prev_day', label: 'Prev day' },
  { value: 'same_weekday', label: 'Same weekday' },
  { value: 'd7', label: '7d' },
  { value: 'd30', label: '30d' },
  { value: 'd90', label: '90d' },
]

export const WINDOW_LABEL = {
  prev_day: 'vs previous day',
  same_weekday: 'vs same weekday',
  d7: 'vs 7-day avg',
  d30: 'vs 30-day avg',
  d90: 'vs 90-day avg',
}

export function closeBadge(status) {
  if (status === 'closed') return <Badge tone="good" icon={CheckCircle2}>Closed</Badge>
  if (status === 'closed_late') return <Badge tone="warn" icon={Clock}>Closed late</Badge>
  if (status === 'missed') return <Badge tone="bad" icon={AlertTriangle}>Missed close</Badge>
  return <Badge icon={CircleDashed}>Open</Badge>
}

export function payBadge(s) {
  if (s === 'paid') return <Badge tone="good" icon={Check}>Paid</Badge>
  if (s === 'overdue') return <Badge tone="bad" icon={AlertTriangle}>Payment overdue</Badge>
  if (s === 'to_collect') return <Badge tone="warn" icon={Wallet}>To collect</Badge>
  if (s === 'unpaid') return <Badge tone="warn">Unpaid</Badge>
  return null
}

export function attendBadge(b) {
  if (b.status === 'cancelled') return <Badge tone="bad" icon={XCircle}>Cancelled</Badge>
  if (b.attendance === 'attended') return <Badge tone="good" icon={UserCheck}>Attended</Badge>
  if (b.attendance === 'no_show') return <Badge tone="bad" icon={UserX}>No-show</Badge>
  if (b.source === 'district' && !b.facility_id) return <Badge tone="info" icon={MapPin}>Awaiting arrival</Badge>
  return <Badge icon={Clock}>Booked</Badge>
}
