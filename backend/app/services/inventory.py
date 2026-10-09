"""Inventory: operating hours, the allocation ledger, conflict checks, availability.

Hard rule (PRD §13): a facility never has two overlapping active allocations.
All writes go through ``allocate`` which re-checks inside ``inventory_lock``.

District bookings arrive without a court. Until the VM assigns one, they are
held as venue-level demand: an allocation is refused if it would leave fewer
free facilities in a slot than there are unassigned District bookings in it.
"""
from datetime import date

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from .. import config
from ..audit import DomainError
from ..db import inventory_lock
from ..models import (Allocation, AllocationKind, Booking, Facility, SpecialDate, Venue, WeeklyHours)
from ..timeutil import hhmm, now_local, on_boundary

S = config.SLOT_MINUTES


def hours_for(db: Session, venue_id: int, d: date) -> tuple[int, int] | None:
    special = db.scalar(select(SpecialDate).where(SpecialDate.venue_id == venue_id, SpecialDate.date == d))
    if special:
        return None if special.closed else (special.open_min, special.close_min)
    wh = db.get(WeeklyHours, (venue_id, d.weekday()))
    if not wh or wh.closed or wh.close_min <= wh.open_min:
        return None
    return wh.open_min, wh.close_min


def hours_map(db: Session, venue_ids: list[int], start: date, end: date) -> dict[tuple[int, date], tuple[int, int] | None]:
    """Bulk operating hours for analytics: {(venue_id, date): (open, close) | None}."""
    from datetime import timedelta
    weekly = {(w.venue_id, w.weekday): w for w in db.scalars(select(WeeklyHours).where(WeeklyHours.venue_id.in_(venue_ids)))}
    specials = {(s.venue_id, s.date): s for s in db.scalars(select(SpecialDate).where(
        SpecialDate.venue_id.in_(venue_ids), SpecialDate.date >= start, SpecialDate.date <= end))}
    out = {}
    d = start
    while d <= end:
        for v in venue_ids:
            sp = specials.get((v, d))
            if sp:
                out[(v, d)] = None if sp.closed else (sp.open_min, sp.close_min)
            else:
                w = weekly.get((v, d.weekday()))
                out[(v, d)] = None if (not w or w.closed or w.close_min <= w.open_min) else (w.open_min, w.close_min)
        d += timedelta(days=1)
    return out


def active_facilities(db: Session, venue_id: int) -> list[Facility]:
    return list(db.scalars(select(Facility).where(Facility.venue_id == venue_id, Facility.status == "active")
                           .order_by(Facility.sort_order, Facility.id)))


def validate_window(db: Session, venue_id: int, d: date, start: int, end: int) -> None:
    if not (on_boundary(start) and on_boundary(end)):
        raise DomainError("Times must start and end on :00 or :30.", 422)
    if end <= start:
        raise DomainError("End time must be after start time.", 422)
    hours = hours_for(db, venue_id, d)
    if hours is None:
        raise DomainError(f"The venue is closed on {d.isoformat()}.")
    if start < hours[0] or end > hours[1]:
        raise DomainError(f"Outside operating hours ({hhmm(hours[0])}–{hhmm(hours[1])}).")


def _overlapping(db: Session, facility_id: int, d: date, start: int, end: int):
    return list(db.scalars(select(Allocation).where(
        Allocation.facility_id == facility_id, Allocation.date == d, Allocation.active.is_(True),
        Allocation.start_min < end, Allocation.end_min > start)))


def _unassigned_district(db: Session, venue_id: int, d: date, exclude_booking: int | None = None) -> list[Booking]:
    q = select(Booking).where(Booking.venue_id == venue_id, Booking.date == d, Booking.source == "district",
                              Booking.status == "confirmed", Booking.facility_id.is_(None),
                              Booking.attendance == "pending")
    rows = list(db.scalars(q))
    return [b for b in rows if b.id != exclude_booking]


