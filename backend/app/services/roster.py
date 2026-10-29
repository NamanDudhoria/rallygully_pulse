"""Daily VM roster: who runs which venue on which date (normally set the day before).

The roster gates VM access (permissions.ensure_venue). To keep that from silently
locking a venue out, Operations is emailed each evening about tomorrow's operating
venues that have no VM rostered.
"""
import hashlib
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..timeutil import hhmm
from ..audit import DomainError
from ..models import Notification, RosterAlert, RosterMail, User, Venue, VmAssignment, VmLeave, VmRoster
from ..timeutil import minute_of, now_local, today
from . import inventory, notify


def window(db: Session, r: VmRoster) -> tuple[int, int] | None:
    """Effective shift: the custom window, else the venue's hours that day (None if closed)."""
    if r.start_min is not None and r.end_min is not None:
        return r.start_min, r.end_min
    return inventory.hours_for(db, r.venue_id, r.date)


def touch(db: Session, d: date) -> None:
    """The roster for ``d`` changed: note when (the admins are emailed once it settles) and refresh who is on leave."""
    if d < today():
        return
    row = db.get(RosterMail, d)
    if not row:
        row = RosterMail(date=d, changed_at=now_local(), sent_count=0)
        db.add(row)
    row.changed_at = now_local()
    db.flush()
    sync_leaves(db, d)


def sync_leaves(db: Session, d: date) -> None:
    """Record every active VM with no venue on ``d`` as on leave, or on their weekly off. Only for days that have a
    roster: until someone is rostered, nobody is "on leave"."""
    rostered = set(db.scalars(select(VmRoster.user_id).where(VmRoster.date == d)))
    want = {}
    if rostered:
        for u in db.scalars(select(User).where(User.role == "vm", User.active.is_(True))):
            if u.id not in rostered:
                want[u.id] = "week_off" if u.week_off == d.weekday() else "leave"
    have = {x.user_id: x for x in db.scalars(select(VmLeave).where(VmLeave.date == d))}
    for uid, x in have.items():
        if want.get(uid) != x.kind:
            db.delete(x)
    for uid, kind in want.items():
        if uid not in have or have[uid].kind != kind:
            db.add(VmLeave(user_id=uid, date=d, kind=kind))
    db.flush()


def _vname(v: Venue) -> str:
    return v.name.removeprefix("RallyGully ").strip()


def _clock(minute: int) -> str:
    h, m = divmod(minute % 1440, 60)
    return f"{h % 12 or 12}{f':{m:02d}' if m else ''}{'am' if h < 12 else 'pm'}"


def build_message(db: Session, d: date) -> str | None:
    """The day's roster as the admins read it: shifts by venue, then who is off. None if nobody is rostered."""
    rows = list(db.execute(select(VmRoster, User, Venue).join(User, User.id == VmRoster.user_id)
                           .join(Venue, Venue.id == VmRoster.venue_id).where(VmRoster.date == d)
                           .order_by(Venue.id, User.name)))
    if not rows:
        return None
    groups: dict[str, dict] = {"Morning": {}, "Evening": {}}
    for r, u, v in rows:
        w = window(db, r)
        start = w[0] if w else (r.start_min if r.start_min is not None else 17 * 60)
        label = f" ({_clock(w[0])}-{_clock(w[1])})" if r.start_min is not None and w else ""
        groups["Morning" if start < 12 * 60 else "Evening"].setdefault((v.id, label), (v, []))[1].append(u.name)
    lines = [f"Roster Date - {d.day} {d:%B %Y} ( {d:%A} )", ""]
    for name, items in groups.items():
        if items:
            lines += [f"{name} Shift", ""]
            lines += [f"* {_vname(v)} – {' + '.join(names)}{label}" for (_, label), (v, names) in items.items()]
            lines.append("")
    off = list(db.execute(select(VmLeave, User).join(User, User.id == VmLeave.user_id).where(VmLeave.date == d)
                          .order_by(User.name)))
    for kind, title in (("week_off", "Week off :-"), ("leave", "Leave :-")):
        names = [u.name for x, u in off if x.kind == kind]
        if names:
            lines.append(f"{title} {', '.join(names)}")
    return "\n".join(lines).rstrip() + "\n"


