"""Venue day: performance summary, availability grid, quotes, and day close."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serialize
from ..auth import current_user, ensure_venue_access, venue_ids_for
from ..db import get_db
from ..models import (AcademyOccurrence, Allocation, Booking, CommunityGame, CorporateEvent, Customer, Facility,
                      OperationalBlock, User, Venue)
from ..services import analytics, bookings, dayclose, inventory
from ..services.pricing import quote
from ..timeutil import hhmm, minute_of, now_local
from .common import day_or_today, get_or_404, minutes

router = APIRouter(prefix="/api/venues", tags=["venues"])

DAY_METRICS = ["net_revenue", "collected", "occupancy", "utilization", "total_activity", "opportunity",
               "unique_customers"]


@router.get("")
def list_venues(user: User = Depends(current_user), db: Session = Depends(get_db)):
    allowed = venue_ids_for(user)
    q = select(Venue).order_by(Venue.id)
    if allowed is not None:
        q = q.where(Venue.id.in_(allowed))
    return [serialize.venue(v, full=True) for v in db.scalars(q)]


def _venue(db: Session, user: User, venue_id: int) -> Venue:
    ensure_venue_access(user, venue_id)
    return get_or_404(db, Venue, venue_id)


def _activities(db: Session, venue_id: int, d: date) -> dict:
    return {
        "bookings": [serialize.booking(db, b) for b in db.scalars(select(Booking).where(
            Booking.venue_id == venue_id, Booking.date == d).order_by(Booking.start_min, Booking.id))],
        "games": [serialize.game(db, g) for g in db.scalars(select(CommunityGame).where(
            CommunityGame.venue_id == venue_id, CommunityGame.date == d).order_by(CommunityGame.start_min))],
        "academy": [serialize.occurrence(db, o) for o in db.scalars(select(AcademyOccurrence).where(
            AcademyOccurrence.venue_id == venue_id, AcademyOccurrence.date == d,
            AcademyOccurrence.status != "skipped").order_by(AcademyOccurrence.start_min))],
        "events": [serialize.event(db, e) for e in db.scalars(select(CorporateEvent).where(
            CorporateEvent.venue_id == venue_id, CorporateEvent.date == d).order_by(CorporateEvent.start_min))],
        "blocks": [serialize.block(db, b) for b in db.scalars(select(OperationalBlock).where(
            OperationalBlock.venue_id == venue_id, OperationalBlock.date == d).order_by(OperationalBlock.start_min))],
    }


def intraday_curve(db: Session, venue_ids: list[int], d: date, hours: tuple[int, int] | None) -> dict | None:
    """Cumulative net revenue & occupied units by hour, vs the same weekday's last 4 weeks."""
    if not hours:
        return None
    now = now_local()
    series = analytics.daily_series(db, venue_ids, d - timedelta(days=28), d)
    hist = [series[x] for x in (d - timedelta(days=7 * i) for i in range(1, 5))
            if x in series and series[x]["operational_units"] > 0]
    cur = series.get(d)
    marks = [hours[0]] + list(range(hours[0] + 60, hours[1] + 1, 60))
    if marks and marks[-1] != hours[1]:
        marks.append(hours[1])

    def upto(m: dict, minute: int, key: str) -> float:
        return sum(m[key][: minute // 60])

    points = []
    for mk in marks:
        reached = d < now.date() or (d == now.date() and minute_of(now, d) >= mk)
        points.append({
            "minute": mk, "label": hhmm(mk),
            "net_revenue": upto(cur, mk, "hourly_net") if cur and reached else None,
            "occupied_units": upto(cur, mk, "hourly_occ") if cur and reached else None,
            "baseline_net_revenue": sum(upto(h, mk, "hourly_net") for h in hist) / len(hist) if hist else None,
            "baseline_occupied_units": sum(upto(h, mk, "hourly_occ") for h in hist) / len(hist) if hist else None,
        })
    return {"points": points, "baseline_samples": len(hist)}


@router.get("/{venue_id}/day")
def venue_day(venue_id: int, date: date | None = None, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    v = _venue(db, user, venue_id)
    d = day_or_today(date)
    bookings.refresh_overdue(db, venue_id)
    db.commit()
    hours = inventory.hours_for(db, venue_id, d)
    now = now_local()
    cutoff = analytics.now_cutoff(d)
    if hours and cutoff is not None:
        cutoff = max(min(cutoff, hours[1]), hours[0])
    full = analytics.daily_series(db, [venue_id], d, d).get(d)
    trend = analytics.daily_series(db, [venue_id], d - timedelta(days=29), d)
    acts = _activities(db, venue_id, d)
    is_today = d == now.date()
    return {
        "venue": serialize.venue(v, full=True), "date": d, "is_today": is_today,
        "now_min": minute_of(now, d) if is_today else None,
        "hours": {"open_min": hours[0], "close_min": hours[1]} if hours else None,
        "metrics": full,
        # Today: everything up to now, so headline numbers match their same-time-of-day baselines.
        "metrics_now": analytics.daily_series(db, [venue_id], d, d, cutoff).get(d) if is_today and hours else None,
        "comparison_mode": "intraday" if is_today else "full_day",
        "comparisons": analytics.comparisons(db, [venue_id], d, DAY_METRICS, cutoff if is_today else None),
        "intraday": intraday_curve(db, [venue_id], d, hours),
        "trend": [{"date": k, "net_revenue": m["net_revenue"], "occupancy": m["occupancy"],
                   "total_activity": m["total_activity"]} for k, m in trend.items()],
        "repeat": analytics.repeat_rate(db, d, d - timedelta(days=89), [venue_id]),
        "history_days": analytics.history_days(db),
        "activities": acts,
        "day_close": dayclose.status(db, venue_id, d),
    }


@router.get("/{venue_id}/grid")
def grid(venue_id: int, date: date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    v = _venue(db, user, venue_id)
    d = day_or_today(date)
    hours = inventory.hours_for(db, venue_id, d)
    facilities = inventory.active_facilities(db, venue_id)
    cells = []
    for a in sorted(inventory.venue_allocations(db, venue_id, d), key=lambda a: a.start_min):
        cells.append(_cell(db, a))
    pending = [serialize.booking(db, b) for b in db.scalars(select(Booking).where(
        Booking.venue_id == venue_id, Booking.date == d, Booking.source == "district", Booking.status == "confirmed",
        Booking.facility_id.is_(None), Booking.attendance == "pending").order_by(Booking.start_min))]
    now = now_local()
    return {"venue": serialize.venue(v), "date": d,
            "hours": {"open_min": hours[0], "close_min": hours[1]} if hours else None,
            "now_min": minute_of(now, d) if d == now.date() else None,
            "facilities": [serialize.facility(f) for f in facilities], "cells": cells,
            "unassigned_district": pending}


def _cell(db: Session, a: Allocation) -> dict:
    base = {"allocation_id": a.id, "facility_id": a.facility_id, "start_min": a.start_min, "end_min": a.end_min,
            "kind": a.kind, "ref_id": a.ref_id, "time": f"{hhmm(a.start_min)}–{hhmm(a.end_min)}"}
    if a.kind in ("district", "direct"):
        b = db.get(Booking, a.ref_id)
        c = db.get(Customer, b.customer_id)
        base.update(title=c.name or c.phone, amount=b.booking_value, payment_status=b.payment_status,
                    attendance=b.attendance, external_ref=b.external_ref,
                    moved=b.original_facility_id not in (None, b.facility_id))
    elif a.kind == "community":
        g = db.get(CommunityGame, a.ref_id)
        base.update(title=g.title, per_person=g.per_person, participants=len(g.participants), capacity=g.capacity,
                    sport=g.sport)
    elif a.kind == "academy":
        o = db.get(AcademyOccurrence, a.ref_id)
        base.update(title=o.series.name, amount=o.value)
    elif a.kind == "corporate":
        e = db.get(CorporateEvent, a.ref_id)
        base.update(title=e.company, amount=e.amount, payment_status=e.payment_status)
    else:
        blk = db.get(OperationalBlock, a.ref_id)
        base.update(title=blk.reason.replace("_", " ").title(), note=blk.note, released=blk.released_at is not None)
    return base


@router.get("/{venue_id}/quote")
def get_quote(venue_id: int, facility_id: int, date: date, start: str, duration: int = 60,
              user: User = Depends(current_user), db: Session = Depends(get_db)):
    _venue(db, user, venue_id)
    f = get_or_404(db, Facility, facility_id)
    s = minutes(start)
    free = not any(a for a in inventory.venue_allocations(db, venue_id, date)
                   if a.facility_id == facility_id and a.start_min < s + duration and a.end_min > s)
    return {"amount": quote(db, f, date, s, s + duration), "available": free}


@router.get("/{venue_id}/free")
def free(venue_id: int, date: date, start: str, end: str, sport: str | None = None,
         user: User = Depends(current_user), db: Session = Depends(get_db)):
    _venue(db, user, venue_id)
    return [serialize.facility(f) for f in inventory.free_facilities(db, venue_id, date, minutes(start), minutes(end), sport)]


@router.get("/{venue_id}/close")
def close_status(venue_id: int, date: date | None = None, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    _venue(db, user, venue_id)
    return dayclose.status(db, venue_id, day_or_today(date))


@router.post("/{venue_id}/close")
def close(venue_id: int, date: date | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _venue(db, user, venue_id)
    d = day_or_today(date)
    dayclose.close_day(db, user, venue_id, d)
    db.commit()
    return dayclose.status(db, venue_id, d)