def _check_district_capacity(db: Session, venue_id: int, d: date, start: int, end: int,
                             taking: set[int], exclude_booking: int | None = None) -> None:
    pending = _unassigned_district(db, venue_id, d, exclude_booking)
    if not pending:
        return
    facilities = active_facilities(db, venue_id)
    allocs = list(db.scalars(select(Allocation).where(Allocation.venue_id == venue_id, Allocation.date == d,
                                                      Allocation.active.is_(True))))
    for slot in range(start, end, S):
        demand = sum(1 for b in pending if b.start_min <= slot < b.end_min)
        if not demand:
            continue
        busy = {a.facility_id for a in allocs if a.start_min <= slot < a.end_min} | taking
        free = sum(1 for f in facilities if f.id not in busy)
        if free < demand:
            raise DomainError(
                f"{demand} unassigned District booking(s) at {hhmm(slot)} need a free court. "
                "Assign them first or pick another time.")


def allocate(db: Session, venue_id: int, facility_ids: list[int], d: date, start: int, end: int,
             kind: str, ref_id: int, *, check_hours: bool = True, district_guard: bool = True,
             exclude_booking: int | None = None) -> list[Allocation]:
    """Create active allocations atomically (caller commits). Raises DomainError on conflict."""
    if kind not in AllocationKind.ALL:
        raise ValueError(kind)
    if not facility_ids:
        raise DomainError("Select at least one facility.", 422)
    with inventory_lock:
        if check_hours:
            validate_window(db, venue_id, d, start, end)
        for fid in facility_ids:
            fac = db.get(Facility, fid)
            if not fac or fac.venue_id != venue_id:
                raise DomainError("Facility does not belong to this venue.", 422)
            if fac.status != "active":
                raise DomainError(f"{fac.name} is not active.")
            clash = _overlapping(db, fid, d, start, end)
            if clash:
                c = clash[0]
                raise DomainError(f"{fac.name} is already allocated ({label(c.kind)}) "
                                  f"{hhmm(c.start_min)}–{hhmm(c.end_min)}.")
        if district_guard and kind != AllocationKind.DISTRICT:
            _check_district_capacity(db, venue_id, d, start, end, set(facility_ids), exclude_booking)
        rows = [Allocation(venue_id=venue_id, facility_id=fid, date=d, start_min=start, end_min=end,
                           kind=kind, ref_id=ref_id) for fid in facility_ids]
        db.add_all(rows)
        db.flush()
        return rows


def release(db: Session, kind: str, ref_id: int) -> list[Allocation]:
    rows = list(db.scalars(select(Allocation).where(Allocation.kind == kind, Allocation.ref_id == ref_id,
                                                    Allocation.active.is_(True))))
    now = now_local()
    for r in rows:
        r.active = False
        r.released_at = now
    db.flush()
    return rows


def facilities_of(db: Session, kind: str, ref_id: int) -> list[int]:
    return [a.facility_id for a in db.scalars(select(Allocation).where(
        Allocation.kind == kind, Allocation.ref_id == ref_id, Allocation.active.is_(True)))]


def check_free(db: Session, venue_id: int, facility_ids: list[int], d: date, start: int, end: int,
               ignore: tuple[str, int] | None = None) -> None:
    """Validate without writing (used when moving multi-facility allocations)."""
    for fid in facility_ids:
        clash = [a for a in _overlapping(db, fid, d, start, end)
                 if not (ignore and a.kind == ignore[0] and a.ref_id == ignore[1])]
        if clash:
            fac = db.get(Facility, fid)
            raise DomainError(f"{fac.name} is not available {hhmm(start)}–{hhmm(end)}.")


LABELS = {
    "district": "District", "direct": "Direct", "community": "Community Game",
    "academy": "Academy", "corporate": "Corporate/Private", "block": "Operational Block",
}


def label(kind: str) -> str:
    return LABELS.get(kind, kind)


def free_facilities(db: Session, venue_id: int, d: date, start: int, end: int, sport: str | None = None) -> list[Facility]:
    out = []
    for f in active_facilities(db, venue_id):
        if sport and f.sport != sport:
            continue
        if not _overlapping(db, f.id, d, start, end):
            out.append(f)
    return out


def venue_allocations(db: Session, venue_id: int, d: date) -> list[Allocation]:
    return list(db.scalars(select(Allocation).where(and_(
        Allocation.venue_id == venue_id, Allocation.date == d, Allocation.active.is_(True)))))


def venue_or_404(db: Session, venue_id: int) -> Venue:
    v = db.get(Venue, venue_id)
    if not v:
        raise DomainError("Venue not found.", 404)
    return v
