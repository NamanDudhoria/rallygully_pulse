"""Academy (one-time & recurring), Corporate/Private events and Operational Blocks."""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import DomainError
from ..models import (AcademyOccurrence, AcademyReconciliation, AcademySeries, AllocationKind, CorporateEvent,
                      Facility, OperationalBlock, Payment, User)
from ..timeutil import ceil_slot, minute_of, now_local, today
from . import inventory
from .bookings import PAYMENT_METHODS

BLOCK_REASONS = {
    "maintenance": "Maintenance",
    "facility_problem": "Facility problem",
    "operational_closure": "Operational closure",
    "weather": "Weather",
    "other": "Other",
}

MAX_OCCURRENCES = 400


# ── Academy ──────────────────────────────────────────────────────────────────
def create_academy(db: Session, actor: User, *, venue_id: int, name: str, facility_ids: list[int], recurring: bool,
                   weekdays: str, start_date: date, end_date: date | None, start_min: int, end_min: int,
                   value_per_occurrence: float) -> tuple[AcademySeries, list[dict]]:
    """Materializes every occurrence as its own inventory allocation (PRD §33.2).

    Occurrences that clash or fall on closed days are skipped and reported, not silently dropped.
    """
    if not name.strip():
        raise DomainError("Name is required.", 422)
    end_date = end_date if recurring else start_date
    if end_date < start_date:
        raise DomainError("End date must be on/after start date.", 422)
    if recurring and not weekdays:
        raise DomainError("Choose at least one weekday.", 422)
    s = AcademySeries(venue_id=venue_id, name=name.strip(), facility_ids=facility_ids, recurring=recurring,
                      weekdays=weekdays if recurring else str(start_date.weekday()), start_date=start_date,
                      end_date=end_date, start_min=start_min, end_min=end_min,
                      value_per_occurrence=value_per_occurrence)
    db.add(s)
    db.flush()
    skipped, made = [], 0
    d = start_date
    while d <= end_date:
        if str(d.weekday()) in s.weekdays:
            if made >= MAX_OCCURRENCES:
                raise DomainError(f"Too many occurrences (max {MAX_OCCURRENCES}). Shorten the date range.", 422)
            occ = AcademyOccurrence(series_id=s.id, venue_id=venue_id, date=d, start_min=start_min,
                                    end_min=end_min, value=value_per_occurrence)
            db.add(occ)
            db.flush()
            try:
                with db.begin_nested():
                    inventory.allocate(db, venue_id, facility_ids, d, start_min, end_min, AllocationKind.ACADEMY, occ.id)
                made += 1
            except DomainError as e:
                if not recurring:
                    raise
                occ.status, occ.note = "skipped", e.message
                skipped.append({"date": d.isoformat(), "reason": e.message})
        d += timedelta(days=1)
    if made == 0:
        raise DomainError("No occurrence could be scheduled: " + (skipped[0]["reason"] if skipped else "no matching dates"))
    audit.record(db, actor, "academy.create", "academy_series", s.id, None,
                 {"name": s.name, "facilities": facility_ids, "occurrences": made, "skipped": len(skipped)}, venue_id)
    return s, skipped


def cancel_occurrence(db: Session, actor: User, occ: AcademyOccurrence, note: str | None) -> AcademyOccurrence:
    if occ.status != "scheduled":
        raise DomainError("Occurrence is not active.")
    occ.status, occ.note = "cancelled", note
    inventory.release(db, AllocationKind.ACADEMY, occ.id)
    audit.record(db, actor, "academy.cancel_occurrence", "academy_occurrence", occ.id,
                 {"status": "scheduled"}, {"status": "cancelled", "note": note}, occ.venue_id)
    return occ


def end_series(db: Session, actor: User, s: AcademySeries, from_date: date) -> int:
    n = 0
    for occ in s.occurrences:
        if occ.date >= from_date and occ.status == "scheduled":
            occ.status, occ.note = "cancelled", "Series ended"
            inventory.release(db, AllocationKind.ACADEMY, occ.id)
            n += 1
    s.end_date = min(s.end_date, from_date - timedelta(days=1)) if from_date > s.start_date else s.start_date
    s.status = "ended"
    audit.record(db, actor, "academy.end_series", "academy_series", s.id, None, {"from": from_date, "cancelled": n}, s.venue_id)
    return n


def reconcile_month(db: Session, actor: User, s: AcademySeries, month: str, amount: float, method: str) -> AcademyReconciliation:
    if method not in PAYMENT_METHODS:
        raise DomainError("Choose a payment method.", 422)
    rec = db.scalar(select(AcademyReconciliation).where(AcademyReconciliation.series_id == s.id,
                                                         AcademyReconciliation.month == month))
    before = {"amount": rec.amount_collected} if rec else None
    if rec:
        rec.amount_collected, rec.payment_method = amount, method
    else:
        rec = AcademyReconciliation(series_id=s.id, month=month, amount_collected=amount, payment_method=method)
        db.add(rec)
    audit.record(db, actor, "academy.reconcile", "academy_series", s.id, before, {"month": month, "amount": amount}, s.venue_id)
    return rec


