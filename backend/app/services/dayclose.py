"""Daily close (PRD §57-59): completeness checks, VM close, 11:30 PM missed-close escalation."""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from .. import audit, config
from ..audit import DomainError
from ..models import Booking, CommunityGame, Customer, DayClose, Participant, RentalCharge, User, Venue
from ..permissions import roster_scoped
from ..timeutil import hhmm, minute_of, now_local, tz_label
from . import inventory, notify, roster


def missing_items(db: Session, venue_id: int, d: date) -> list[dict]:
    """Unresolved items. ``due`` is False for activity that hasn't started yet today."""
    now = now_local()
    now_min = minute_of(now, d)
    items = []
    day_bookings = list(db.scalars(select(Booking).where(Booking.venue_id == venue_id, Booking.date == d,
                                                         Booking.status == "confirmed").order_by(Booking.start_min)))
    games = list(db.scalars(select(CommunityGame).where(CommunityGame.venue_id == venue_id, CommunityGame.date == d,
                                                        CommunityGame.status == "scheduled")
                            .options(selectinload(CommunityGame.participants))))
    # One query for every customer named below, instead of one per booking and participant.
    wanted = {b.customer_id for b in day_bookings} | {p.customer_id for g in games for p in g.participants if p.customer_id}
    customers = {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(wanted)))} if wanted else {}
    for b in day_bookings:
        who = customers.get(b.customer_id)
        tag = f"{b.source.title()} {hhmm(b.start_min)}–{hhmm(b.end_min)} · {who.name or who.phone}"
        if b.attendance == "pending":
            what = "court not assigned / arrival not recorded" if b.facility_id is None else "attendance not recorded"
            items.append({"type": "booking_attendance", "ref_id": b.id, "message": f"{tag}: {what}",
                          "due": now_min >= b.start_min})
        if b.payment_status in ("to_collect", "overdue") and b.attendance != "no_show":
            state = "payment overdue — collect or cancel" if b.payment_status == "overdue" else "payment not recorded"
            items.append({"type": "booking_payment", "ref_id": b.id, "message": f"{tag}: {state}",
                          "due": now_min >= b.start_min})
    unpaid_rentals = {r[0]: r[1] for r in db.execute(
        select(RentalCharge.booking_id, func.sum(RentalCharge.amount)).where(
            RentalCharge.booking_id.in_([b.id for b in day_bookings]), RentalCharge.paid_at.is_(None))
        .group_by(RentalCharge.booking_id))} if day_bookings else {}
    for b in day_bookings:
        if b.id in unpaid_rentals and b.attendance != "no_show":
            who = customers.get(b.customer_id)
            items.append({"type": "booking_payment", "ref_id": b.id, "due": True,
                          "message": f"{b.source.title()} {hhmm(b.start_min)}–{hhmm(b.end_min)} · {who.name or who.phone}: "
                                     f"equipment rental ₹{unpaid_rentals[b.id]:,.0f} not paid"})
    for g in games:
        for p in g.participants:
            c = customers.get(p.customer_id) if p.customer_id else None
            tag = f"{g.title} {hhmm(g.start_min)} · {(c and (c.name or c.phone)) or p.name or 'Player'}"
            if p.attendance == "added":
                items.append({"type": "participant_attendance", "ref_id": p.id, "game_id": g.id, "message": f"{tag}: attendance not recorded",
                              "due": now_min >= g.start_min})
            elif p.attendance == "attended" and p.payment_status != "paid":
                items.append({"type": "participant_payment", "ref_id": p.id, "game_id": g.id, "message": f"{tag}: payment not recorded",
                              "due": True})
    return items


def _state(dc, hours, d: date, now) -> str:
    deadline_passed = d < now.date() or (d == now.date() and minute_of(now, d) >= config.DAY_CLOSE_DEADLINE_MIN)
    return dc.status if dc else ("missed" if deadline_passed and hours else "open")


def states(db: Session, venue_ids: list[int], d: date, hours_by_venue: dict) -> dict[int, str]:
    """Just the close state of every venue for ``d`` (one query), for pages that don't need the item list."""
    dcs = {dc.venue_id: dc for dc in db.scalars(select(DayClose).where(DayClose.venue_id.in_(venue_ids),
                                                                       DayClose.date == d))}
    now = now_local()
    return {v: _state(dcs.get(v), hours_by_venue[(v, d)], d, now) for v in venue_ids}


_UNSET = object()


def status(db: Session, venue_id: int, d: date, hours=_UNSET) -> dict:
    """``hours`` may be passed when the caller already has the venue's hours for ``d`` (None = closed)."""
    dc = db.scalar(select(DayClose).where(DayClose.venue_id == venue_id, DayClose.date == d))
    items = missing_items(db, venue_id, d)
    if hours is _UNSET:
        hours = inventory.hours_for(db, venue_id, d)
    now = now_local()
    return {
        "date": d.isoformat(), "operating": hours is not None,
        "status": _state(dc, hours, d, now),
        "closed_at": dc.closed_at if dc else None, "closed_by": dc.closed_by if dc else None,
        "escalated_at": dc.escalated_at if dc else None,
        "missed_items": dc.missing_items if dc and dc.status in ("missed", "closed_late") else None,
        "missing": items, "can_close": not items and hours is not None and (not dc or dc.status == "missed"),
        "deadline": hhmm(config.DAY_CLOSE_DEADLINE_MIN),
        "rostered": roster.names_for(db, venue_id, d),
    }


def close_day(db: Session, actor: User, venue_id: int, d: date) -> DayClose:
    if d > now_local().date():
        raise DomainError("You can't close a future day.")
    if inventory.hours_for(db, venue_id, d) is None:
        raise DomainError("The venue is not operating on this day.")
    if roster_scoped(actor):
        from . import attendance
        attendance.require_shift_over(db, actor, venue_id, d, "close the day")
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
    if roster_scoped(actor):
        from . import attendance
        attendance.close_out(db, actor, venue_id, d)  # closing the day is the VM's out-attendance
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
            on_duty = ", ".join(roster.names_for(db, v.id, d)) or "nobody was rostered"
            notify.email(db, config.OPS_EMAIL, f"[Pulse] Missed day close — {v.name}, {d.strftime('%a %d %b %Y')}",
                         f"{v.name} was not closed by the {hhmm(config.DAY_CLOSE_DEADLINE_MIN)} {tz_label()} deadline.\n"
                         f"Rostered VM(s): {on_duty}\n\n"
                         f"Outstanding items ({len(items)}):\n{body}\n", "day_close", dc.id)
            audit.record(db, None, "day.missed", "day_close", dc.id, None, {"date": d, "items": len(items)}, v.id)
            n += 1
    return n
