"""Community Games.

The Community team sets up the game and adds every player before it starts (one at a
time, pasted from a sheet, or uploaded). On the day, VMs only mark attendance and
collect payment from players who haven't already paid.
"""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..permissions import can
from ..audit import DomainError
from ..models import AllocationKind, CommunityGame, Customer, Facility, Participant, Payment, User
from ..timeutil import minute_of, now_local
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


def _require_manage(actor: User, what: str) -> None:
    if not can(actor, "community.manage"):
        raise DomainError(f"{what} is done by the Community team. Venue Managers mark attendance and "
                          f"collect payment.", 403)


def _player_name(db: Session, p: Participant) -> str:
    c = db.get(Customer, p.customer_id) if p.customer_id else None
    return (c and (c.name or c.phone)) or p.name or "Player"


def _check_open(g: CommunityGame) -> None:
    if g.status == "cancelled":
        raise DomainError("Game is cancelled.")
    if game_state(g) == "completed":
        raise DomainError("This game has finished.")


def add_participant(db: Session, actor: User, g: CommunityGame, phone: str | None, name: str | None, level: str,
                    notify_player: bool = True, guest_of: int | None = None) -> Participant:
    """Add a player by phone (linked to the customer record) or, failing that, by name only."""
    _require_manage(actor, "Adding players")
    _check_open(g)
    if level not in LEVELS:
        raise DomainError("Choose a level: beginner, intermediate or advanced.", 422)
    if len(g.participants) >= g.capacity:
        raise DomainError(f"Game is full ({g.capacity} players).")
    name = (name or "").strip() or None
    customer = None
    if (phone or "").strip():
        existing = customers.find_by_phone(db, phone)
        if not existing and not name:
            raise DomainError("New participant — enter their name.", 422)
        customer, _ = customers.get_or_create(db, phone, name)
        if db.scalar(select(Participant).where(Participant.game_id == g.id, Participant.customer_id == customer.id)):
            raise DomainError(f"{customer.name or customer.phone} is already in this game.")
    elif not name:
        raise DomainError("Enter a name, a phone number, or both.", 422)
    p = Participant(game_id=g.id, customer_id=customer.id if customer else None, name=None if customer else name,
                    level=level, guest_of=guest_of)
    db.add(p)
    db.flush()
    db.refresh(g)
    audit.record(db, actor, "community.add_participant", "participant", p.id, None,
                 {"game_id": g.id, "customer_id": p.customer_id, "name": p.name, "level": level,
                  "guest_of": guest_of}, g.venue_id)
    if notify_player and customer:
        notify.whatsapp(db, customer.phone, "community_link", notify.community_message(level, g.community_link),
                        "participant", p.id)
    return p


def set_phone(db: Session, actor: User, p: Participant, phone: str) -> Participant:
    """Link a name-only player to a customer once their number is known (Community or the VM on ground)."""
    if p.customer_id:
        raise DomainError("This player already has a phone number.")
    c = customers.find_by_phone(db, phone)
    if c and db.scalar(select(Participant.id).where(Participant.game_id == p.game_id, Participant.customer_id == c.id)):
        raise DomainError(f"{c.name or c.phone} is already in this game.")
    c, _ = customers.get_or_create(db, phone, p.name)
    before = {"customer_id": None, "name": p.name}
    p.customer_id, p.name = c.id, None
    audit.record(db, actor, "community.link_phone", "participant", p.id, before, {"customer_id": c.id},
                 p.game.venue_id)
    return p


def _norm(name: str | None) -> str:
    return " ".join((name or "").lower().split())


