"""Community Games: HQ lodges the game and its courts; VMs record on-ground participants."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..audit import DomainError
from ..models import AllocationKind, CommunityGame, Customer, Facility, Participant, Payment, User
from ..timeutil import minute_of, now_local, today
from . import customers, inventory, notify
from .bookings import PAYMENT_METHODS

LEVELS = ("beginner", "intermediate", "advanced")
CANCEL_REASONS = {
    "low_registrations": "Low registrations",
    "weather": "Weather",
    "facility_issue": "Facility issue",
    "community_decision": "Community team decision",
    "other": "Other",
}


def game_state(g: CommunityGame) -> str:
    if g.status == "cancelled":
        return "cancelled"
    now = now_local()
    if g.date > now.date():
        return "upcoming"
    if g.date < now.date():
        return "completed"
    m = minute_of(now, g.date)
    return "upcoming" if m < g.start_min else "live" if m < g.end_min else "completed"


def _started(g: CommunityGame) -> bool:
    return game_state(g) in ("live", "completed")


def _check_facilities(db: Session, venue_id: int, sport: str, facility_ids: list[int]) -> None:
    for fid in facility_ids:
        f = db.get(Facility, fid)
        if not f or f.venue_id != venue_id:
            raise DomainError("Facility not found at this venue.", 422)
        if f.sport != sport:
            raise DomainError(f"{f.name} is a {f.sport} facility, not {sport}.", 422)


def create_game(db: Session, actor: User, *, venue_id: int, title: str, sport: str, d: date, start_min: int,
                end_min: int, facility_ids: list[int], capacity: int, per_person: float, link: str) -> CommunityGame:
    if capacity < 1 or per_person < 0:
        raise DomainError("Capacity must be ≥ 1 and charge ≥ 0.", 422)
    if not link.strip():
        raise DomainError("Community joining link is required.", 422)
    _check_facilities(db, venue_id, sport, facility_ids)
    g = CommunityGame(venue_id=venue_id, title=title.strip() or "Community Game", sport=sport, date=d,
                      start_min=start_min, end_min=end_min, capacity=capacity, per_person=per_person,
                      community_link=link.strip())
    db.add(g)
    db.flush()
    inventory.allocate(db, venue_id, facility_ids, d, start_min, end_min, AllocationKind.COMMUNITY, g.id)
    audit.record(db, actor, "community.create", "community_game", g.id, None,
                 {"facilities": facility_ids, "date": d, "start": start_min, "end": end_min,
                  "capacity": capacity, "per_person": per_person}, venue_id)
    return g


def change_facilities(db: Session, actor: User, g: CommunityGame, facility_ids: list[int]) -> CommunityGame:
    if g.status == "cancelled":
        raise DomainError("Game is cancelled.")
    if _started(g):
        raise DomainError("Courts are locked once the game has started.")
    _check_facilities(db, g.venue_id, g.sport, facility_ids)
    before = inventory.facilities_of(db, AllocationKind.COMMUNITY, g.id)
    inventory.check_free(db, g.venue_id, facility_ids, g.date, g.start_min, g.end_min,
                         ignore=(AllocationKind.COMMUNITY, g.id))
    inventory.release(db, AllocationKind.COMMUNITY, g.id)
    inventory.allocate(db, g.venue_id, facility_ids, g.date, g.start_min, g.end_min, AllocationKind.COMMUNITY, g.id)
    audit.record(db, actor, "community.change_facilities", "community_game", g.id,
                 {"facilities": before}, {"facilities": facility_ids}, g.venue_id)
    return g


def cancel_game(db: Session, actor: User, g: CommunityGame, reason: str, note: str | None) -> CommunityGame:
    if g.status == "cancelled":
        raise DomainError("Already cancelled.")
    if _started(g):
        raise DomainError("A game can only be cancelled before it starts.")
    if reason not in CANCEL_REASONS:
        raise DomainError("Choose a cancellation reason.", 422)
    if reason == "other" and not (note or "").strip():
        raise DomainError("Add a note for 'Other'.", 422)
    g.status, g.cancel_reason, g.cancel_note = "cancelled", reason, (note or "").strip() or None
    inventory.release(db, AllocationKind.COMMUNITY, g.id)
    audit.record(db, actor, "community.cancel", "community_game", g.id, {"status": "scheduled"},
                 {"status": "cancelled", "reason": reason}, g.venue_id)
    return g


def add_participant(db: Session, actor: User, g: CommunityGame, phone: str, name: str | None, level: str) -> Participant:
    if g.status == "cancelled":
        raise DomainError("Game is cancelled.")
    if level not in LEVELS:
        raise DomainError("Choose a level: beginner, intermediate or advanced.", 422)
    if actor.role != "hq" and g.date != today():
        raise DomainError("Participants are added on the game day.")
    existing = customers.find_by_phone(db, phone)
    if not existing and not (name or "").strip():
        raise DomainError("New participant — enter their name.", 422)
    customer, _ = customers.get_or_create(db, phone, name)
    if db.scalar(select(Participant).where(Participant.game_id == g.id, Participant.customer_id == customer.id)):
        raise DomainError(f"{customer.name or customer.phone} is already in this game.")
    if len(g.participants) >= g.capacity:
        raise DomainError(f"Game is full ({g.capacity} players).")
    p = Participant(game_id=g.id, customer_id=customer.id, level=level)
    db.add(p)
    db.flush()
    audit.record(db, actor, "community.add_participant", "participant", p.id, None,
                 {"game_id": g.id, "customer_id": customer.id, "level": level}, g.venue_id)
    notify.whatsapp(db, customer.phone, "community_link", notify.community_message(level, g.community_link),
                    "participant", p.id)
    return p


def _p_snapshot(p: Participant) -> dict:
    return {"level": p.level, "attendance": p.attendance, "payment_status": p.payment_status,
            "amount": p.amount, "payment_method": p.payment_method}


def update_participant(db: Session, actor: User, p: Participant, *, attendance: str | None = None,
                       paid: bool | None = None, method: str | None = None, level: str | None = None) -> Participant:
    g = p.game
    before = _p_snapshot(p)
    if level is not None:
        if level not in LEVELS:
            raise DomainError("Invalid level.", 422)
        p.level = level
    if attendance is not None:
        if attendance not in ("added", "attended", "no_show"):
            raise DomainError("Invalid attendance.", 422)
        p.attendance = attendance
    if paid is True and p.payment_status != "paid":
        if method not in PAYMENT_METHODS:
            raise DomainError("Choose a payment method.", 422)
        p.payment_status, p.payment_method, p.amount = "paid", method, g.per_person
        db.add(Payment(source_type="participant", source_id=p.id, venue_id=g.venue_id, activity_date=g.date,
                       amount=g.per_person, method=method, collected_at=now_local(), actor_id=actor.id))
    elif paid is False and p.payment_status == "paid":
        p.payment_status, p.payment_method, p.amount = "unpaid", None, 0
        for pay in db.scalars(select(Payment).where(Payment.source_type == "participant", Payment.source_id == p.id,
                                                    Payment.void.is_(False))):
            pay.void = True
    audit.record(db, actor, "community.update_participant", "participant", p.id, before, _p_snapshot(p), g.venue_id)
    return p


def remove_participant(db: Session, actor: User, p: Participant) -> None:
    if p.payment_status == "paid":
        raise DomainError("Mark the payment unpaid before removing a paid participant.")
    audit.record(db, actor, "community.remove_participant", "participant", p.id, _p_snapshot(p), None, p.game.venue_id)
    db.delete(p)


def participant_dict(db: Session, p: Participant) -> dict:
    c = db.get(Customer, p.customer_id)
    return {"id": p.id, "customer_id": c.id, "name": c.name, "phone": c.phone, "level": p.level,
            "attendance": p.attendance, "payment_status": p.payment_status, "amount": p.amount,
            "payment_method": p.payment_method}
