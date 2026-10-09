"""Daily close (PRD §57-59): completeness checks, VM close, 11:30 PM missed-close escalation."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import Booking, CommunityGame, Customer, DayClose, Participant, User, Venue
from ..timeutil import hhmm, minute_of, now_local
from . import inventory, notify


def missing_items(db: Session, venue_id: int, d: date) -> list[dict]:
    """Unresolved items. ``due`` is False for activity that hasn't started yet today."""
    now = now_local()
    now_min = minute_of(now, d)
    items = []
    for b in db.scalars(select(Booking).where(Booking.venue_id == venue_id, Booking.date == d,
                                              Booking.status == "confirmed").order_by(Booking.start_min)):
        who = db.get(Customer, b.customer_id)
        tag = f"{b.source.title()} {hhmm(b.start_min)}–{hhmm(b.end_min)} · {who.name or who.phone}"
        if b.attendance == "pending":
            what = "court not assigned / arrival not recorded" if b.facility_id is None else "attendance not recorded"
            items.append({"type": "booking_attendance", "ref_id": b.id, "message": f"{tag}: {what}",
                          "due": now_min >= b.start_min})
        if b.payment_status in ("to_collect", "overdue") and b.attendance != "no_show":
            state = "payment overdue — collect or cancel" if b.payment_status == "overdue" else "payment not recorded"
            items.append({"type": "booking_payment", "ref_id": b.id, "message": f"{tag}: {state}",
                          "due": now_min >= b.start_min})
    for g in db.scalars(select(CommunityGame).where(CommunityGame.venue_id == venue_id, CommunityGame.date == d,
                                                    CommunityGame.status == "scheduled")):
        for p in g.participants:
            c = db.get(Customer, p.customer_id)
            tag = f"{g.title} {hhmm(g.start_min)} · {c.name or c.phone}"
            if p.attendance == "added":
                items.append({"type": "participant_attendance", "ref_id": p.id, "game_id": g.id, "message": f"{tag}: attendance not recorded",
                              "due": now_min >= g.start_min})
            elif p.attendance == "attended" and p.payment_status != "paid":
                items.append({"type": "participant_payment", "ref_id": p.id, "game_id": g.id, "message": f"{tag}: payment not recorded",
                              "due": True})
    return items


def status(db: Session, venue_id: int, d: date) -> dict:
    dc = db.scalar(select(DayClose).where(DayClose.venue_id == venue_id, DayClose.date == d))
    items = missing_items(db, venue_id, d)
    hours = inventory.hours_for(db, venue_id, d)
    now = now_local()
    deadline_passed = d < now.date() or (d == now.date() and minute_of(now, d) >= config.DAY_CLOSE_DEADLINE_MIN)
    return {
        "date": d.isoformat(), "operating": hours is not None,
        "status": dc.status if dc else ("missed" if deadline_passed and hours else "open"),
        "closed_at": dc.closed_at if dc else None, "closed_by": dc.closed_by if dc else None,
        "escalated_at": dc.escalated_at if dc else None,
        "missed_items": dc.missing_items if dc and dc.status in ("missed", "closed_late") else None,
        "missing": items, "can_close": not items and hours is not None and (not dc or dc.status == "missed"),
        "deadline": hhmm(config.DAY_CLOSE_DEADLINE_MIN),
    }


def close_day(db: Session, actor: User, venue_id: int, d: date) -> DayClose:
    if d > now_local().date():
        raise DomainError("You can't close a future day.")
    if inventory.hours_for(db, venue_id, d) is None:
        raise DomainError("The venue is not operating on this day.")
    items = missing_items(db, venue_id, d)
    if items:
        raise DomainError(f"{len(items)} item(s) still need resolving before close.")
    dc = db.scalar(select(DayClose).where(DayClose.venue_id == venue_id, DayClose.date == d))
    now = now_local()
    if dc and dc.status in ("closed", "closed_late"):
        raise DomainError("Day already closed.")
    if dc:  # previously missed: record the late close, keep the miss on record
        dc.status, dc.closed_by, dc.closed_at = "closed_late", actor.id, now
    else:
        late = d < now.date() or minute_of(now, d) >= config.DAY_CLOSE_DEADLINE_MIN
        dc = DayClose(venue_id=venue_id, date=d, status="closed_late" if late else "closed",
                      closed_by=actor.id, closed_at=now)
        db.add(dc)
    db.flush()
    audit.record(db, actor, "day.close", "day_close", dc.id, None, {"date": d, "status": dc.status}, venue_id)
    return dc


def escalate_missed(db: Session) -> int:
    """Run after 11:30 PM: record missed closes, log missing items, email Operations once."""
    from .analytics import first_data_date
    now = now_local()
    first = first_data_date(db)
    n = 0
    for v in db.scalars(select(Venue).where(Venue.status == "active")):
        for d in (now.date() - timedelta(days=1), now.date()):
            if first is None or d < first:
                continue  # Pulse had not started recording yet
            if d == now.date() and minute_of(now, d) < config.DAY_CLOSE_DEADLINE_MIN:
                continue
            if inventory.hours_for(db, v.id, d) is None:
                continue
            dc = db.scalar(select(DayClose).where(DayClose.venue_id == v.id, DayClose.date == d))
            if dc:
                continue
            items = missing_items(db, v.id, d) or [{"type": "not_closed", "ref_id": None,
                                                     "message": "Day close was not submitted by the VM"}]
            dc = DayClose(venue_id=v.id, date=d, status="missed", missing_items=items, escalated_at=now)
            db.add(dc)
            db.flush()
            body = "\n".join(f"• {i['message']}" for i in items)
            notify.email(db, config.OPS_EMAIL, f"[Pulse] Missed day close — {v.name}, {d.strftime('%a %d %b %Y')}",
                         f"{v.name} was not closed by the {hhmm(config.DAY_CLOSE_DEADLINE_MIN)} deadline.\n\n"
                         f"Outstanding items ({len(items)}):\n{body}\n", "day_close", dc.id)
            audit.record(db, None, "day.missed", "day_close", dc.id, None, {"date": d, "items": len(items)}, v.id)
            n += 1
    return n
