"""Cash held by each Venue Manager.

A VM's cash is what they collected in cash at the venues they work (bookings, rentals, RG Store, Community and the
rest: whatever is recorded against their name), less what they deposited in the bank or spent, plus any adjustment HQ
makes. Collections are read from the payment records, so a corrected or voided payment corrects the VM's cash too.

A VM deposits (a photo of the bank receipt is required) or records an expense (a message is required, a photo is
optional). Each of those is also taken off the venue's own cash balance (see services/cash.py) and emailed straight
away to PULSE_CASH_EMAIL with the photo attached.
"""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import CashAccount, CashEntry, Payment, RentalCharge, StoreSale, User, Venue, VmCashEntry
from ..timeutil import now_local, tz_label
from . import cash, notify
from .attendance import _decode_photo


def _group(rows) -> dict[tuple[int, int], float]:
    return {(r[0], r[1]): float(r[2] or 0.0) for r in rows}


def holdings(db: Session, user_ids: list[int] | None = None) -> dict[int, dict]:
    """{user_id: {"venues": {venue_id: balance}, "total", "collected", "deposited", "expenses", "adjusted"}} for VMs."""
    cash.ensure_accounts(db)
    vms = [u for u in db.scalars(select(User).where(User.role == "vm")) if user_ids is None or u.id in user_ids]
    ids = [u.id for u in vms]
    if not ids:
        return {}
    pay = _group(db.execute(select(Payment.actor_id, Payment.venue_id, func.sum(Payment.amount))
                            .join(CashAccount, CashAccount.venue_id == Payment.venue_id)
                            .where(Payment.method == "cash", Payment.void.is_(False), Payment.actor_id.in_(ids),
                                   Payment.collected_at >= CashAccount.started_at)
                            .group_by(Payment.actor_id, Payment.venue_id)))
    rent = _group(db.execute(select(RentalCharge.paid_by, RentalCharge.venue_id, func.sum(RentalCharge.amount))
                             .join(CashAccount, CashAccount.venue_id == RentalCharge.venue_id)
                             .where(RentalCharge.paid_method == "cash", RentalCharge.paid_by.in_(ids),
                                    RentalCharge.paid_at >= CashAccount.started_at)
                             .group_by(RentalCharge.paid_by, RentalCharge.venue_id)))
    shop = _group(db.execute(select(StoreSale.paid_by, StoreSale.venue_id, func.sum(StoreSale.total))
                             .join(CashAccount, CashAccount.venue_id == StoreSale.venue_id)
                             .where(StoreSale.payment_method == "cash", StoreSale.status == "confirmed",
                                    StoreSale.paid_by.in_(ids), StoreSale.paid_at >= CashAccount.started_at)
                             .group_by(StoreSale.paid_by, StoreSale.venue_id)))
    own = {}
    for uid, vid, kind, total in db.execute(select(VmCashEntry.user_id, VmCashEntry.venue_id, VmCashEntry.kind,
                                                    func.sum(VmCashEntry.amount))
                                            .where(VmCashEntry.user_id.in_(ids))
                                            .group_by(VmCashEntry.user_id, VmCashEntry.venue_id, VmCashEntry.kind)):
        own[(uid, vid, kind)] = float(total or 0.0)
    # a deposit HQ recorded in the venue's cash screen, naming the VM, comes off that VM's cash as well
    hq_dep = _group(db.execute(select(CashEntry.depositor_id, CashEntry.venue_id, func.sum(CashEntry.amount))
                               .where(CashEntry.kind == "deposit", CashEntry.depositor_id.in_(ids))
                               .group_by(CashEntry.depositor_id, CashEntry.venue_id)))
    out = {}
    for u in vms:
        venues_ = {vid for (uid, vid) in list(pay) + list(rent) + list(shop) + list(hq_dep) if uid == u.id}
        venues_ |= {vid for (uid, vid, _k) in own if uid == u.id}
        per, tot = {}, {"collected": 0.0, "deposited": 0.0, "expenses": 0.0, "adjusted": 0.0}
        for vid in venues_:
            k = (u.id, vid)
            collected = pay.get(k, 0.0) + rent.get(k, 0.0) + shop.get(k, 0.0)
            deposited = own.get((u.id, vid, "deposit"), 0.0) + hq_dep.get(k, 0.0)
            expenses, adjusted = own.get((u.id, vid, "expense"), 0.0), own.get((u.id, vid, "adjust"), 0.0)
            per[vid] = round(collected - deposited - expenses + adjusted, 2)
            for key, val in (("collected", collected), ("deposited", deposited), ("expenses", expenses), ("adjusted", adjusted)):
                tot[key] += val
        out[u.id] = {"venues": per, "total": round(sum(per.values()), 2), **{k: round(v, 2) for k, v in tot.items()}}
    return out


