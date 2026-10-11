"""Price lookup. Most specific rule wins (facility > sport > venue), then latest effective_from.

Prices are captured on the booking at creation (``booking_value``); later rule
changes never touch existing bookings (PRD §11.1).
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Facility, PricingRule
from ..timeutil import today


class PriceBook:
    """In-memory rule set for one or more venues; cheap repeated lookups.

    Rules are copied into plain tuples indexed by venue once, so hot analytics loops
    don't pay ORM attribute access per comparison.
    """

    def __init__(self, rules: list[PricingRule]):
        self.rules = rules
        self._by_venue: dict[int, list[tuple]] = {}
        for r in rules:
            self._by_venue.setdefault(r.venue_id, []).append(
                (r.duration_min, r.effective_from, r.weekdays, r.start_min, r.end_min, r.facility_id, r.sport,
                 r.amount, r.id))

    @classmethod
    def load(cls, db: Session, venue_ids: list[int]) -> "PriceBook":
        return cls(list(db.scalars(select(PricingRule).where(PricingRule.venue_id.in_(venue_ids)))))

    def _match(self, facility: Facility, d: date, slot: int, duration: int, as_of: date) -> float | None:
        wd = str(d.weekday())
        fid, sport = facility.id, facility.sport
        best, best_key = None, None
        for dur, eff, wds, start, end, r_fid, r_sport, amount, rid in self._by_venue.get(facility.venue_id, ()):
            if dur != duration or eff > as_of or wd not in wds or not (start <= slot < end):
                continue
            if r_fid is not None and r_fid != fid:
                continue
            if r_sport is not None and r_sport != sport:
                continue
            key = (2 if r_fid is not None else 1 if r_sport is not None else 0, eff, rid)
            if best_key is None or key > best_key:
                best, best_key = amount, key
        return best

    def price(self, facility: Facility, d: date, start: int, duration: int, as_of: date | None = None) -> float | None:
        as_of = as_of or today()
        amount = self._match(facility, d, start, duration, as_of)
        if amount is not None:
            return amount
        if duration == 30:
            a60 = self._match(facility, d, start, 60, as_of)
            return round(a60 / 2, 2) if a60 is not None else None
        if duration == 60:
            a = self._match(facility, d, start, 30, as_of)
            b = self._match(facility, d, start + 30, 30, as_of)
            return a + b if a is not None and b is not None else None
        return None

    def unit_value(self, facility: Facility, d: date, slot: int, as_of: date | None = None) -> float:
        """Value of one 30-minute unit — used for Revenue Opportunity."""
        p = self.price(facility, d, slot, 30, as_of or d)
        return p or 0.0


def quote(db: Session, facility: Facility, d: date, start: int, end: int) -> float | None:
    book = PriceBook.load(db, [facility.venue_id])
    duration = end - start
    if duration in (30, 60):
        return book.price(facility, d, start, duration)
    total = 0.0
    for slot in range(start, end, 30):
        total += book.unit_value(facility, d, slot, today())
    return total
