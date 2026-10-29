"""Memberships: hours one person bought in a single payment, usable at chosen venues.

A booking by that person is covered from the balance. Hours are reserved when the booking is made (so the
balance can't be promised twice), deducted when it is played, and given back if it is cancelled or a no-show.
The payment for the hours is recorded on the membership, not in the booking payments ledger.
"""
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import DomainError
from ..models import Booking, Facility, Membership, MembershipUse, MembershipVenue, User, Venue
from ..timeutil import now_local, today
from . import customers
from .pricing import quote


def _reserved(db: Session, ids: list[int]) -> dict[int, float]:
    """Hours promised to bookings that haven't been played yet, per membership."""
    if not ids:
        return {}
    rows = db.execute(select(Booking.membership_id, func.sum(Booking.member_hours)).where(
        Booking.membership_id.in_(ids), Booking.status == "confirmed", Booking.attendance == "pending")
        .group_by(Booking.membership_id))
    return {r[0]: float(r[1] or 0.0) for r in rows}


def balance(m: Membership, reserved: float) -> dict:
    left = round(m.total_hours - m.used_hours - reserved, 2)
    return {"total": m.total_hours, "used": m.used_hours, "reserved": reserved, "left": max(left, 0.0)}


def state(m: Membership, left: float, on: date | None = None) -> str:
    on = on or today()
    if m.status == "cancelled":
        return "cancelled"
    if m.expires_on and m.expires_on < on:
        return "expired"
    if m.used_hours >= m.total_hours - 1e-9:
        return "used_up"
    return "active"


def find(db: Session, customer_id: int, venue_id: int, d: date) -> tuple[Membership, float] | None:
    """The membership that would cover this customer at this venue on this day, with its hours left."""
    cands = list(db.scalars(select(Membership).join(MembershipVenue, MembershipVenue.membership_id == Membership.id).where(
        Membership.customer_id == customer_id, Membership.status == "active", MembershipVenue.venue_id == venue_id,
        Membership.starts_on <= d).order_by(Membership.id)))
    cands = [m for m in cands if m.expires_on is None or d <= m.expires_on]
    if not cands:
        return None
    reserved = _reserved(db, [m.id for m in cands])
    live = [(m, balance(m, reserved.get(m.id, 0.0))["left"]) for m in cands]
    live = [x for x in live if x[1] > 0]
    live.sort(key=lambda x: (x[0].expires_on is None, x[0].expires_on or date.max, x[0].id))  # soonest expiry first
    return live[0] if live else None


def cover(db: Session, actor: User | None, b: Booking) -> bool:
    """Cover an unpaid booking from the customer's membership, as far as their hours go. Idempotent. The part
    beyond the hours left stays to be paid at the normal price. Returns whether anything was covered."""
    if b.membership_id is not None or b.status != "confirmed" or b.payment_status not in ("to_collect", "overdue"):
        return False
    hit = find(db, b.customer_id, b.venue_id, b.date)
    if not hit:
        return False
    m, left = hit
    length = b.end_min - b.start_min
    covered_min = min(length, int(round(left * 60)) // 30 * 30)
    if covered_min <= 0:
        return False
    before = {"value": b.booking_value, "payment": b.payment_status}
    if b.list_price is None:
        b.list_price = b.booking_value
    b.membership_id, b.member_hours = m.id, covered_min / 60
    if covered_min >= length:
        b.booking_value, b.payment_status, b.payment_method, b.collected_amount = 0.0, "paid", "membership", 0.0
    else:
        fac = db.get(Facility, b.facility_id) if b.facility_id else None
        tail = quote(db, fac, b.date, b.start_min + covered_min, b.end_min) if fac else None
        b.booking_value = tail if tail is not None else round(b.booking_value * (1 - covered_min / length), 2)
    audit.record(db, actor, "membership.cover", "booking", b.id, before,
                 {"membership": m.id, "hours": b.member_hours, "value": b.booking_value}, b.venue_id)
    return True


def consume(db: Session, actor: User | None, b: Booking) -> None:
    """The booking was played: take its covered hours off the membership (once)."""
    if b.membership_id is None or not b.member_hours:
        return
    if db.scalar(select(MembershipUse.id).where(MembershipUse.booking_id == b.id)):
        return
    m = db.get(Membership, b.membership_id)
    m.used_hours = round(m.used_hours + b.member_hours, 2)
    db.add(MembershipUse(membership_id=m.id, booking_id=b.id, hours=b.member_hours, used_on=b.date))
    audit.record(db, actor, "membership.use", "membership", m.id, None,
                 {"booking": b.id, "hours": b.member_hours, "used": m.used_hours}, b.venue_id)


def on_attended(db: Session, actor: User | None, b: Booking) -> None:
    cover(db, actor, b)  # a member who walks in unpaid is recognised here
    consume(db, actor, b)


def release(db: Session, actor: User | None, b: Booking) -> None:
    """The booking was cancelled: hours already taken go back to the membership."""
    use = db.scalar(select(MembershipUse).where(MembershipUse.booking_id == b.id))
    if not use:
        return
    m = db.get(Membership, use.membership_id)
    m.used_hours = round(max(m.used_hours - use.hours, 0.0), 2)
    audit.record(db, actor, "membership.restore", "membership", m.id, None, {"booking": b.id, "hours": use.hours}, b.venue_id)
    db.delete(use)


def create(db: Session, actor: User, *, phone: str, name: str, venue_ids: list[int], hours: float, amount_paid: float,
           method: str, sold_on: date, starts_on: date, expires_on: date | None, notes: str | None) -> Membership:
    from .bookings import PAYMENT_METHODS
    if not (name or "").strip():
        raise DomainError("Enter the member's name.", 422)
    if not venue_ids:
        raise DomainError("Choose at least one venue.", 422)
    if len({*venue_ids}) != db.scalar(select(func.count(Venue.id)).where(Venue.id.in_(venue_ids))):
        raise DomainError("One of those venues doesn't exist.", 422)
    if not 0.5 <= hours <= 5000 or round(hours * 2) != hours * 2:
        raise DomainError("Hours must be between 0.5 and 5,000, in steps of half an hour.", 422)
    if amount_paid < 0:
        raise DomainError("The amount paid can't be negative.", 422)
    if method not in PAYMENT_METHODS:
        raise DomainError("Choose how the membership was paid: cash, UPI, Razorpay or other.", 422)
    if expires_on and expires_on < starts_on:
        raise DomainError("The expiry date can't be before the start date.", 422)
    customer, _ = customers.get_or_create(db, phone, name)
    m = Membership(customer_id=customer.id, total_hours=hours, amount_paid=amount_paid, payment_method=method,
                   sold_on=sold_on, starts_on=starts_on, expires_on=expires_on, notes=(notes or "").strip()[:200] or None,
                   created_by=actor.id)
    m.venues = [MembershipVenue(venue_id=v) for v in sorted(set(venue_ids))]
    db.add(m)
    db.flush()
    audit.record(db, actor, "membership.create", "membership", m.id, None,
                 {"customer": customer.phone, "hours": hours, "paid": amount_paid, "method": method,
                  "venues": sorted(set(venue_ids)), "expires_on": expires_on}, None)
    return m


def cancel(db: Session, actor: User, m: Membership) -> Membership:
    if m.status == "cancelled":
        raise DomainError("This membership is already cancelled.")
    m.status = "cancelled"
    audit.record(db, actor, "membership.cancel", "membership", m.id, None, {"used": m.used_hours, "total": m.total_hours}, None)
    return m
