"""HQ configuration (venues, facilities, hours, special dates, pricing, users), customers, audit & system."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit, serialize
from ..auth import current_user, hash_password, require_hq
from ..db import get_db
from ..models import (AuditEvent, Booking, CommunityGame, Customer, DayClose, Facility, IngestionRun, Notification,
                      Participant, PricingRule, SpecialDate, User, Venue, VmAssignment, WeeklyHours)
from ..services import customers as customer_svc
from ..services import district
from ..timeutil import hhmm
from .common import get_or_404, minutes

router = APIRouter(prefix="/api", tags=["admin"])


# ── Venues & facilities ──────────────────────────────────────────────────────
class VenueIn(BaseModel):
    code: str
    name: str
    location: str = ""
    status: str = "active"


class FacilityIn(BaseModel):
    venue_id: int
    name: str
    facility_type: str = "Court"
    sport: str
    status: str = "active"
    sort_order: int = 0


class HoursIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    open: str = "17:00"
    close: str = "23:00"
    closed: bool = False


class SpecialIn(BaseModel):
    date: date
    open: str = "00:00"
    close: str = "00:00"
    closed: bool = False
    note: str = ""


class PriceIn(BaseModel):
    venue_id: int
    facility_id: int | None = None
    sport: str | None = None
    weekdays: str = "0123456"
    start: str
    end: str
    duration_min: int = 60
    amount: float = Field(ge=0)
    effective_from: date


def _check_hours(o: int, c: int):
    if o % 30 or c % 30 or c <= o or c > 24 * 60:
        raise HTTPException(422, "Hours must be on :00/:30 and close after open (same day).")


@router.post("/venues")
def create_venue(body: VenueIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    v = Venue(code=body.code.strip().upper(), name=body.name.strip(), location=body.location, status=body.status)
    db.add(v)
    db.flush()
    for wd in range(7):
        db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
    audit.record(db, user, "venue.create", "venue", v.id, None, body.model_dump(), v.id)
    db.commit()
    db.refresh(v)
    return serialize.venue(v, full=True)


@router.put("/venues/{venue_id}")
def update_venue(venue_id: int, body: VenueIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    v = get_or_404(db, Venue, venue_id)
    before = serialize.venue(v)
    v.code, v.name, v.location, v.status = body.code.strip().upper(), body.name.strip(), body.location, body.status
    audit.record(db, user, "venue.update", "venue", v.id, before, serialize.venue(v), v.id)
    db.commit()
    return serialize.venue(v, full=True)


@router.put("/venues/{venue_id}/hours")
def set_hours(venue_id: int, body: list[HoursIn], user: User = Depends(require_hq), db: Session = Depends(get_db)):
    v = get_or_404(db, Venue, venue_id)
    for h in body:
        o, c = minutes(h.open), minutes(h.close)
        if not h.closed:
            _check_hours(o, c)
        row = db.get(WeeklyHours, (venue_id, h.weekday)) or WeeklyHours(venue_id=venue_id, weekday=h.weekday)
        before = {"open": row.open_min, "close": row.close_min, "closed": row.closed} if row.open_min is not None else None
        row.open_min, row.close_min, row.closed = o, c, h.closed
        db.add(row)
        audit.record(db, user, "venue.hours", "venue", venue_id, before,
                     {"weekday": h.weekday, "open": o, "close": c, "closed": h.closed}, venue_id)
    db.commit()
    db.refresh(v)
    return serialize.venue(v, full=True)


@router.get("/venues/{venue_id}/special-dates")
def list_special(venue_id: int, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    rows = db.scalars(select(SpecialDate).where(SpecialDate.venue_id == venue_id).order_by(SpecialDate.date.desc()))
    return [{"id": s.id, "date": s.date, "open_min": s.open_min, "close_min": s.close_min, "closed": s.closed,
             "note": s.note} for s in rows]


@router.post("/venues/{venue_id}/special-dates")
def add_special(venue_id: int, body: SpecialIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    get_or_404(db, Venue, venue_id)
    o, c = minutes(body.open), minutes(body.close)
    if not body.closed:
        _check_hours(o, c)
    s = db.scalar(select(SpecialDate).where(SpecialDate.venue_id == venue_id, SpecialDate.date == body.date))
    s = s or SpecialDate(venue_id=venue_id, date=body.date)
    s.open_min, s.close_min, s.closed, s.note = o, c, body.closed, body.note
    db.add(s)
    db.flush()
    audit.record(db, user, "venue.special_date", "venue", venue_id, None, {**body.model_dump()}, venue_id)
    db.commit()
    return {"id": s.id}


@router.delete("/special-dates/{sid}")
def del_special(sid: int, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    s = get_or_404(db, SpecialDate, sid)
    audit.record(db, user, "venue.special_date_delete", "venue", s.venue_id, {"date": s.date}, None, s.venue_id)
    db.delete(s)
    db.commit()
    return {"ok": True}


@router.post("/facilities")
def create_facility(body: FacilityIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    get_or_404(db, Venue, body.venue_id)
    f = Facility(**body.model_dump())
    db.add(f)
    db.flush()
    audit.record(db, user, "facility.create", "facility", f.id, None, body.model_dump(), f.venue_id)
    db.commit()
    return serialize.facility(f)


@router.put("/facilities/{fid}")
def update_facility(fid: int, body: FacilityIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    f = get_or_404(db, Facility, fid)
    before = serialize.facility(f)
    for k, v in body.model_dump().items():
        if k != "venue_id":
            setattr(f, k, v)
    audit.record(db, user, "facility.update", "facility", f.id, before, serialize.facility(f), f.venue_id)
    db.commit()
    return serialize.facility(f)


@router.get("/pricing")
def list_pricing(venue_id: int | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    q = select(PricingRule).order_by(PricingRule.venue_id, PricingRule.effective_from.desc(), PricingRule.start_min)
    if venue_id:
        q = q.where(PricingRule.venue_id == venue_id)
    return [{"id": r.id, "venue_id": r.venue_id, "facility_id": r.facility_id, "sport": r.sport,
             "weekdays": r.weekdays, "start": hhmm(r.start_min), "end": hhmm(r.end_min) if r.end_min < 1440 else "24:00",
             "duration_min": r.duration_min, "amount": r.amount, "effective_from": r.effective_from,
             "created_at": r.created_at} for r in db.scalars(q)]


@router.post("/pricing")
def add_pricing(body: PriceIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    """New rules never alter existing bookings; set effective_from to schedule a change."""
    if body.duration_min not in (30, 60):
        raise HTTPException(422, "Duration must be 30 or 60 minutes.")
    s, e = minutes(body.start), minutes(body.end)
    if e <= s:
        raise HTTPException(422, "End must be after start.")
    r = PricingRule(venue_id=body.venue_id, facility_id=body.facility_id, sport=body.sport or None,
                    weekdays=body.weekdays, start_min=s, end_min=e, duration_min=body.duration_min,
                    amount=body.amount, effective_from=body.effective_from)
    db.add(r)
    db.flush()
    audit.record(db, user, "pricing.create", "pricing_rule", r.id, None, body.model_dump(), body.venue_id)
    db.commit()
    return {"id": r.id}


# ── Users & VM assignment ────────────────────────────────────────────────────
class UserIn(BaseModel):
    name: str
    email: str
    role: str
    password: str | None = None
    venue_ids: list[int] = []
    active: bool = True


@router.get("/users")
def list_users(user: User = Depends(require_hq), db: Session = Depends(get_db)):
    return [serialize.user(u) for u in db.scalars(select(User).order_by(User.role, User.name))]


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    if body.role not in ("hq", "vm"):
        raise HTTPException(422, "Role must be hq or vm")
    if not body.password or len(body.password) < 8:
        raise HTTPException(422, "Password must be at least 8 characters")
    if db.scalar(select(User).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "Email already in use")
    u = User(name=body.name, email=body.email.lower(), role=body.role, password_hash=hash_password(body.password))
    u.venues = [VmAssignment(venue_id=v) for v in body.venue_ids] if body.role == "vm" else []
    db.add(u)
    db.flush()
    audit.record(db, user, "user.create", "user", u.id, None, {"email": u.email, "role": u.role, "venues": body.venue_ids})
    db.commit()
    return serialize.user(u)


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    u = get_or_404(db, User, uid)
    before = serialize.user(u)
    u.name, u.role, u.active = body.name, body.role, body.active
    if body.password:
        if len(body.password) < 8:
            raise HTTPException(422, "Password must be at least 8 characters")
        u.password_hash = hash_password(body.password)
    u.venues = [VmAssignment(user_id=u.id, venue_id=v) for v in body.venue_ids] if body.role == "vm" else []
    audit.record(db, user, "user.update", "user", u.id, before, serialize.user(u))
    db.commit()
    return serialize.user(u)


# ── Customers ────────────────────────────────────────────────────────────────
@router.get("/customers/lookup")
def lookup(phone: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Used by VM forms to load an existing profile by phone."""
    try:
        c = customer_svc.find_by_phone(db, phone)
    except Exception:  # noqa: BLE001 — partial numbers while typing
        return {"found": False}
    return {"found": bool(c), "customer": serialize.customer(c) if c else None}


