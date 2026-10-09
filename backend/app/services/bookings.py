"""District & Direct booking lifecycle: create, assign/reassign court, attendance,
payment, overdue, cancellation, amount change and refunds."""
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import Allocation, AllocationKind, Booking, Facility, Payment, Refund, User, Venue
from ..timeutil import at_minute, hhmm, minute_of, now_local, today
from . import customers, inventory, notify
from .pricing import quote

PAYMENT_METHODS = ("cash", "upi", "card", "other")
CANCEL_REASONS = {
    "court_unavailable": "Court unavailable",
    "maintenance": "Maintenance / facility issue",
    "venue_closed": "Venue closed",
    "weather": "Weather",
    "staff_error": "Staff / operational error",
    "double_booking": "Double booking",
    "payment_overdue": "Payment overdue",
    "other": "Other",
}


def _now_min(d: date) -> int:
    return minute_of(now_local(), d)


def default_window_end(end_min: int) -> int:
    # PRD §19: a 7–8 PM booking may be paid 6:30–8:30 PM (30 min after end).
    return end_min + config.PAYMENT_WINDOW_AFTER_MIN


def _snapshot(b: Booking) -> dict:
    return {k: getattr(b, k) for k in (
        "facility_id", "booking_value", "payment_status", "payment_method", "collected_amount",
        "payment_window_end_min", "attendance", "status", "cancel_reason")}


def _is_hq(actor: User | None) -> bool:
    return actor is None or actor.role == "hq"


def _require_not_cancelled(b: Booking) -> None:
    if b.status == "cancelled":
        raise DomainError("This booking is cancelled.")


def create_direct(db: Session, actor: User, *, venue_id: int, facility_id: int, d: date, start_min: int,
                  duration: int, phone: str, name: str, amount: float | None, pay_now: bool,
                  method: str | None) -> Booking:
    if duration not in (30, 60):
        raise DomainError("Direct bookings are 30 or 60 minutes.", 422)
    if not (name or "").strip():
        raise DomainError("Customer name is required for Direct bookings.", 422)
    if not _is_hq(actor) and d < today():
        raise DomainError("Venue Managers can only create bookings for today or later.")
    fac = db.get(Facility, facility_id)
    if not fac or fac.venue_id != venue_id:
        raise DomainError("Facility not found at this venue.", 422)
    end_min = start_min + duration
    inventory.validate_window(db, venue_id, d, start_min, end_min)
    customer, _ = customers.get_or_create(db, phone, name)
    expected = quote(db, fac, d, start_min, end_min)
    if amount is None:
        if expected is None:
            raise DomainError("No price configured for this slot — enter the amount.", 422)
        amount = expected
    if amount < 0:
        raise DomainError("Amount cannot be negative.", 422)
    b = Booking(source="direct", customer_id=customer.id, venue_id=venue_id, facility_id=fac.id,
                original_facility_id=fac.id, sport=fac.sport, date=d, start_min=start_min, end_min=end_min,
                booking_value=amount, payment_status="to_collect",
                payment_window_end_min=default_window_end(end_min), assigned_at=now_local())
    db.add(b)
    db.flush()
    inventory.allocate(db, venue_id, [fac.id], d, start_min, end_min, AllocationKind.DIRECT, b.id, check_hours=False)
    audit.record(db, actor, "booking.create", "booking", b.id, None,
                 {**_snapshot(b), "source": "direct", "expected_amount": expected}, venue_id)
    if pay_now:
        record_payment(db, actor, b, method, at_creation=True)
    _send_booking_whatsapp(db, b)
    return b


def ingest_district(db: Session, *, external_ref: str, venue_id: int, d: date, start_min: int, end_min: int,
                    amount: float, paid: bool, phone: str, sport: str | None = None) -> tuple[Booking, bool]:
    """Idempotent on (district, external_ref). Returns (booking, created)."""
    existing = db.scalar(select(Booking).where(Booking.source == "district", Booking.external_ref == external_ref))
    if existing:
        return existing, False
    if end_min - start_min not in (30, 60):
        raise DomainError(f"District booking {external_ref}: duration must be 30 or 60 minutes.", 422)
    customer, _ = customers.get_or_create(db, phone)
    b = Booking(source="district", external_ref=external_ref, customer_id=customer.id, venue_id=venue_id,
                sport=sport, date=d, start_min=start_min, end_min=end_min, booking_value=amount,
                payment_status="paid" if paid else "to_collect", payment_method="online" if paid else None,
                collected_amount=amount if paid else 0, payment_window_end_min=default_window_end(end_min))
    db.add(b)
    db.flush()
    if paid:
        db.add(Payment(source_type="booking", source_id=b.id, venue_id=venue_id, activity_date=d,
                       amount=amount, method="online", collected_at=now_local()))
    audit.record(db, None, "booking.create", "booking", b.id, None, {**_snapshot(b), "source": "district",
                                                                      "external_ref": external_ref}, venue_id)
    _send_booking_whatsapp(db, b)
    return b, True


