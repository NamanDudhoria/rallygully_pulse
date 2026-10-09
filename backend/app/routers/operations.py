"""Bookings, Community Games, Academy, Corporate events and Operational Blocks."""
from datetime import date

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import serialize
from ..auth import current_user, ensure_venue_access, require_hq, venue_ids_for
from ..db import get_db
from ..models import (AcademyOccurrence, AcademySeries, Booking, CommunityGame, CorporateEvent, OperationalBlock,
                      Participant, User)
from ..services import activities, bookings, community
from .common import get_or_404, minutes

router = APIRouter(prefix="/api", tags=["operations"])


def _scoped(q, model, user: User, venue_id: int | None):
    allowed = venue_ids_for(user)
    if venue_id is not None:
        ensure_venue_access(user, venue_id)
        q = q.where(model.venue_id == venue_id)
    elif allowed is not None:
        q = q.where(model.venue_id.in_(allowed))
    return q


# ── Bookings ─────────────────────────────────────────────────────────────────
class DirectIn(BaseModel):
    venue_id: int
    facility_id: int
    date: date
    start: str
    duration: int = 60
    phone: str
    name: str
    amount: float | None = None
    pay_now: bool = False
    method: str | None = None


@router.get("/bookings")
def list_bookings(venue_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                  source: str | None = None, status: str | None = None, payment_status: str | None = None,
                  q: str | None = None, limit: int = 200, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    query = _scoped(select(Booking), Booking, user, venue_id)
    if date_from:
        query = query.where(Booking.date >= date_from)
    if date_to:
        query = query.where(Booking.date <= date_to)
    if source:
        query = query.where(Booking.source == source)
    if status:
        query = query.where(Booking.status == status)
    if payment_status:
        query = query.where(Booking.payment_status == payment_status)
    if q:
        from ..models import Customer
        query = query.join(Customer, Customer.id == Booking.customer_id).where(
            (Customer.phone.contains(q)) | (Customer.name.ilike(f"%{q}%")) | (Booking.external_ref.ilike(f"%{q}%")))
    rows = db.scalars(query.order_by(Booking.date.desc(), Booking.start_min).limit(min(limit, 500)))
    return [serialize.booking(db, b) for b in rows]


@router.post("/bookings/direct")
def create_direct(body: DirectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_venue_access(user, body.venue_id)
    b = bookings.create_direct(db, user, venue_id=body.venue_id, facility_id=body.facility_id, d=body.date,
                               start_min=minutes(body.start), duration=body.duration, phone=body.phone,
                               name=body.name, amount=body.amount, pay_now=body.pay_now, method=body.method)
    db.commit()
    return serialize.booking(db, b, detail=True)


def _booking(db: Session, user: User, booking_id: int) -> Booking:
    b = get_or_404(db, Booking, booking_id)
    ensure_venue_access(user, b.venue_id)
    return b


@router.get("/bookings/{booking_id}")
def get_booking(booking_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize.booking(db, _booking(db, user, booking_id), detail=True)


class FacilityIn(BaseModel):
    facility_id: int


class PayIn(BaseModel):
    method: str


class WindowIn(BaseModel):
    end: str  # HH:MM, may be past midnight via "24:30" style


class CancelIn(BaseModel):
    reason: str
    note: str | None = None


class AmountIn(BaseModel):
    amount: float


class RefundIn(BaseModel):
    amount: float
    reason: str


@router.post("/bookings/{booking_id}/assign")
def assign(booking_id: int, body: FacilityIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.assign_court(db, user, _booking(db, user, booking_id), body.facility_id)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/reassign")
def reassign(booking_id: int, body: FacilityIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.reassign(db, user, _booking(db, user, booking_id), body.facility_id)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/attended")
def attended(booking_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.mark_attended(db, user, _booking(db, user, booking_id))
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/no-show")
def no_show(booking_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.mark_no_show(db, user, _booking(db, user, booking_id))
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/pay")
def pay(booking_id: int, body: PayIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.record_payment(db, user, _booking(db, user, booking_id), body.method)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/extend-window")
def extend(booking_id: int, body: WindowIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.extend_window(db, user, _booking(db, user, booking_id), minutes(body.end))
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/cancel")
def cancel(booking_id: int, body: CancelIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.cancel(db, user, _booking(db, user, booking_id), body.reason, body.note)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/amount")
def amount(booking_id: int, body: AmountIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = bookings.change_amount(db, user, _booking(db, user, booking_id), body.amount)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.post("/bookings/{booking_id}/refund")
def refund(booking_id: int, body: RefundIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    b = _booking(db, user, booking_id)
    bookings.refund(db, user, b, body.amount, body.reason)
    db.commit()
    return serialize.booking(db, b, detail=True)


@router.get("/meta/reasons")
def reasons(_: User = Depends(current_user)):
    return {"booking_cancel": bookings.CANCEL_REASONS, "game_cancel": community.CANCEL_REASONS,
            "block": activities.BLOCK_REASONS, "levels": community.LEVELS, "methods": bookings.PAYMENT_METHODS}


# ── Community Games ──────────────────────────────────────────────────────────
class GameIn(BaseModel):
    venue_id: int
    title: str = "Community Game"
    sport: str
    date: date
    start: str
    end: str
    facility_ids: list[int]
    capacity: int = Field(ge=1)
    per_person: float = Field(ge=0)
    community_link: str


class FacilitiesIn(BaseModel):
    facility_ids: list[int]


class ParticipantIn(BaseModel):
    phone: str
    name: str | None = None
    level: str


class ParticipantPatch(BaseModel):
    attendance: str | None = None
    paid: bool | None = None
    method: str | None = None
    level: str | None = None


@router.get("/community-games")
def list_games(venue_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = _scoped(select(CommunityGame), CommunityGame, user, venue_id)
    if date_from:
        q = q.where(CommunityGame.date >= date_from)
    if date_to:
        q = q.where(CommunityGame.date <= date_to)
    return [serialize.game(db, g) for g in db.scalars(q.order_by(CommunityGame.date.desc(), CommunityGame.start_min).limit(300))]


@router.post("/community-games")
def create_game(body: GameIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    g = community.create_game(db, user, venue_id=body.venue_id, title=body.title, sport=body.sport, d=body.date,
                              start_min=minutes(body.start), end_min=minutes(body.end), facility_ids=body.facility_ids,
                              capacity=body.capacity, per_person=body.per_person, link=body.community_link)
    db.commit()
    return serialize.game(db, g, detail=True)


def _game(db, user, gid) -> CommunityGame:
    g = get_or_404(db, CommunityGame, gid)
    ensure_venue_access(user, g.venue_id)
    return g


@router.get("/community-games/{gid}")
def get_game(gid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize.game(db, _game(db, user, gid), detail=True)


@router.post("/community-games/{gid}/facilities")
def game_facilities(gid: int, body: FacilitiesIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    g = community.change_facilities(db, user, _game(db, user, gid), body.facility_ids)
    db.commit()
    return serialize.game(db, g, detail=True)


@router.post("/community-games/{gid}/cancel")
def game_cancel(gid: int, body: CancelIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    g = community.cancel_game(db, user, _game(db, user, gid), body.reason, body.note)
    db.commit()
    return serialize.game(db, g, detail=True)


@router.post("/community-games/{gid}/participants")
def add_participant(gid: int, body: ParticipantIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _game(db, user, gid)
    community.add_participant(db, user, g, body.phone, body.name, body.level)
    db.commit()
    db.refresh(g)
    return serialize.game(db, g, detail=True)


def _participant(db, user, pid) -> Participant:
    p = get_or_404(db, Participant, pid)
    ensure_venue_access(user, p.game.venue_id)
    return p


@router.patch("/participants/{pid}")
def patch_participant(pid: int, body: ParticipantPatch, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _participant(db, user, pid)
    community.update_participant(db, user, p, attendance=body.attendance, paid=body.paid, method=body.method,
                                 level=body.level)
    db.commit()
    return serialize.game(db, p.game, detail=True)


@router.delete("/participants/{pid}")
def delete_participant(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = _participant(db, user, pid)
    g = p.game
    community.remove_participant(db, user, p)
    db.commit()
    db.refresh(g)
    return serialize.game(db, g, detail=True)


# ── Academy ──────────────────────────────────────────────────────────────────
class AcademyIn(BaseModel):
    venue_id: int
    name: str
    facility_ids: list[int]
    recurring: bool = False
    weekdays: str = ""
    start_date: date
    end_date: date | None = None
    start: str
    end: str
    value_per_occurrence: float = Field(ge=0)


class ReconcileIn(BaseModel):
    month: str
    amount: float = Field(ge=0)
    method: str


class NoteIn(BaseModel):
    note: str | None = None


class EndSeriesIn(BaseModel):
    from_date: date


@router.get("/academy")
def list_academy(venue_id: int | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    q = _scoped(select(AcademySeries), AcademySeries, user, venue_id)
    return [serialize.series(db, s) for s in db.scalars(q.order_by(AcademySeries.start_date.desc()))]


@router.post("/academy")
def create_academy(body: AcademyIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    s, skipped = activities.create_academy(
        db, user, venue_id=body.venue_id, name=body.name, facility_ids=body.facility_ids, recurring=body.recurring,
        weekdays=body.weekdays, start_date=body.start_date, end_date=body.end_date, start_min=minutes(body.start),
        end_min=minutes(body.end), value_per_occurrence=body.value_per_occurrence)
    db.commit()
    return {**serialize.series(db, s, detail=True), "skipped_dates": skipped}


@router.get("/academy/{sid}")
def get_academy(sid: int, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    return serialize.series(db, get_or_404(db, AcademySeries, sid), detail=True)


@router.post("/academy/{sid}/end")
def end_academy(sid: int, body: EndSeriesIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    s = get_or_404(db, AcademySeries, sid)
    activities.end_series(db, user, s, body.from_date)
    db.commit()
    return serialize.series(db, s, detail=True)


@router.post("/academy/{sid}/reconcile")
def reconcile(sid: int, body: ReconcileIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    s = get_or_404(db, AcademySeries, sid)
    activities.reconcile_month(db, user, s, body.month, body.amount, body.method)
    db.commit()
    return serialize.series(db, s, detail=True)


@router.post("/academy/occurrences/{oid}/cancel")
def cancel_occurrence(oid: int, body: NoteIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    o = get_or_404(db, AcademyOccurrence, oid)
    activities.cancel_occurrence(db, user, o, body.note)
    db.commit()
    return serialize.occurrence(db, o)


# ── Corporate / Private events ───────────────────────────────────────────────
class EventIn(BaseModel):
    venue_id: int
    company: str
    contact_name: str
    contact_phone: str
    facility_ids: list[int]
    date: date
    start: str
    end: str
    amount: float = Field(ge=0)
    paid: bool = False
    method: str | None = None


@router.get("/events")
def list_events(venue_id: int | None = None, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    q = _scoped(select(CorporateEvent), CorporateEvent, user, venue_id)
    return [serialize.event(db, e) for e in db.scalars(q.order_by(CorporateEvent.date.desc()).limit(300))]


@router.post("/events")
def create_event(body: EventIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    e = activities.create_event(db, user, venue_id=body.venue_id, company=body.company, contact_name=body.contact_name,
                                contact_phone=body.contact_phone, facility_ids=body.facility_ids, d=body.date,
                                start_min=minutes(body.start), end_min=minutes(body.end), amount=body.amount,
                                paid=body.paid, method=body.method)
    db.commit()
    return serialize.event(db, e)


@router.post("/events/{eid}/pay")
def event_pay(eid: int, body: PayIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    e = activities.event_mark_paid(db, user, get_or_404(db, CorporateEvent, eid), body.method)
    db.commit()
    return serialize.event(db, e)


@router.post("/events/{eid}/cancel")
def event_cancel(eid: int, body: CancelIn, user: User = Depends(require_hq), db: Session = Depends(get_db)):
    e = activities.cancel_event(db, user, get_or_404(db, CorporateEvent, eid), body.note or body.reason)
    db.commit()
    return serialize.event(db, e)


# ── Operational blocks ───────────────────────────────────────────────────────
class BlockIn(BaseModel):
    venue_id: int
    facility_id: int
    date: date
    start: str
    end: str
    reason: str
    note: str | None = None


@router.get("/blocks")
def list_blocks(venue_id: int | None = None, date_from: date | None = None, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    q = _scoped(select(OperationalBlock), OperationalBlock, user, venue_id)
    if date_from:
        q = q.where(OperationalBlock.date >= date_from)
    return [serialize.block(db, b) for b in db.scalars(q.order_by(OperationalBlock.date.desc()).limit(300))]


@router.post("/blocks")
def create_block(body: BlockIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ensure_venue_access(user, body.venue_id)
    b = activities.create_block(db, user, venue_id=body.venue_id, facility_id=body.facility_id, d=body.date,
                                start_min=minutes(body.start), end_min=minutes(body.end), reason=body.reason,
                                note=body.note)
    db.commit()
    return serialize.block(db, b)


@router.post("/blocks/{bid}/release")
def release_block(bid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = get_or_404(db, OperationalBlock, bid)
    ensure_venue_access(user, b.venue_id)
    activities.release_block(db, user, b)
    db.commit()
    return serialize.block(db, b)

