"""Venue cash balances.

A venue's balance is the cash collected there since its account started (bookings, community games, academy,
corporate, equipment rentals and RG Store sales paid in cash), less cash refunds, less deposits, plus non-deposit additions
entered by an HQ Admin. Collections are read from the payment records themselves, so a corrected or voided
payment corrects the balance too.
"""
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import DomainError
from ..models import (Booking, CashAccount, CashEntry, Payment, Refund, RentalCharge, StoreSale, User, Venue)
from ..timeutil import now_local

BEGINNING = datetime(2000, 1, 1)  # an account created after the fact counts everything recorded so far


def ensure_accounts(db: Session, since: datetime | None = None) -> int:
    """Give every venue a cash account. At startup and on a new venue ``since`` is now: cash taken before cash
    tracking began isn't counted (HQ adds an opening balance if they want one)."""
    have = set(db.scalars(select(CashAccount.venue_id)))
    made = 0
    for vid in db.scalars(select(Venue.id)):
        if vid not in have:
            db.add(CashAccount(venue_id=vid, started_at=since or BEGINNING))
            made += 1
    db.flush()
    return made


def _by_venue(rows) -> dict[int, float]:
    return {r[0]: float(r[1] or 0.0) for r in rows}


def balances(db: Session, venue_ids: list[int] | None = None) -> dict[int, dict]:
    ensure_accounts(db)
    pay_q = (select(Payment.venue_id, func.sum(Payment.amount)).join(CashAccount, CashAccount.venue_id == Payment.venue_id)
             .where(Payment.method == "cash", Payment.void.is_(False), Payment.collected_at >= CashAccount.started_at)
             .group_by(Payment.venue_id))
    rent_q = (select(RentalCharge.venue_id, func.sum(RentalCharge.amount))
              .join(CashAccount, CashAccount.venue_id == RentalCharge.venue_id)
              .where(RentalCharge.paid_method == "cash", RentalCharge.paid_at >= CashAccount.started_at)
              .group_by(RentalCharge.venue_id))
    shop_q = (select(StoreSale.venue_id, func.sum(StoreSale.total)).join(CashAccount, CashAccount.venue_id == StoreSale.venue_id)
              .where(StoreSale.payment_method == "cash", StoreSale.status == "confirmed",
                     StoreSale.paid_at >= CashAccount.started_at)
              .group_by(StoreSale.venue_id))
    ref_q = (select(Booking.venue_id, func.sum(Refund.amount)).join(Booking, Booking.id == Refund.booking_id)
             .join(CashAccount, CashAccount.venue_id == Booking.venue_id)
             .where(Booking.payment_method == "cash", Refund.status == "processed",
                    Refund.processed_at >= CashAccount.started_at)
             .group_by(Booking.venue_id))
    entries = db.execute(select(CashEntry.venue_id, CashEntry.kind, func.sum(CashEntry.amount))
                         .group_by(CashEntry.venue_id, CashEntry.kind)).all()
    if venue_ids is not None:
        pay_q, rent_q, shop_q, ref_q = (q.where(col.in_(venue_ids)) for q, col in
                                        ((pay_q, Payment.venue_id), (rent_q, RentalCharge.venue_id),
                                         (shop_q, StoreSale.venue_id), (ref_q, Booking.venue_id)))
    pay, rent, ref = _by_venue(db.execute(pay_q)), _by_venue(db.execute(rent_q)), _by_venue(db.execute(ref_q))
    shop = _by_venue(db.execute(shop_q))
    dep = {v: float(t) for v, k, t in entries if k == "deposit"}
    add = {v: float(t) for v, k, t in entries if k == "non_deposit"}
    out = {}
    for a in db.scalars(select(CashAccount)):
        v = a.venue_id
        if venue_ids is not None and v not in venue_ids:
            continue
        collected = round(pay.get(v, 0.0) + rent.get(v, 0.0) + shop.get(v, 0.0), 2)
        out[v] = {"since": a.started_at if a.started_at > BEGINNING else None, "collected": collected,
                  "refunded": round(ref.get(v, 0.0), 2), "deposited": round(dep.get(v, 0.0), 2),
                  "added": round(add.get(v, 0.0), 2),
                  "balance": round(collected - ref.get(v, 0.0) - dep.get(v, 0.0) + add.get(v, 0.0), 2)}
    return out


def daily(db: Session, venue_id: int, days: int = 14) -> list[dict]:
    """Cash collected and refunded per day for the last ``days`` days, for checking the till."""
    acc = db.get(CashAccount, venue_id)
    start = max(acc.started_at if acc else BEGINNING, datetime.combine(now_local().date() - timedelta(days=days - 1), datetime.min.time()))
    rows = {}
    for d, t in db.execute(select(func.date(Payment.collected_at), func.sum(Payment.amount)).where(
            Payment.venue_id == venue_id, Payment.method == "cash", Payment.void.is_(False),
            Payment.collected_at >= start).group_by(func.date(Payment.collected_at))):
        rows[str(d)] = {"date": str(d), "collected": float(t), "refunded": 0.0}
    for d, t in db.execute(select(func.date(RentalCharge.paid_at), func.sum(RentalCharge.amount)).where(
            RentalCharge.venue_id == venue_id, RentalCharge.paid_method == "cash", RentalCharge.paid_at >= start)
            .group_by(func.date(RentalCharge.paid_at))):
        rows.setdefault(str(d), {"date": str(d), "collected": 0.0, "refunded": 0.0})["collected"] += float(t)
    for d, t in db.execute(select(func.date(StoreSale.paid_at), func.sum(StoreSale.total)).where(
            StoreSale.venue_id == venue_id, StoreSale.payment_method == "cash", StoreSale.status == "confirmed",
            StoreSale.paid_at >= start).group_by(func.date(StoreSale.paid_at))):
        rows.setdefault(str(d), {"date": str(d), "collected": 0.0, "refunded": 0.0})["collected"] += float(t)
    for d, t in db.execute(select(func.date(Refund.processed_at), func.sum(Refund.amount)).join(
            Booking, Booking.id == Refund.booking_id).where(
            Booking.venue_id == venue_id, Booking.payment_method == "cash", Refund.status == "processed",
            Refund.processed_at >= start).group_by(func.date(Refund.processed_at))):
        rows.setdefault(str(d), {"date": str(d), "collected": 0.0, "refunded": 0.0})["refunded"] += float(t)
    return [rows[k] for k in sorted(rows, reverse=True)]


def add_entry(db: Session, actor: User, venue_id: int, kind: str, amount: float, depositor_id: int | None,
              comment: str | None) -> CashEntry:
    if kind not in ("deposit", "non_deposit"):
        raise DomainError("Choose deposit or non-deposit.", 422)
    if not db.get(Venue, venue_id):
        raise DomainError("Venue not found.", 404)
    if not 0 < amount <= 10_000_000:
        raise DomainError("Enter an amount above zero.", 422)
    if depositor_id is None and kind == "deposit":
        raise DomainError("Choose who made the deposit.", 422)
    if depositor_id is not None:
        who = db.get(User, depositor_id)
        if not who or who.role != "vm":
            raise DomainError("The depositor must be one of the Venue Managers.", 422)
    ensure_accounts(db)
    e = CashEntry(venue_id=venue_id, kind=kind, amount=round(amount, 2), depositor_id=depositor_id,
                  comment=(comment or "").strip()[:300] or None, created_by=actor.id)
    db.add(e)
    db.flush()
    audit.record(db, actor, "cash.entry", "venue", venue_id, None,
                 {"kind": kind, "amount": e.amount, "depositor": depositor_id, "comment": e.comment}, venue_id)
    return e