def _send_booking_whatsapp(db: Session, b: Booking) -> None:
    venue = db.get(Venue, b.venue_id)
    when = f"{b.date.strftime('%a %d %b')}, {hhmm(b.start_min)}–{hhmm(b.end_min)}"
    notify.whatsapp(db, customers_phone(db, b), "booking_created",
                    notify.booking_message(venue.name, when, config.COMMUNITY_LINK), "booking", b.id)


def customers_phone(db: Session, b: Booking) -> str:
    from ..models import Customer
    return db.get(Customer, b.customer_id).phone


def assign_court(db: Session, actor: User, b: Booking, facility_id: int) -> Booking:
    """Record the actual court for an arriving District customer — marks it attended."""
    _require_not_cancelled(b)
    if b.facility_id is not None:
        return reassign(db, actor, b, facility_id)
    if b.attendance == "no_show":
        raise DomainError("Booking is marked no-show.")
    if not _is_hq(actor):
        if b.date != today():
            raise DomainError("Courts can only be assigned on the day of the booking.")
        if _now_min(b.date) < b.start_min - 60:
            raise DomainError("Courts can be assigned from 60 minutes before start.")
    fac = db.get(Facility, facility_id)
    if not fac or fac.venue_id != b.venue_id:
        raise DomainError("Facility not found at this venue.", 422)
    before = _snapshot(b)
    inventory.allocate(db, b.venue_id, [fac.id], b.date, b.start_min, b.end_min, b.source, b.id,
                       check_hours=False, district_guard=False)
    b.facility_id = b.original_facility_id = fac.id
    b.sport = fac.sport
    b.assigned_at = now_local()
    b.attendance = "attended"
    audit.record(db, actor, "booking.assign_court", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def reassign(db: Session, actor: User, b: Booking, facility_id: int) -> Booking:
    """Move to another facility; the original facility's slot is freed in full (PRD §24.1)."""
    _require_not_cancelled(b)
    if b.facility_id == facility_id:
        raise DomainError("Booking is already on this facility.", 422)
    if b.facility_id is None:
        raise DomainError("Assign a court first.", 422)
    if not _is_hq(actor) and (b.date < today() or (b.date == today() and _now_min(b.date) >= b.end_min)):
        raise DomainError("This booking has already ended.")
    fac = db.get(Facility, facility_id)
    if not fac or fac.venue_id != b.venue_id:
        raise DomainError("Facility not found at this venue.", 422)
    before = _snapshot(b)
    inventory.check_free(db, b.venue_id, [fac.id], b.date, b.start_min, b.end_min)
    inventory.release(db, b.source, b.id)
    inventory.allocate(db, b.venue_id, [fac.id], b.date, b.start_min, b.end_min, b.source, b.id,
                       check_hours=False, district_guard=False)
    b.facility_id = fac.id
    b.sport = fac.sport
    audit.record(db, actor, "booking.reassign_court", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def mark_attended(db: Session, actor: User, b: Booking) -> Booking:
    _require_not_cancelled(b)
    if b.facility_id is None:
        raise DomainError("Assign a court to check the customer in.", 422)
    before = _snapshot(b)
    b.attendance = "attended"
    audit.record(db, actor, "booking.attended", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def mark_no_show(db: Session, actor: User, b: Booking) -> Booking:
    _require_not_cancelled(b)
    if b.attendance == "no_show":
        return b
    if b.attendance == "attended" and not _is_hq(actor):
        raise DomainError("Customer is already checked in.")
    now_min = _now_min(b.date)
    if now_min < b.start_min + config.NO_SHOW_AFTER_MIN:
        allowed = at_minute(b.date, b.start_min + config.NO_SHOW_AFTER_MIN)
        raise DomainError(f"No-show can be marked from {allowed.strftime('%I:%M %p').lstrip('0')}.")
    before = _snapshot(b)
    b.attendance = "no_show"
    inventory.release(db, b.source, b.id)  # remaining time becomes sellable again
    audit.record(db, actor, "booking.no_show", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def record_payment(db: Session, actor: User, b: Booking, method: str | None, *, at_creation: bool = False) -> Booking:
    _require_not_cancelled(b)
    if method not in PAYMENT_METHODS:
        raise DomainError("Choose a payment method: cash, UPI, card or other.", 422)
    if b.payment_status == "paid":
        raise DomainError("Already paid.")
    if not _is_hq(actor):
        now_min = _now_min(b.date)
        opens = b.start_min - config.PAYMENT_WINDOW_BEFORE_MIN
        if now_min < opens:
            raise DomainError(f"Payment window opens at {hhmm(opens)}. Reserve now and collect then.")
        if now_min > b.payment_window_end_min and not at_creation:
            raise DomainError("Payment window has closed. Extend the window to collect.")
    before = _snapshot(b)
    b.payment_status = "paid"
    b.payment_method = method
    b.collected_amount = b.booking_value  # no partial payments
    db.add(Payment(source_type="booking", source_id=b.id, venue_id=b.venue_id, activity_date=b.date,
                   amount=b.booking_value, method=method, collected_at=now_local(),
                   actor_id=actor.id if actor else None))
    audit.record(db, actor, "payment.record", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def extend_window(db: Session, actor: User, b: Booking, new_end_min: int) -> Booking:
    _require_not_cancelled(b)
    if b.payment_status == "paid":
        raise DomainError("Already paid.")
    if new_end_min <= b.payment_window_end_min:
        raise DomainError("New window must end later than the current one.", 422)
    before = _snapshot(b)
    b.payment_window_end_min = new_end_min
    if b.payment_status == "overdue" and _now_min(b.date) <= new_end_min:
        b.payment_status = "to_collect"
    audit.record(db, actor, "payment.extend_window", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def refresh_overdue(db: Session, venue_id: int | None = None) -> int:
    """Move unpaid bookings past their window to Payment Overdue. Inventory stays held."""
    now = now_local()
    q = select(Booking).where(Booking.status == "confirmed", Booking.payment_status == "to_collect",
                              Booking.attendance != "no_show", Booking.date <= now.date())
    if venue_id:
        q = q.where(Booking.venue_id == venue_id)
    n = 0
    for b in db.scalars(q):
        if minute_of(now, b.date) > b.payment_window_end_min:
            before = _snapshot(b)
            b.payment_status = "overdue"
            audit.record(db, None, "payment.overdue", "booking", b.id, before, _snapshot(b), b.venue_id)
            n += 1
    return n


def cancel(db: Session, actor: User, b: Booking, reason: str, note: str | None) -> Booking:
    _require_not_cancelled(b)
    if b.payment_status == "overdue":
        reason = "payment_overdue"  # overdue is sufficient reason (PRD §21)
    if reason not in CANCEL_REASONS:
        raise DomainError("Choose a cancellation reason.", 422)
    if reason == "other" and not (note or "").strip():
        raise DomainError("Add a note for 'Other'.", 422)
    if b.attendance == "attended" and not _is_hq(actor):
        raise DomainError("Customer already played. HQ can correct this record.")
    before = _snapshot(b)
    b.status = "cancelled"
    b.cancel_reason = reason
    b.cancel_note = (note or "").strip() or None
    b.cancelled_at = now_local()
    inventory.release(db, b.source, b.id)
    audit.record(db, actor, "booking.cancel", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def change_amount(db: Session, actor: User, b: Booking, amount: float) -> Booking:
    if amount < 0:
        raise DomainError("Amount cannot be negative.", 422)
    if b.payment_status == "paid" and not _is_hq(actor):
        raise DomainError("Amount is locked after payment. HQ can correct it.")
    before = _snapshot(b)
    b.booking_value = amount
    if b.payment_status == "paid":
        b.collected_amount = amount
        pay = db.scalar(select(Payment).where(Payment.source_type == "booking", Payment.source_id == b.id,
                                              Payment.void.is_(False)))
        if pay:
            pay.amount = amount
    audit.record(db, actor, "booking.amount_change", "booking", b.id, before, _snapshot(b), b.venue_id)
    return b


def refunded_total(db: Session, booking_id: int) -> float:
    return db.scalar(select(func.coalesce(func.sum(Refund.amount), 0)).where(
        Refund.booking_id == booking_id, Refund.status == "processed")) or 0.0


def refund(db: Session, actor: User, b: Booking, amount: float, reason: str) -> Refund:
    if not _is_hq(actor):
        raise DomainError("Refunds are handled by HQ.", 403)
    if amount <= 0:
        raise DomainError("Refund amount must be positive.", 422)
    if not reason.strip():
        raise DomainError("Refund reason is required.", 422)
    available = b.collected_amount - refunded_total(db, b.id)
    if amount > available + 0.001:
        raise DomainError(f"Refund exceeds collected amount (₹{available:,.0f} refundable).", 422)
    if b.source == "district":
        deadline = at_minute(b.date, b.end_min) + timedelta(days=config.REFUND_WINDOW_DAYS)
        if now_local() > deadline:
            raise DomainError(f"District refund window closed on {deadline.date().isoformat()}.")
    r = Refund(booking_id=b.id, amount=amount, reason=reason.strip(), status="processed",
               processed_by=actor.id if actor else None, processed_at=now_local())
    db.add(r)
    db.flush()
    audit.record(db, actor, "refund.process", "booking", b.id, {"refundable": available},
                 {"refund_id": r.id, "amount": amount, "reason": reason}, b.venue_id)
    return r


def assignment_history(db: Session, b: Booking) -> list[dict]:
    rows = db.scalars(select(Allocation).where(Allocation.kind == b.source, Allocation.ref_id == b.id)
                      .order_by(Allocation.created_at, Allocation.id))
    out = []
    for a in rows:
        fac = db.get(Facility, a.facility_id)
        out.append({"facility_id": a.facility_id, "facility": fac.name if fac else None, "assigned_at": a.created_at,
                    "released_at": a.released_at, "current": a.active})
    return out
