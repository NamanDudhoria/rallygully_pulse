"""Daily VM roster: who runs which venue on which date (normally set the day before).

The roster gates VM access (permissions.ensure_venue). To keep that from silently
locking a venue out, Operations is emailed each evening about tomorrow's operating
venues that have no VM rostered.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import RosterAlert, User, Venue, VmAssignment, VmRoster
from ..timeutil import minute_of, now_local
from . import inventory, notify


def add(db: Session, actor: User, venue_id: int, user_id: int, d: date) -> VmRoster:
    v = db.get(Venue, venue_id)
    u = db.get(User, user_id)
    if not v or not u:
        raise DomainError("Venue or person not found.", 404)
    if u.role != "vm" or not u.active:
        raise DomainError(f"{u.name} isn't an active Venue Manager.", 422)
    if not db.get(VmAssignment, (user_id, venue_id)):
        raise DomainError(f"{u.name} isn't on {v.name}'s team. Add the venue to their profile first.", 422)
    existing = db.scalar(select(VmRoster).where(VmRoster.venue_id == venue_id, VmRoster.user_id == user_id,
                                                VmRoster.date == d))
    if existing:
        return existing
    r = VmRoster(venue_id=venue_id, user_id=user_id, date=d, created_by=actor.id if actor else None)
    db.add(r)
    db.flush()
    audit.record(db, actor, "roster.add", "roster", r.id, None, {"venue_id": venue_id, "user": u.email, "date": d},
                 venue_id)
    return r


def remove(db: Session, actor: User, r: VmRoster) -> None:
    u = db.get(User, r.user_id)
    audit.record(db, actor, "roster.remove", "roster", r.id, {"venue_id": r.venue_id, "user": u.email if u else None,
                                                             "date": r.date}, None, r.venue_id)
    db.delete(r)


def copy_range(db: Session, actor: User, src_start: date, dst_start: date, days: int) -> int:
    """Copy a block of days (e.g. last week → this week). Existing entries are kept."""
    if not 1 <= days <= 31:
        raise DomainError("Copy between 1 and 31 days.", 422)
    n = 0
    for r in list(db.scalars(select(VmRoster).where(VmRoster.date >= src_start,
                                                     VmRoster.date < src_start + timedelta(days=days)))):
        target = dst_start + (r.date - src_start)
        u = db.get(User, r.user_id)
        if not u or not u.active or not db.get(VmAssignment, (r.user_id, r.venue_id)):
            continue  # person left the team since; don't carry them forward
        if not db.scalar(select(VmRoster.id).where(VmRoster.venue_id == r.venue_id, VmRoster.user_id == r.user_id,
                                                   VmRoster.date == target)):
            db.add(VmRoster(venue_id=r.venue_id, user_id=r.user_id, date=target, created_by=actor.id))
            n += 1
    db.flush()
    audit.record(db, actor, "roster.copy", "roster", None, None,
                 {"from": src_start, "to": dst_start, "days": days, "added": n})
    return n


def names_for(db: Session, venue_id: int, d: date) -> list[str]:
    return [u.name for u in db.scalars(select(User).join(VmRoster, VmRoster.user_id == User.id).where(
        VmRoster.venue_id == venue_id, VmRoster.date == d).order_by(User.name))]


def gaps(db: Session, d: date) -> list[Venue]:
    """Active venues operating on ``d`` with nobody rostered."""
    out = []
    for v in db.scalars(select(Venue).where(Venue.status == "active").order_by(Venue.id)):
        if inventory.hours_for(db, v.id, d) and not names_for(db, v.id, d):
            out.append(v)
    return out


def alert_gaps(db: Session) -> int:
    """From the configured evening time, warn Operations once per unrostered venue for tomorrow."""
    now = now_local()
    if minute_of(now, now.date()) < config.ROSTER_ALERT_MIN:
        return 0
    tomorrow = now.date() + timedelta(days=1)
    missing = [v for v in gaps(db, tomorrow)
               if not db.scalar(select(RosterAlert.id).where(RosterAlert.venue_id == v.id,
                                                             RosterAlert.date == tomorrow))]
    if not missing:
        return 0
    for v in missing:
        db.add(RosterAlert(venue_id=v.id, date=tomorrow, sent_at=now))
    lines = "\n".join(f"• {v.name}" for v in missing)
    notify.email(db, config.OPS_EMAIL, f"[Pulse] No VM rostered for tomorrow — {tomorrow.strftime('%a %d %b')}",
                 f"These venues operate tomorrow but have no Venue Manager on the roster, so no VM can open them "
                 f"in Pulse:\n\n{lines}\n\nAdd someone under Roster in Pulse.", "roster", None, template="roster_gap")
    return len(missing)