def send_digests(db: Session) -> int:
    """Email every active HQ Admin the roster for each day that was created or changed and has since been quiet for
    ROSTER_MAIL_DELAY_MIN minutes (so building a roster entry by entry sends one email, not one per entry). A later
    change sends an updated one. Returns days emailed."""
    now = now_local()
    due = list(db.scalars(select(RosterMail).where(
        RosterMail.date >= today(), RosterMail.changed_at <= now - timedelta(minutes=config.ROSTER_MAIL_DELAY_MIN))
        .order_by(RosterMail.date)))
    admins = [e for e in db.scalars(select(User.email).where(User.role == "hq_admin", User.active.is_(True))
                                    .order_by(User.id))] or [config.OPS_EMAIL]
    sent = 0
    for row in due:
        if row.sent_at and row.sent_at >= row.changed_at:
            continue
        sync_leaves(db, row.date)
        body = build_message(db, row.date)
        digest = hashlib.sha256(body.encode()).hexdigest() if body else None
        row.sent_at = now
        if not body or digest == row.sent_hash:
            continue  # nobody rostered, or the change left the roster as it was
        subject = f"Roster – {row.date:%A} {row.date.day} {row.date:%B %Y}" + (" (updated)" if row.sent_count else "")
        for to in admins:
            notify.email(db, to, subject, body, "roster_day", row.date.toordinal(), template="roster_digest")
        row.sent_hash, row.sent_count = digest, row.sent_count + 1
        sent += 1
    return sent


def _time12(minute: int) -> str:
    h, m = divmod(minute % 1440, 60)
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def send_tomorrow(db: Session) -> int:
    """From VM_ALERT_MIN (11:30 PM) until midnight, WhatsApp each rostered VM their venue and shift for tomorrow:
    one message per allotment. Sent once; if the shift is edited afterwards, the new time is sent again.
    VMs without a phone number are skipped (their roster still works). Returns messages queued."""
    now = now_local()
    if minute_of(now, now.date()) < config.VM_ALERT_MIN:
        return 0
    tomorrow = now.date() + timedelta(days=1)
    sent = 0
    for r in db.scalars(select(VmRoster).where(VmRoster.date == tomorrow).order_by(VmRoster.id)):
        u, v, win = db.get(User, r.user_id), db.get(Venue, r.venue_id), window(db, r)
        if not u or not u.active or not u.phone or not v or not win:
            continue
        slot = f"{_time12(win[0])} to {_time12(win[1])}"
        body = notify.roster_message(v.name, slot)
        if db.scalar(select(Notification.id).where(Notification.template == "roster_allotment",
                                                   Notification.ref_type == "roster", Notification.ref_id == r.id,
                                                   Notification.body == body)):
            continue
        notify.whatsapp(db, u.phone, "roster_allotment", body, "roster", r.id,
                        content_sid=config.TWILIO_CONTENT_SID_ROSTER,
                        variables=notify.named(config.TWILIO_ROSTER_VARS, v.name, slot))
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
    touch(db, d)
    return r


def set_shift(db: Session, actor: User, r: VmRoster, start_min: int | None, end_min: int | None) -> VmRoster:
    start_min, end_min = _check_shift(start_min, end_min)
    before = {"start": r.start_min, "end": r.end_min}
    r.start_min, r.end_min = start_min, end_min
    audit.record(db, actor, "roster.shift", "roster", r.id, before, {"start": start_min, "end": end_min}, r.venue_id)
    touch(db, r.date)
    return r


def remove(db: Session, actor: User, r: VmRoster) -> None:
    u = db.get(User, r.user_id)
    audit.record(db, actor, "roster.remove", "roster", r.id, {"venue_id": r.venue_id, "user": u.email if u else None,
                                                             "date": r.date}, None, r.venue_id)
    db.delete(r)
    db.flush()
    touch(db, r.date)


def copy_range(db: Session, actor: User, src_start: date, dst_start: date, days: int) -> int:
    """Copy a block of days (e.g. last week → this week). Existing entries are kept."""
    if not 1 <= days <= 31:
        raise DomainError("Copy between 1 and 31 days.", 422)
    n = 0
    touched: set[date] = set()
    for r in list(db.scalars(select(VmRoster).where(VmRoster.date >= src_start,
                                                     VmRoster.date < src_start + timedelta(days=days)))):
        target = dst_start + (r.date - src_start)
        u = db.get(User, r.user_id)
        if not u or not u.active or not db.get(VmAssignment, (r.user_id, r.venue_id)):
            continue  # person left the team since; don't carry them forward
        if not db.scalar(select(VmRoster.id).where(VmRoster.venue_id == r.venue_id, VmRoster.user_id == r.user_id,
                                                   VmRoster.date == target)):
            db.add(VmRoster(venue_id=r.venue_id, user_id=r.user_id, date=target, start_min=r.start_min,
                            end_min=r.end_min, created_by=actor.id))
            n += 1
            touched.add(target)
    db.flush()
    for t in sorted(touched):
        touch(db, t)
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