def import_players(db: Session, actor: User, g: CommunityGame, parsed: list[dict], default_level: str,
                   dry_run: bool, sections: list[dict] | None = None, only: list[str] | None = None) -> dict:
    """Validate (and unless ``dry_run``, add) a parsed player list. Rows that can't be added are reported.

    ``sections`` come from a WhatsApp post ("7-8 PM | Intermediate"); ``only`` limits the import to
    those labels, defaulting to the sections whose time matches this game (or all, if none match).
    """
    _require_manage(actor, "Adding players")
    _check_open(g)
    if default_level not in LEVELS:
        raise DomainError("Choose a default level.", 422)
    sections = sections or []
    for sec in sections:
        sec["matches"] = sec["start_min"] < g.end_min and g.start_min < sec["end_min"]
    if only is None:
        matching = [x["label"] for x in sections if x["matches"]]
        only = matching or [x["label"] for x in sections]
    for sec in sections:
        sec["selected"] = sec["label"] in only
    in_game_ids = {p.customer_id for p in g.participants if p.customer_id}
    in_game_names = {_norm(_player_name(db, p)) for p in g.participants}
    by_id = {p.id: p for p in g.participants}
    guests_in_game: dict[str, int] = {}
    for p in g.participants:
        if p.guest_of in by_id:
            host = _norm(_player_name(db, by_id[p.guest_of]))
            guests_in_game[host] = guests_in_game.get(host, 0) + 1
    room = g.capacity - len(g.participants)
    seen_phones: set[str] = set()
    seen_names: dict[str, dict] = {}
    rows = []
    for r in parsed:
        if r.get("section") and r["section"] not in only:
            continue
        row = {**r, "guests": r.get("guests", 0), "level": r.get("level") or default_level,
               "status": "ready", "message": None, "existing": False, "host_only": False}
        key = _norm(r.get("name"))
        if r.get("error"):
            row["status"], row["message"] = "error", r["error"]
        elif r.get("phone"):
            c = customers.find_by_phone(db, r["phone"])
            if r["phone"] in seen_phones:
                row["status"], row["message"] = "skip", "Listed twice"
            elif c and c.id in in_game_ids:
                row["status"], row["message"] = "skip", "Already in this game"
            elif not c and not key:
                row["status"], row["message"] = "error", "New player — name needed"
            else:
                row["existing"] = bool(c)
                if c and c.name_locked and c.name:
                    row["name"] = c.name
            seen_phones.add(r["phone"])
        elif key in seen_names or key in in_game_names:
            have = guests_in_game.get(key, 0)
            row["guests"] = max(0, row["guests"] - have)
            guests_in_game[key] = max(have - r.get("guests", 0), 0)
            if row["guests"]:
                row["host_only"] = True  # "Jatan + 1" after "Jatan": only the guest is new
                row["message"] = f"Guest of {r['name']}"
            else:
                row["status"], row["message"] = "skip", "Listed twice" if key in seen_names else "Already in this game"
        else:
            row["message"] = "Name only — add a phone later"
        if row["status"] == "ready":
            need = (0 if row["host_only"] else 1) + row["guests"]
            if room < need:
                row["status"], row["message"] = "skip", f"Game full ({g.capacity})"
            else:
                room -= need
            if key:
                seen_names.setdefault(key, row)
        rows.append(row)
    added = 0
    if not dry_run:
        hosts: dict[str, int] = {_norm(_player_name(db, p)): p.id for p in g.participants}
        for row in rows:
            if row["status"] != "ready":
                continue
            key = _norm(row.get("name"))
            host_id = hosts.get(key) if row["host_only"] else None
            if not row["host_only"]:
                host = add_participant(db, actor, g, row.get("phone"), row.get("name"), row["level"])
                host_id = hosts[key] = host.id
                added += 1
            for n in range(row["guests"]):
                label = f"{row.get('name') or 'Player'}'s guest" + (f" {n + 1}" if row["guests"] > 1 else "")
                add_participant(db, actor, g, None, label, row["level"], guest_of=host_id)
                added += 1
            row["status"] = "added"
        audit.record(db, actor, "community.import_players", "community_game", g.id, None,
                     {"added": added, "rows": len(rows)}, g.venue_id)
    ready = [r for r in rows if r["status"] in ("ready", "added")]
    return {"rows": rows, "sections": sections,
            "ready": sum((0 if r["host_only"] else 1) + r["guests"] for r in ready), "added": added,
            "problems": sum(r["status"] == "error" for r in rows), "skipped": sum(r["status"] == "skip" for r in rows),
            "capacity_left": g.capacity - len(g.participants)}


def _p_snapshot(p: Participant) -> dict:
    return {"level": p.level, "attendance": p.attendance, "payment_status": p.payment_status,
            "amount": p.amount, "payment_method": p.payment_method}


def update_participant(db: Session, actor: User, p: Participant, *, attendance: str | None = None,
                       paid: bool | None = None, method: str | None = None, level: str | None = None) -> Participant:
    g = p.game
    before = _p_snapshot(p)
    manage = can(actor, "community.manage")
    if level is not None and level != p.level:
        _require_manage(actor, "Changing a player's level")
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
        live = list(db.scalars(select(Payment).where(Payment.source_type == "participant", Payment.source_id == p.id,
                                                     Payment.void.is_(False))))
        if not manage and any(pay.actor_id != actor.id for pay in live):
            raise DomainError("This payment was recorded by the Community team — ask them to change it.", 403)
        p.payment_status, p.payment_method, p.amount = "unpaid", None, 0
        for pay in db.scalars(select(Payment).where(Payment.source_type == "participant", Payment.source_id == p.id,
                                                    Payment.void.is_(False))):
            pay.void = True
    audit.record(db, actor, "community.update_participant", "participant", p.id, before, _p_snapshot(p), g.venue_id)
    return p


def remove_participant(db: Session, actor: User, p: Participant) -> None:
    _require_manage(actor, "Removing players")
    if p.payment_status == "paid":
        raise DomainError("Mark the payment unpaid before removing a paid participant.")
    for guest in db.scalars(select(Participant).where(Participant.guest_of == p.id)):
        guest.guest_of = None
    audit.record(db, actor, "community.remove_participant", "participant", p.id, _p_snapshot(p), None, p.game.venue_id)
    db.delete(p)


def participant_dict(db: Session, p: Participant) -> dict:
    c = db.get(Customer, p.customer_id) if p.customer_id else None
    collected_by = None
    if p.payment_status == "paid":
        collected_by = db.scalar(select(Payment.actor_id).where(Payment.source_type == "participant",
                                                                Payment.source_id == p.id, Payment.void.is_(False)))
    host = db.get(Participant, p.guest_of) if p.guest_of else None
    return {"id": p.id, "customer_id": c.id if c else None, "name": (c.name if c else None) or p.name,
            "phone": c.phone if c else None, "level": p.level, "guest_of": p.guest_of,
            "guest_of_name": _player_name(db, host) if host else None,
            "attendance": p.attendance, "payment_status": p.payment_status, "amount": p.amount,
            "payment_method": p.payment_method, "collected_by": collected_by}