def balance_at(db: Session, user_id: int, venue_id: int) -> float:
    return holdings(db, [user_id]).get(user_id, {"venues": {}})["venues"].get(venue_id, 0.0)


def _email(db: Session, e: VmCashEntry, vm: User, v: Venue, left: float) -> None:
    what = "deposit" if e.kind == "deposit" else "expense"
    venue = v.name.removeprefix("RallyGully ").strip()
    when = e.created_at.strftime("%d %b %Y, %I:%M %p").replace(" 0", " ") + f" {tz_label()}"
    body = (f"{vm.name} recorded a cash {what} of ₹{e.amount:,.2f} from {venue}.\n\n"
            f"When: {when}\nPaid in at / spent on: {e.message or '—'}\n"
            f"Cash still with {vm.name}: ₹{left:,.2f} (across all venues)\n"
            + ("\nThe bank receipt is attached." if e.photo else "\nNo photo was attached.") + "\n")
    n = notify.email(db, config.CASH_EMAIL, f"[Pulse] Cash {what} ₹{e.amount:,.0f} by {vm.name} · {venue}", body,
                     ref_type="vm_cash", ref_id=e.id, template="vm_cash",
                     attachments=[(f"cash-{what}-{e.id}.jpg", e.photo, "image", "jpeg")] if e.photo else None)
    e.email_status = n.status


def record(db: Session, actor: User, vm: User, venue_id: int, kind: str, amount: float, message: str | None,
           photo: str | None) -> VmCashEntry:
    if kind not in ("deposit", "expense"):
        raise DomainError("Choose deposit or expense.", 422)
    if vm.role != "vm":
        raise DomainError("Only Venue Managers hold cash.", 403)
    if not db.get(Venue, venue_id):
        raise DomainError("Venue not found.", 404)
    if not 0 < amount <= 10_000_000:
        raise DomainError("Enter an amount above zero.", 422)
    message = (message or "").strip()[:300]
    if kind == "expense" and not message:
        raise DomainError("Say what the money was spent on.", 422)
    if kind == "deposit" and not photo:
        raise DomainError("Take a photo of the bank receipt to record a deposit.", 422)
    held = balance_at(db, vm.id, venue_id)
    if amount > held + 0.001:
        raise DomainError(f"You hold ₹{max(held, 0):,.0f} from this venue: you can't take out ₹{amount:,.0f}.", 409)
    e = VmCashEntry(user_id=vm.id, venue_id=venue_id, kind=kind, amount=round(amount, 2), message=message or None,
                    photo=_decode_photo(photo) if photo else None, created_by=actor.id, created_at=now_local())
    db.add(e)
    db.flush()
    audit.record(db, actor, f"vmcash.{kind}", "user", vm.id, None,
                 {"venue_id": venue_id, "amount": e.amount, "message": message, "receipt": bool(e.photo)}, venue_id)
    _email(db, e, vm, db.get(Venue, venue_id), holdings(db, [vm.id])[vm.id]["total"])
    return e


def adjust(db: Session, actor: User, vm: User, venue_id: int, amount: float, message: str) -> VmCashEntry:
    """HQ puts cash on a VM's account (a positive amount, for example an opening balance) or takes it off (negative)."""
    if vm.role != "vm":
        raise DomainError("Only Venue Managers hold cash.", 403)
    if not db.get(Venue, venue_id):
        raise DomainError("Venue not found.", 404)
    if not amount or abs(amount) > 10_000_000:
        raise DomainError("Enter an amount (use a minus sign to take cash off).", 422)
    if not (message or "").strip():
        raise DomainError("Give a reason for the adjustment.", 422)
    e = VmCashEntry(user_id=vm.id, venue_id=venue_id, kind="adjust", amount=round(amount, 2), message=message.strip()[:300],
                    created_by=actor.id, created_at=now_local())
    db.add(e)
    db.flush()
    audit.record(db, actor, "vmcash.adjust", "user", vm.id, None, {"venue_id": venue_id, "amount": e.amount, "message": e.message}, venue_id)
    return e


def entry_dict(e: VmCashEntry, names: dict, venues: dict) -> dict:
    return {"id": e.id, "kind": e.kind, "amount": e.amount, "venue_id": e.venue_id, "venue": venues.get(e.venue_id),
            "message": e.message, "has_photo": e.photo is not None, "by": names.get(e.created_by),
            "email": e.email_status, "created_at": e.created_at}
