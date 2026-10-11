"""Venue configuration edits that can't strand existing activity.

Closing a court, shrinking hours, closing a date or deactivating a venue is refused
while future activity still sits in the affected time; the error names what to move or
cancel first. Prices are versioned, never edited in place: a change is a new rule
effective from a date, so past bookings and historical analytics keep their prices.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import DomainError
from ..models import Allocation, Booking, Facility, PricingRule, SpecialDate
from ..timeutil import hhmm, today
from .inventory import LABELS


def _future_activity(db: Session, venue_id: int, facility_id: int | None = None) -> list[tuple[date, int, int, str]]:
    """(date, start, end, label) for live activity from today on; includes unassigned District bookings."""
    q = select(Allocation).where(Allocation.venue_id == venue_id, Allocation.active.is_(True),
                                 Allocation.date >= today())
    if facility_id:
        q = q.where(Allocation.facility_id == facility_id)
    out = [(a.date, a.start_min, a.end_min, LABELS.get(a.kind, a.kind)) for a in db.scalars(q)]
    if facility_id is None:
        out += [(b.date, b.start_min, b.end_min, "District (court not assigned)") for b in db.scalars(select(Booking).where(
            Booking.venue_id == venue_id, Booking.source == "district", Booking.status == "confirmed",
            Booking.facility_id.is_(None), Booking.date >= today()))]
    return sorted(out)


def _refuse(items: list, what: str) -> None:
    if not items:
        return
    sample = "; ".join(f"{d.strftime('%a %d %b')} {hhmm(s)}–{hhmm(e)} {label}" for d, s, e, label in items[:3])
    more = f" and {len(items) - 3} more" if len(items) > 3 else ""
    raise DomainError(f"{what} would strand {len(items)} upcoming booking(s)/allocation(s): {sample}{more}. "
                      "Move or cancel them first.")


def _outside(items, hours: tuple[int, int] | None):
    return [i for i in items if hours is None or i[1] < hours[0] or i[2] > hours[1]]


def check_venue_deactivation(db: Session, venue_id: int) -> None:
    _refuse(_future_activity(db, venue_id), "Deactivating this venue")


def check_facility_change(db: Session, f: Facility, new_status: str, new_sport: str) -> None:
    if new_status == f.status and new_sport == f.sport:
        return
    what = f"Deactivating {f.name}" if new_status != f.status else f"Changing {f.name}'s sport"
    _refuse(_future_activity(db, f.venue_id, f.id), what)


def check_weekly_hours(db: Session, venue_id: int, weekday: int, hours: tuple[int, int] | None) -> None:
    """Future dates on this weekday without a special-date override must still fit their activity."""
    overridden = {s.date for s in db.scalars(select(SpecialDate).where(SpecialDate.venue_id == venue_id,
                                                                        SpecialDate.date >= today()))}
    items = [i for i in _future_activity(db, venue_id) if i[0].weekday() == weekday and i[0] not in overridden]
    _refuse(_outside(items, hours), f"New {['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'][weekday]} hours")


def check_date_hours(db: Session, venue_id: int, d: date, hours: tuple[int, int] | None, what: str) -> None:
    items = [i for i in _future_activity(db, venue_id) if i[0] == d]
    _refuse(_outside(items, hours), what)


# ── Pricing versions ─────────────────────────────────────────────────────────
def _scope(r: PricingRule) -> tuple:
    return (r.venue_id, r.facility_id, r.sport, r.weekdays, r.start_min, r.end_min, r.duration_min)


def rule_status(r: PricingRule, all_rules: list[PricingRule]) -> str:
    t = today()
    if r.effective_from > t:
        return "scheduled"
    newer = [x for x in all_rules if _scope(x) == _scope(r) and x.id != r.id
             and r.effective_from < x.effective_from <= t]
    return "superseded" if newer else "current"


def change_price(db: Session, r: PricingRule, amount: float, effective_from: date) -> PricingRule:
    if effective_from < today():
        raise DomainError("A price change can start today at the earliest — past bookings keep their price.", 422)
    if amount < 0:
        raise DomainError("Price can't be negative.", 422)
    clash = db.scalar(select(PricingRule).where(
        PricingRule.venue_id == r.venue_id, PricingRule.facility_id.is_(r.facility_id) if r.facility_id is None
        else PricingRule.facility_id == r.facility_id,
        PricingRule.sport.is_(None) if r.sport is None else PricingRule.sport == r.sport,
        PricingRule.weekdays == r.weekdays, PricingRule.start_min == r.start_min, PricingRule.end_min == r.end_min,
        PricingRule.duration_min == r.duration_min, PricingRule.effective_from == effective_from))
    if clash:
        if clash.effective_from <= today():
            raise DomainError("A price already starts on that date for this slot.", 409)
        clash.amount = amount  # re-scheduling a future change just updates it
        return clash
    new = PricingRule(venue_id=r.venue_id, facility_id=r.facility_id, sport=r.sport, weekdays=r.weekdays,
                      start_min=r.start_min, end_min=r.end_min, duration_min=r.duration_min, amount=amount,
                      effective_from=effective_from)
    db.add(new)
    db.flush()
    return new


def delete_rule(db: Session, r: PricingRule) -> None:
    if r.effective_from <= today():
        raise DomainError("This price is (or was) in use, so it stays for history. Schedule a new price instead.", 409)
    db.delete(r)