@router.get("/customers")
def list_customers(q: str | None = None, limit: int = 100, user: User = Depends(require_hq),
                   db: Session = Depends(get_db)):
    query = select(Customer)
    if q:
        query = query.where(or_(Customer.phone.contains(q), Customer.name.ilike(f"%{q}%")))
    rows = list(db.scalars(query.order_by(Customer.created_at.desc()).limit(min(limit, 500))))
    ids = [c.id for c in rows]
    counts: dict = {}
    for cid, n, last in db.execute(select(Booking.customer_id, func.count(), func.max(Booking.date)).where(
            Booking.customer_id.in_(ids), Booking.status == "confirmed", Booking.attendance == "attended")
            .group_by(Booking.customer_id)):
        counts[cid] = [n, last]
    for cid, n, last in db.execute(select(Participant.customer_id, func.count(), func.max(CommunityGame.date)).join(
            CommunityGame, CommunityGame.id == Participant.game_id).where(
            Participant.customer_id.in_(ids), Participant.attendance == "attended").group_by(Participant.customer_id)):
        prev = counts.get(cid, [0, None])
        counts[cid] = [prev[0] + n, max(filter(None, [prev[1], last]))]
    return [{**serialize.customer(c), "interactions": counts.get(c.id, [0, None])[0],
             "last_seen": counts.get(c.id, [0, None])[1], "returning": counts.get(c.id, [0])[0] >= 2} for c in rows]


