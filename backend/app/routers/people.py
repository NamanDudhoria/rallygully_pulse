"""People (users, roles, venue teams, invites) and the daily VM roster."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, serialize
from ..auth import create_auth_token, link_for
from ..db import get_db
from ..models import User, Venue, VmAssignment, VmLeave, VmRoster
from ..permissions import PERMISSIONS, ROLE_PERMISSIONS, ROLES, require
from ..services import attendance, inventory, roster
from ..services.customers import normalize_phone
from ..timeutil import today
from .auth_routes import send_link_email, smtp_configured
from .common import get_or_404, minutes

router = APIRouter(prefix="/api", tags=["people"])


class UserIn(BaseModel):
    name: str
    email: str
    role: str
    venue_ids: list[int] = []
    active: bool = True
    phone: str | None = None
    week_off: int | None = None  # a VM's weekly day off: 0 = Monday … 6 = Sunday


def _validate(db: Session, body: UserIn) -> None:
    if body.week_off is not None and not 0 <= body.week_off <= 6:
        raise HTTPException(422, "Weekly off must be a day of the week.")
    if body.role not in ROLES:
        raise HTTPException(422, f"Role must be one of: {', '.join(ROLES)}")
    if not body.name.strip():
        raise HTTPException(422, "Name is required.")
    if "@" not in body.email or body.email.strip().startswith("@"):
        raise HTTPException(422, "Enter a valid work email.")
    for v in body.venue_ids:
        get_or_404(db, Venue, v)


def _link_result(db: Session, u: User, purpose: str) -> dict:
    """Send the invite/reset email. Without SMTP configured, hand the link to the admin to share."""
    raw = create_auth_token(db, u, purpose)
    send_link_email(db, u, purpose, raw)
    return {"emailed": smtp_configured(), "link": None if smtp_configured() else link_for(raw), "purpose": purpose}


@router.get("/roles")
def roles(_: User = Depends(require("users.manage", "roster.manage"))):
    return {"roles": [{"value": k, "label": v, "permissions": sorted(ROLE_PERMISSIONS[k])} for k, v in ROLES.items()],
            "permissions": PERMISSIONS}


@router.get("/users")
def list_users(user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    return [serialize.user(u) for u in db.scalars(select(User).order_by(User.role, User.name))]


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    """People are created by HQ only, keyed by work email, and set their own password via an invite link."""
    _validate(db, body)
    email = body.email.strip().lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(409, "Someone with that email already exists.")
    u = User(name=body.name.strip(), email=email, role=body.role, password_hash=None,
             week_off=body.week_off if body.role == "vm" else None,
             phone=(normalize_phone(body.phone) if (body.phone or "").strip() else None))
    u.venues = [VmAssignment(venue_id=v) for v in body.venue_ids] if body.role == "vm" else []
    db.add(u)
    db.flush()
    audit.record(db, user, "user.create", "user", u.id, None,
                 {"email": email, "role": body.role, "venues": body.venue_ids})
    result = _link_result(db, u, "invite")
    db.commit()
    return {**serialize.user(u), "invite": result}


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    _validate(db, body)
    u = get_or_404(db, User, uid)
    if u.id == user.id and (body.role != u.role or not body.active):
        raise HTTPException(409, "You can't change your own role or disable yourself. Ask another HQ Admin.")
    before = serialize.user(u)
    role_changed = body.role != u.role
    u.name, u.role = body.name.strip(), body.role
    u.phone = normalize_phone(body.phone) if (body.phone or "").strip() else None
    u.week_off = body.week_off if body.role == "vm" else None
    if u.active and not body.active:
        u.token_version = (u.token_version or 0) + 1  # sign them out immediately
    u.active = body.active
    new_team = set(body.venue_ids) if body.role == "vm" else set()
    u.venues = [a for a in u.venues if a.venue_id in new_team] + [
        VmAssignment(user_id=u.id, venue_id=v) for v in new_team - {a.venue_id for a in u.venues}]
    if role_changed:
        u.token_version = (u.token_version or 0) + 1  # new permissions apply from their next sign-in
    db.flush()
    # Leaving a venue team (or the VM role) removes future roster entries there.
    dropped, dates = 0, set()
    for r in db.scalars(select(VmRoster).where(VmRoster.user_id == u.id, VmRoster.date >= today())):
        if r.venue_id not in new_team or not u.active:
            db.delete(r)
            dates.add(r.date)
            dropped += 1
    db.flush()
    for d in sorted(dates):
        roster.touch(db, d)
    audit.record(db, user, "user.update", "user", u.id, before, {**serialize.user(u), "roster_removed": dropped})
    db.commit()
    return serialize.user(u)


@router.post("/users/{uid}/send-link")
def send_link(uid: int, user: User = Depends(require("users.manage")), db: Session = Depends(get_db)):
    """Re-send the invite, or send a password-reset link to someone who already set a password."""
    u = get_or_404(db, User, uid)
    if not u.active:
        raise HTTPException(409, "Enable this person first.")
    purpose = "reset" if u.password_hash else "invite"
    result = _link_result(db, u, purpose)
    audit.record(db, user, f"user.send_{purpose}", "user", u.id)
    db.commit()
    return result


# ── Roster ───────────────────────────────────────────────────────────────────
class RosterIn(BaseModel):
    venue_id: int
    user_id: int
    date: date
    start: str | None = None  # HH:MM; omit start and end for the venue's full hours
    end: str | None = None


class ShiftIn(BaseModel):
    start: str | None = None
    end: str | None = None


def _shift(start: str | None, end: str | None) -> tuple[int | None, int | None]:
    return (minutes(start) if start else None), (minutes(end) if end else None)


class CopyIn(BaseModel):
    from_date: date
    to_date: date
    days: int = 7


@router.get("/roster")
def get_roster(date_from: date, date_to: date, user: User = Depends(require("roster.manage")),
               db: Session = Depends(get_db)):
    if date_to < date_from or (date_to - date_from).days > 62:
        raise HTTPException(422, "Choose up to 62 days.")
    venues = list(db.scalars(select(Venue).where(Venue.status == "active").order_by(Venue.id)))
    entries = list(db.execute(select(VmRoster, User).join(User, User.id == VmRoster.user_id).where(
        VmRoster.date >= date_from, VmRoster.date <= date_to)))
    seen = attendance.by_roster(db, [(u.id, r.venue_id, r.date) for r, u in entries])
    days = []
    d = date_from
    while d <= date_to:
        days.append(d)
        d += timedelta(days=1)
    teams = {}
    for v in venues:
        teams[v.id] = [{"id": u.id, "name": u.name, "email": u.email}
                       for u in db.scalars(select(User).join(VmAssignment, VmAssignment.user_id == User.id).where(
                           VmAssignment.venue_id == v.id, User.role == "vm", User.active.is_(True)).order_by(User.name))]
    return {
        "days": days,
        "venues": [{"id": v.id, "name": v.name, "code": v.code, "team": teams[v.id],
                    "hours": {x.isoformat(): inventory.hours_for(db, v.id, x) for x in days}}
                   for v in venues],
        "entries": [{**_entry(db, r, u), "attendance": seen.get((u.id, r.venue_id, r.date))} for r, u in entries],
        "leaves": [{"user_id": x.user_id, "name": n, "date": x.date, "kind": x.kind} for x, n in db.execute(
            select(VmLeave, User.name).join(User, User.id == VmLeave.user_id).where(
                VmLeave.date >= date_from, VmLeave.date <= date_to).order_by(VmLeave.date, User.name))],
        "tomorrow_gaps": [v.name for v in roster.gaps(db, today() + timedelta(days=1))],
        "vms": [{"id": u.id, "name": u.name, "email": u.email} for u in db.scalars(
            select(User).where(User.role == "vm", User.active.is_(True)).order_by(User.name))],
    }


def _entry(db: Session, r: VmRoster, u: User) -> dict:
    w = roster.window(db, r)
    return {"id": r.id, "venue_id": r.venue_id, "user_id": r.user_id, "name": u.name, "date": r.date,
            "custom": r.start_min is not None, "start_min": w[0] if w else None, "end_min": w[1] if w else None}


@router.post("/roster")
def add_roster(body: RosterIn, user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    r = roster.add(db, user, body.venue_id, body.user_id, body.date, *_shift(body.start, body.end))
    db.commit()
    return {"id": r.id}


@router.patch("/roster/{rid}")
def set_shift(rid: int, body: ShiftIn, user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    r = roster.set_shift(db, user, get_or_404(db, VmRoster, rid), *_shift(body.start, body.end))
    db.commit()
    return _entry(db, r, db.get(User, r.user_id))


@router.delete("/roster/{rid}")
def remove_roster(rid: int, user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    roster.remove(db, user, get_or_404(db, VmRoster, rid))
    db.commit()
    return {"ok": True}


@router.post("/roster/copy")
def copy_roster(body: CopyIn, user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    n = roster.copy_range(db, user, body.from_date, body.to_date, body.days)
    db.commit()
    return {"added": n}