# ── Corporate / Private events ───────────────────────────────────────────────
def create_event(db: Session, actor: User, *, venue_id: int, company: str, contact_name: str, contact_phone: str,
                 facility_ids: list[int], d: date, start_min: int, end_min: int, amount: float, paid: bool,
                 method: str | None) -> CorporateEvent:
    from .customers import normalize_phone
    if not company.strip() or not contact_name.strip():
        raise DomainError("Company and contact person are required.", 422)
    if paid and method not in PAYMENT_METHODS:
        raise DomainError("Choose a payment method.", 422)
    e = CorporateEvent(venue_id=venue_id, company=company.strip(), contact_name=contact_name.strip(),
                       contact_phone=normalize_phone(contact_phone), date=d, start_min=start_min, end_min=end_min,
                       amount=amount, payment_status="paid" if paid else "to_collect",
                       payment_method=method if paid else None)
    db.add(e)
    db.flush()
    inventory.allocate(db, venue_id, facility_ids, d, start_min, end_min, AllocationKind.CORPORATE, e.id)
    if paid:
        db.add(Payment(source_type="event", source_id=e.id, venue_id=venue_id, activity_date=d, amount=amount,
                       method=method, collected_at=now_local(), actor_id=actor.id))
    audit.record(db, actor, "event.create", "corporate_event", e.id, None,
                 {"company": e.company, "amount": amount, "facilities": facility_ids, "paid": paid}, venue_id)
    return e


def event_mark_paid(db: Session, actor: User, e: CorporateEvent, method: str) -> CorporateEvent:
    if e.payment_status == "paid":
        raise DomainError("Already paid.")
    if method not in PAYMENT_METHODS:
        raise DomainError("Choose a payment method.", 422)
    e.payment_status, e.payment_method = "paid", method
    db.add(Payment(source_type="event", source_id=e.id, venue_id=e.venue_id, activity_date=e.date, amount=e.amount,
                   method=method, collected_at=now_local(), actor_id=actor.id))
    audit.record(db, actor, "payment.record", "corporate_event", e.id, {"payment_status": "to_collect"},
                 {"payment_status": "paid", "method": method}, e.venue_id)
    return e


def cancel_event(db: Session, actor: User, e: CorporateEvent, reason: str) -> CorporateEvent:
    if e.status == "cancelled":
        raise DomainError("Already cancelled.")
    if not reason.strip():
        raise DomainError("Reason is required.", 422)
    e.status, e.cancel_reason = "cancelled", reason.strip()[:40]
    inventory.release(db, AllocationKind.CORPORATE, e.id)
    audit.record(db, actor, "event.cancel", "corporate_event", e.id, {"status": "confirmed"},
                 {"status": "cancelled", "reason": reason}, e.venue_id)
    return e


# ── Operational blocks ───────────────────────────────────────────────────────
def create_block(db: Session, actor: User, *, venue_id: int, facility_id: int, d: date, start_min: int,
                 end_min: int, reason: str, note: str | None) -> OperationalBlock:
    if reason not in BLOCK_REASONS:
        raise DomainError("Choose a block reason.", 422)
    if reason == "other" and not (note or "").strip():
        raise DomainError("Add a note for 'Other'.", 422)
    if actor.role != "hq" and d < today():
        raise DomainError("Venue Managers can only block today or later.")
    blk = OperationalBlock(venue_id=venue_id, facility_id=facility_id, date=d, start_min=start_min, end_min=end_min,
                           reason=reason, note=(note or "").strip() or None, created_by=actor.id)
    db.add(blk)
    db.flush()
    # Blocks reflect physical reality (a broken court), so the District-demand guard does not stop them.
    inventory.allocate(db, venue_id, [facility_id], d, start_min, end_min, AllocationKind.BLOCK, blk.id,
                       district_guard=False)
    audit.record(db, actor, "block.create", "operational_block", blk.id, None,
                 {"facility_id": facility_id, "date": d, "start": start_min, "end": end_min, "reason": reason}, venue_id)
    return blk


def release_block(db: Session, actor: User, blk: OperationalBlock) -> OperationalBlock:
    """Release early: the block keeps the time already elapsed (rounded up to the slot)."""
    if blk.released_at:
        raise DomainError("Block already released.")
    now = now_local()
    cut = ceil_slot(minute_of(now, blk.date))
    before_end = blk.end_min
    if blk.date < now.date() or cut >= blk.end_min:
        raise DomainError("Block has already ended.")
    if cut > blk.start_min:
        # Elapsed portion stays allocated (it really was blocked); the rest frees up.
        blk.end_min = cut
        for r in inventory.venue_allocations(db, blk.venue_id, blk.date):
            if r.kind == AllocationKind.BLOCK and r.ref_id == blk.id:
                r.end_min = cut
    else:
        inventory.release(db, AllocationKind.BLOCK, blk.id)
        blk.end_min = blk.start_min  # never took effect
    blk.released_at, blk.released_by = now, actor.id
    audit.record(db, actor, "block.release", "operational_block", blk.id, {"end": before_end},
                 {"end": blk.end_min, "released_at": now}, blk.venue_id)
    return blk


def facility_names(db: Session, ids: list[int]) -> list[str]:
    return [db.get(Facility, i).name for i in ids if db.get(Facility, i)]
