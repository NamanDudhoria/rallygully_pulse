"""One bill per booking: the court, equipment rentals and RG Store items bought during the session, paid together.

The customer sees and pays a single total. Behind it each part stays in its own basket: the court in the booking
payments ledger (court revenue), rentals on their rental lines, store items as RG Store sales. Paying the bill
records each part in its basket in one transaction, so either everything is paid or nothing is.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .. import audit
from ..audit import DomainError
from ..models import Booking, Customer, StoreSale, User, Venue
from . import bookings, store
from .bookings import METHOD_TEXT


def _store_sales(db: Session, b: Booking) -> list[StoreSale]:
    return list(db.scalars(select(StoreSale).options(selectinload(StoreSale.items)).where(
        StoreSale.booking_id == b.id, StoreSale.status != "voided").order_by(StoreSale.id)))


def _court_due(b: Booking) -> bool:
    return b.status == "confirmed" and b.attendance != "no_show" and b.payment_status in ("to_collect", "overdue")


def bill(db: Session, b: Booking) -> dict:
    lines = []
    court_charged = b.booking_value if (b.status == "confirmed" and b.attendance != "no_show") or b.payment_status == "paid" else 0.0
    lines.append({"basket": "court", "label": "Court booking" + (f" ({b.discount_name})" if b.discount_name else "")
                  + (" · cancelled" if b.status == "cancelled" else ""),
                  "amount": court_charged, "paid": b.payment_status == "paid" or not court_charged,
                  "method": b.payment_method})
    for r in bookings._rental_lines(db, b.id):
        lines.append({"basket": "rental", "id": r.id, "label": f"{r.quantity} × {r.item_name} (rental)", "amount": r.amount,
                      "paid": r.paid_at is not None, "method": r.paid_method})
    for s in _store_sales(db, b):
        for it in s.items:
            lines.append({"basket": "store", "id": s.id, "label": f"{it.quantity} × {it.name}", "amount": it.amount,
                          "paid": s.status == "confirmed", "method": s.payment_method if s.status == "confirmed" else None})
    total = round(sum(x["amount"] for x in lines), 2)
    due = round(sum(x["amount"] for x in lines if not x["paid"]), 2)
    c = db.get(Customer, b.customer_id)
    v = db.get(Venue, b.venue_id)
    by_basket = {}
    for x in lines:
        by_basket[x["basket"]] = round(by_basket.get(x["basket"], 0.0) + x["amount"], 2)
    return {"booking_id": b.id, "bill_no": f"B-{b.id:05d}", "venue": v.name, "date": b.date,
            "customer": {"name": c.name, "phone": c.phone}, "lines": lines, "total": total,
            "paid": round(total - due, 2), "due": due, "by_basket": by_basket}


def pay(db: Session, actor: User, b: Booking, method: str | None) -> dict:
    """Collect everything still owed on the booking's bill with one payment method."""
    if method not in bookings.PAYMENT_METHODS:
        raise DomainError(f"Choose a payment method: {METHOD_TEXT}.", 422)
    paid, anything = {"court": 0.0, "rental": 0.0, "store": 0.0}, False
    if _court_due(b):
        bookings.record_payment(db, actor, b, method)
        paid["court"], anything = b.booking_value, True
    if any(r.paid_at is None for r in bookings._rental_lines(db, b.id)):
        paid["rental"], anything = bookings.pay_rentals(db, actor, b, method), True
    for s in _store_sales(db, b):
        if s.status == "on_bill":
            store.settle(db, actor, s, method)
            paid["store"], anything = round(paid["store"] + s.total, 2), True
    if not anything:
        raise DomainError("Nothing is owed on this bill.")
    audit.record(db, actor, "bill.pay", "booking", b.id, None, {"method": method, **paid}, b.venue_id)
    return bill(db, b)
