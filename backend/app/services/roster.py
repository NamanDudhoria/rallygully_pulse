"""Daily VM roster: who runs which venue on which date (normally set the day before).

The roster gates VM access (permissions.ensure_venue). To keep that from silently
locking a venue out, Operations is emailed each evening about tomorrow's operating
venues that have no VM rostered.
"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..timeutil import hhmm
from ..audit import DomainError
from ..models import RosterAlert, User, Venue, VmAssignment, VmRoster
from ..timeutil import minute_of, now_local
from . import inventory, notify


def window(db: Session, r: VmRoster) -> tuple[int, int] | None:
    """Effective shift: the custom window, else the venue's hours that day (None if closed)."""
    if r.start_min is not None and r.end_min is not None:
        return r.start_min, r.end_min
    return inventory.hours_for(db, r.venue_id, r.date)


def _time12(minute: int) -> str:
    h, m = divmod(minute % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def notify_allotment(db: Session, entries: list[VmRoster]) -> int:
    """WhatsApp each VM the dates they've been allotted, one message per venue and shift time.
    VMs without a phone number are skipped (their roster still works). Returns messages queued."""
    groups: dict[tuple, list[VmRoster]] = {}
    for r in entries:
        groups.setdefault((r.user_id, r.venue_id, window(db, r)), []).append(r)
    sent = 0
    for (uid, vid, win), rows in groups.items():
        u, v = db.get(User, uid), db.get(Venue, vid)
        if not u or not u.phone or not v or not win:
            continue
        slot = f"{_time12(win[0])} to {_time12(win[1])}"
        dates = ", ".join(r.date.strftime("%a %d %b") for r in sorted(rows, key=lambda x: x.date))
        notify.whatsapp(db, u.phone, "roster_allotment", notify.roster_message(v.name, slot, dates), "roster", rows[0].id,
                        content_sid=config.TWILIO_CONTENT_SID_ROSTER,
                        variables=notify.named(config.TWILIO_ROSTER_VARS, v.name, slot, dates))
        sent += 1
    return sent


def _check_shift(start_min: int | None, end_min: int | None) -> tuple[int | None, int | None]:
    """Custom shifts are any times HQ types: no venue-hours or step rules. An end before the start
    runs past midnight. Overlaps across venues are allowed — one VM often covers a cluster at once."""
    if (start_min is None) != (end_min is None):
        raise DomainError("Give both a shift start and end, or neither for the full day.", 422)
    if start_min is None:
        return None, None
    if start_min == end_min:
        raise DomainError("Shift start and end can't be the same time.", 422)
    return start_min, end_min if end_min > start_min else end_min + 1440


def add(db: Session, actor: User, venue_id: int, user_id: int, d: date,
        start_min: int | None = None, end_min: int | None = None) -> VmRoster:
    v = db.get(Venue, venue_id)
    u = db.get(User, user_id)
    if not v or not u:
        raise DomainError("Venue or person not found.", 404)
    if u.role != "vm" or not u.active:
        raise DomainError(f"{u.name} isn't an active Venue Manager.", 422)
    existing = db.scalar(select(VmRoster).where(VmRoster.venue_id == venue_id, VmRoster.user_id == user_id,
                                                VmRoster.date == d))
    if existing:
        if start_min is not None or end_min is not None:
            return set_shift(db, actor, existing, start_min, end_min)
        return existing
    start_min, end_min = _check_shift(start_min, end_min)
    if not db.get(VmAssignment, (user_id, venue_id)):
        # Rostering someone outside the venue team adds them to it (and records that).
        db.add(VmAssignment(user_id=user_id, venue_id=venue_id))
        audit.record(db, actor, "team.add", "user", u.id, None, {"venue_id": venue_id, "via": "roster"}, venue_id)
    r = VmRoster(venue_id=venue_id, user_id=user_id, date=d, start_min=start_min, end_min=end_min,
                 created_by=actor.id if actor else None)
    db.add(r)
    db.flush()
    audit.record(db, actor, "roster.add", "roster", r.id, None,
                 {"venue_id": venue_id, "user": u.email, "date": d, "start": start_min, "end": end_min}, venue_id)
    notify_allotment(db, [r])
    return r


def set_shift(db: Session, actor: User, r: VmRoster, start_min: int | None, end_min: int | None) -> VmRoster:
    start_min, end_min = _check_shift(start_min, end_min)
    before = {"start": r.start_min, "end": r.end_min}
    r.start_min, r.end_min = start_min, end_min
    audit.record(db, actor, "roster.shift", "roster", r.id, before, {"start": start_min, "end": end_min}, r.venue_id)
    if before != {"start": start_min, "end": end_min}:
        notify_allotment(db, [r])
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
    added: list[VmRoster] = []
    for r in list(db.scalars(select(VmRoster).where(VmRoster.date >= src_start,
                                                     VmRoster.date < src_start + timedelta(days=days)))):
        target = dst_start + (r.date - src_start)
        u = db.get(User, r.user_id)
        if not u or not u.active or not db.get(VmAssignment, (r.user_id, r.venue_id)):
            continue  # person left the team since; don't carry them forward
        if not db.scalar(select(VmRoster.id).where(VmRoster.venue_id == r.venue_id, VmRoster.user_id == r.user_id,
                                                   VmRoster.date == target)):
            new = VmRoster(venue_id=r.venue_id, user_id=r.user_id, date=target, start_min=r.start_min,
                           end_min=r.end_min, created_by=actor.id)
            db.add(new)
            added.append(new)
            n += 1
    db.flush()
    notify_allotment(db, added)
    audit.record(db, actor, "roster.copy", "roster", None, None,
                 {"from": src_start, "to": dst_start, "days": days, "added": n})
    return n


def names_for(db: Session, venue_id: int, d: date) -> list[str]:
    return [u.name for u in db.scalars(select(User).join(VmRoster, VmRoster.user_id == User.id).where(
        VmRoster.venue_id == venue_id, VmRoster.date == d).order_by(User.name))]


def on_duty(db: Session, venue_id: int, d: date) -> list[dict]:
    """Who is rostered here on ``d`` and when, ordered by shift start."""
    rows = db.execute(select(VmRoster, User).join(User, User.id == VmRoster.user_id).where(
        VmRoster.venue_id == venue_id, VmRoster.date == d))
    from . import attendance
    rows = list(rows)
    seen = attendance.by_roster(db, [(u.id, r.venue_id, r.date) for r, u in rows])
    out = []
    for r, u in rows:
        w = window(db, r)
        out.append({"user_id": u.id, "name": u.name, "start_min": w[0] if w else None, "end_min": w[1] if w else None,
                    "custom": r.start_min is not None, "attendance": seen.get((u.id, r.venue_id, r.date))})
    return sorted(out, key=lambda x: (x["start_min"] or 0, x["name"]))


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
