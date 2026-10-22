"""Response shaping. Kept separate so routers stay thin."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import (AcademyOccurrence, AcademySeries, Allocation, Booking, CommunityGame, CorporateEvent, Customer,
                     Facility, OperationalBlock, Refund, User, Venue)
from .services import bookings as booking_svc
from .services.community import game_state, participant_dict
from .services.inventory import facilities_of
from .timeutil import hhmm, minute_of, now_local


def facility(f: Facility) -> dict:
    return {"id": f.id, "venue_id": f.venue_id, "name": f.name, "facility_type": f.facility_type, "sport": f.sport,
            "status": f.status, "sort_order": f.sort_order}


def venue(v: Venue, full: bool = False) -> dict:
    out = {"id": v.id, "code": v.code, "name": v.name, "location": v.location, "status": v.status}
    if full:
        out["facilities"] = [facility(f) for f in v.facilities]
        out["weekly_hours"] = [{"weekday": w.weekday, "open_min": w.open_min, "close_min": w.close_min,
                                "closed": w.closed} for w in v.weekly_hours]
    return out


def user(u: User) -> dict:
    from .permissions import ROLES
    status = "disabled" if not u.active else "invited" if not u.password_hash else "active"
    return {"id": u.id, "name": u.name, "email": u.email, "role": u.role, "role_label": ROLES.get(u.role, u.role),
            "active": u.active, "status": status, "venue_ids": [a.venue_id for a in u.venues],
            "last_login_at": u.last_login_at, "created_at": u.created_at}


def customer(c: Customer) -> dict:
    return {"id": c.id, "phone": c.phone, "name": c.name, "name_locked": c.name_locked, "created_at": c.created_at}


def _fac_name(db: Session, fid: int | None, names: dict | None = None) -> str | None:
    if not fid:
        return None
    if names is not None:
        return names.get(fid)
    f = db.get(Facility, fid)
    return f.name if f else None


def booking_lookups(db: Session, rows: list[Booking]) -> dict:
    """Customers, court names and refund totals for a whole list of bookings in three queries,
    so serialising a day's bookings doesn't cost several queries each."""
    cids = {b.customer_id for b in rows}
    fids = {x for b in rows for x in (b.facility_id, b.original_facility_id) if x}
    ids = [b.id for b in rows]
    return {
        "customers": {c.id: c for c in db.scalars(select(Customer).where(Customer.id.in_(cids)))} if cids else {},
        "facilities": dict(db.execute(select(Facility.id, Facility.name).where(Facility.id.in_(fids))).all()) if fids else {},
        "refunded": dict(db.execute(select(Refund.booking_id, func.sum(Refund.amount)).where(
            Refund.booking_id.in_(ids), Refund.status == "processed").group_by(Refund.booking_id)).all()) if ids else {},
    }


def bookings(db: Session, rows, detail: bool = False) -> list[dict]:
    rows = list(rows)
    pre = booking_lookups(db, rows)
    return [booking(db, b, detail, pre) for b in rows]


def booking(db: Session, b: Booking, detail: bool = False, pre: dict | None = None) -> dict:
    c = pre["customers"].get(b.customer_id) if pre else db.get(Customer, b.customer_id)
    names = pre["facilities"] if pre else None
    now = now_local()
    now_min = minute_of(now, b.date)
    out = {
        "id": b.id, "kind": b.source, "source": b.source, "external_ref": b.external_ref,
        "customer": customer(c), "venue_id": b.venue_id,
        "facility_id": b.facility_id, "facility": _fac_name(db, b.facility_id, names),
        "original_facility_id": b.original_facility_id, "original_facility": _fac_name(db, b.original_facility_id, names),
        "sport": b.sport, "date": b.date, "start_min": b.start_min, "end_min": b.end_min,
        "time": f"{hhmm(b.start_min)}–{hhmm(b.end_min)}", "booking_value": b.booking_value,
        "collected_amount": b.collected_amount, "payment_status": b.payment_status, "payment_method": b.payment_method,
        "payment_window_start_min": b.start_min - 30, "payment_window_end_min": b.payment_window_end_min,
        "attendance": b.attendance, "status": b.status, "cancel_reason": b.cancel_reason, "cancel_note": b.cancel_note,
        "created_at": b.created_at, "assigned_at": b.assigned_at,
        "can_no_show": b.status == "confirmed" and b.attendance == "pending" and now_min >= b.start_min + 30,
        "no_show_from_min": b.start_min + 30,
        "refunded": float(pre["refunded"].get(b.id) or 0.0) if pre else booking_svc.refunded_total(db, b.id),
    }
    if detail:
        out["assignments"] = booking_svc.assignment_history(db, b)
        from .models import Refund
        out["refunds"] = [{"id": r.id, "amount": r.amount, "reason": r.reason, "status": r.status,
                           "processed_by": r.processed_by, "processed_at": r.processed_at}
                          for r in db.scalars(select(Refund).where(Refund.booking_id == b.id))]
    return out


def game(db: Session, g: CommunityGame, detail: bool = False) -> dict:
    fids = facilities_of(db, "community", g.id)
    if not fids and g.status == "cancelled":
        fids = list({a.facility_id for a in db.scalars(select(Allocation).where(
            Allocation.kind == "community", Allocation.ref_id == g.id))})
    parts = g.participants
    out = {
        "id": g.id, "kind": "community", "venue_id": g.venue_id, "title": g.title, "sport": g.sport, "date": g.date,
        "start_min": g.start_min, "end_min": g.end_min, "time": f"{hhmm(g.start_min)}–{hhmm(g.end_min)}",
        "facility_ids": fids, "facilities": [_fac_name(db, f) for f in fids], "capacity": g.capacity,
        "per_person": g.per_person, "community_link": g.community_link, "status": g.status,
        "state": game_state(g), "cancel_reason": g.cancel_reason, "cancel_note": g.cancel_note,
        "participant_count": len(parts), "attended": sum(1 for p in parts if p.attendance == "attended"),
        "paid": sum(1 for p in parts if p.payment_status == "paid"),
        "collected": sum(p.amount for p in parts if p.payment_status == "paid"),
        "to_collect": sum(1 for p in parts if p.attendance == "attended" and p.payment_status != "paid"),
        "unmarked": sum(1 for p in parts if p.attendance == "added"),
        "owed": sum(1 for p in parts if p.attendance != "no_show" and p.payment_status != "paid"),
    }
    if detail:
        out["participants"] = [participant_dict(db, p) for p in parts]
    return out


def occurrence(db: Session, o: AcademyOccurrence) -> dict:
    s = o.series
    fids = facilities_of(db, "academy", o.id) or s.facility_ids
    return {"id": o.id, "kind": "academy", "series_id": s.id, "name": s.name, "venue_id": o.venue_id, "date": o.date,
            "start_min": o.start_min, "end_min": o.end_min, "time": f"{hhmm(o.start_min)}–{hhmm(o.end_min)}",
            "facility_ids": fids, "facilities": [_fac_name(db, f) for f in fids], "value": o.value,
            "status": o.status, "note": o.note}


def series(db: Session, s: AcademySeries, detail: bool = False) -> dict:
    occ = s.occurrences
    out = {"id": s.id, "venue_id": s.venue_id, "name": s.name, "facility_ids": s.facility_ids,
           "facilities": [_fac_name(db, f) for f in s.facility_ids], "recurring": s.recurring,
           "weekdays": s.weekdays, "start_date": s.start_date, "end_date": s.end_date, "start_min": s.start_min,
           "end_min": s.end_min, "time": f"{hhmm(s.start_min)}–{hhmm(s.end_min)}",
           "value_per_occurrence": s.value_per_occurrence, "status": s.status,
           "scheduled": sum(1 for o in occ if o.status == "scheduled"),
           "skipped": sum(1 for o in occ if o.status == "skipped"),
           "cancelled": sum(1 for o in occ if o.status == "cancelled")}
    if detail:
        from .models import AcademyReconciliation
        out["occurrences"] = [occurrence(db, o) for o in occ]
        out["reconciliations"] = [{"month": r.month, "amount_collected": r.amount_collected,
                                   "payment_method": r.payment_method, "recorded_at": r.recorded_at}
                                  for r in db.scalars(select(AcademyReconciliation).where(
                                      AcademyReconciliation.series_id == s.id).order_by(AcademyReconciliation.month))]
    return out


def event(db: Session, e: CorporateEvent) -> dict:
    fids = facilities_of(db, "corporate", e.id)
    return {"id": e.id, "kind": "corporate", "venue_id": e.venue_id, "company": e.company,
            "contact_name": e.contact_name, "contact_phone": e.contact_phone, "date": e.date,
            "start_min": e.start_min, "end_min": e.end_min, "time": f"{hhmm(e.start_min)}–{hhmm(e.end_min)}",
            "facility_ids": fids, "facilities": [_fac_name(db, f) for f in fids], "amount": e.amount,
            "payment_status": e.payment_status, "payment_method": e.payment_method, "status": e.status,
            "cancel_reason": e.cancel_reason}


def block(db: Session, b: OperationalBlock) -> dict:
    return {"id": b.id, "kind": "block", "venue_id": b.venue_id, "facility_id": b.facility_id,
            "facility": _fac_name(db, b.facility_id), "date": b.date, "start_min": b.start_min, "end_min": b.end_min,
            "time": f"{hhmm(b.start_min)}–{hhmm(b.end_min)}", "reason": b.reason, "note": b.note,
            "created_by": b.created_by, "created_at": b.created_at, "released_at": b.released_at,
            "active": b.released_at is None and b.end_min > b.start_min}