@router.get("/customers/{cid}")
def customer_detail(cid: int, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    c = get_or_404(db, Customer, cid)
    history = []
    for b in db.scalars(select(Booking).where(Booking.customer_id == cid).order_by(Booking.date.desc())):
        v = db.get(Venue, b.venue_id)
        history.append({"kind": b.source, "id": b.id, "date": b.date, "time": f"{hhmm(b.start_min)}–{hhmm(b.end_min)}",
                        "venue": v.name, "amount": b.booking_value, "status": b.status, "attendance": b.attendance,
                        "payment_status": b.payment_status,
                        "counts": b.status == "confirmed" and b.attendance == "attended"})
    for p in db.scalars(select(Participant).where(Participant.customer_id == cid)):
        g = p.game
        v = db.get(Venue, g.venue_id)
        history.append({"kind": "community", "id": g.id, "date": g.date, "time": f"{hhmm(g.start_min)}–{hhmm(g.end_min)}",
                        "venue": v.name, "amount": p.amount, "status": g.status, "attendance": p.attendance,
                        "payment_status": p.payment_status, "level": p.level,
                        "counts": g.status != "cancelled" and p.attendance == "attended"})
    history.sort(key=lambda h: h["date"], reverse=True)
    completed = sum(1 for h in history if h["counts"])
    return {**serialize.customer(c), "history": history, "completed": completed, "returning": completed >= 2,
            "net_spend": sum(h["amount"] for h in history if h["counts"])}


# ── Audit & system ───────────────────────────────────────────────────────────
@router.get("/audit")
def audit_log(entity: str | None = None, entity_id: int | None = None, venue_id: int | None = None,
              action: str | None = None, limit: int = 200, user: User = Depends(require_hq),
              db: Session = Depends(get_db)):
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if entity:
        q = q.where(AuditEvent.entity == entity)
    if entity_id:
        q = q.where(AuditEvent.entity_id == entity_id)
    if venue_id:
        q = q.where(AuditEvent.venue_id == venue_id)
    if action:
        q = q.where(AuditEvent.action.contains(action))
    return [{"id": a.id, "actor": a.actor_name, "venue_id": a.venue_id, "entity": a.entity, "entity_id": a.entity_id,
             "action": a.action, "before": a.before, "after": a.after, "at": a.at}
            for a in db.scalars(q.limit(min(limit, 1000)))]


@router.get("/system/notifications")
def notifications(channel: str | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    q = select(Notification).order_by(Notification.id.desc())
    if channel:
        q = q.where(Notification.channel == channel)
    return [{"id": n.id, "channel": n.channel, "recipient": n.recipient, "template": n.template, "body": n.body,
             "status": n.status, "error": n.error, "ref_type": n.ref_type, "ref_id": n.ref_id, "at": n.created_at}
            for n in db.scalars(q.limit(200))]


@router.get("/system/ingestion")
def ingestion_runs(user: User = Depends(require_hq), db: Session = Depends(get_db)):
    return [{"id": r.id, "source": r.source, "started_at": r.started_at, "finished_at": r.finished_at,
             "messages": r.messages, "created": r.created, "duplicates": r.duplicates, "errors": r.errors}
            for r in db.scalars(select(IngestionRun).order_by(IngestionRun.id.desc()).limit(50))]


@router.post("/system/district/run")
def run_district(user: User = Depends(require_hq), db: Session = Depends(get_db)):
    r = district.run_ingestion(db)
    return {"id": r.id, "messages": r.messages, "created": r.created, "duplicates": r.duplicates, "errors": r.errors}


class RawEmail(BaseModel):
    raw: str


@router.post("/system/district/simulate")
def simulate_district(body: RawEmail, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    """Feed one District email through the real parser + idempotent ingestion."""
    try:
        result, bid = district.ingest_raw(db, body.raw)
    except district.ParseError as e:
        raise HTTPException(422, f"Parse failure: {e}") from None
    db.commit()
    return {"result": result, "booking_id": bid}


@router.get("/system/day-closes")
def day_closes(user: User = Depends(require_hq), db: Session = Depends(get_db)):
    return [{"id": d.id, "venue_id": d.venue_id, "date": d.date, "status": d.status, "closed_at": d.closed_at,
             "missing_items": d.missing_items, "escalated_at": d.escalated_at}
            for d in db.scalars(select(DayClose).order_by(DayClose.date.desc()).limit(200))]
