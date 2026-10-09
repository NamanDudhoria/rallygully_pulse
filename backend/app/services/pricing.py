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
    """In-memory rule set for one or more venues; cheap repeated lookups."""

    def __init__(self, rules: list[PricingRule]):
        self.rules = rules

    @classmethod
    def load(cls, db: Session, venue_ids: list[int]) -> "PriceBook":
        return cls(list(db.scalars(select(PricingRule).where(PricingRule.venue_id.in_(venue_ids)))))

    def _match(self, facility: Facility, d: date, slot: int, duration: int, as_of: date) -> PricingRule | None:
        wd = str(d.weekday())
        best, best_key = None, None
        for r in self.rules:
            if r.venue_id != facility.venue_id or r.duration_min != duration or r.effective_from > as_of:
                continue
            if wd not in r.weekdays or not (r.start_min <= slot < r.end_min):
                continue
            if r.facility_id is not None and r.facility_id != facility.id:
                continue
            if r.sport is not None and r.sport != facility.sport:
                continue
            spec = 2 if r.facility_id is not None else 1 if r.sport is not None else 0
            key = (spec, r.effective_from, r.id)
            if best_key is None or key > best_key:
                best, best_key = r, key
        return best

    def price(self, facility: Facility, d: date, start: int, duration: int, as_of: date | None = None) -> float | None:
        as_of = as_of or today()
        rule = self._match(facility, d, start, duration, as_of)
        if rule:
            return rule.amount
        if duration == 30:
            r60 = self._match(facility, d, start, 60, as_of)
            return round(r60.amount / 2, 2) if r60 else None
        if duration == 60:
            a = self._match(facility, d, start, 30, as_of)
            b = self._match(facility, d, start + 30, 30, as_of)
            return a.amount + b.amount if a and b else None
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
