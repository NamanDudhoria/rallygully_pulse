"""Cash held by Venue Managers: their own view and actions, and HQ's view of everyone."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import User, Venue, VmCashEntry, VmRoster
from ..permissions import can, require
from ..services import vmcash
from ..timeutil import today
from .common import get_or_404

router = APIRouter(prefix="/api", tags=["vm-cash"])


def _vm(user: User) -> User:
    if user.role != "vm":
        raise HTTPException(403, "Only Venue Managers hold cash.")
    return user


def _names(db: Session):
    return {i: n for i, n in db.execute(select(User.id, User.name))}, {v.id: v.name for v in db.scalars(select(Venue))}


def _entries(db: Session, user_id: int, limit: int = 100) -> list[dict]:
    names, venues = _names(db)
    return [vmcash.entry_dict(e, names, venues) for e in db.scalars(
        select(VmCashEntry).where(VmCashEntry.user_id == user_id).order_by(VmCashEntry.id.desc()).limit(limit))]


def _summary(db: Session, vm: User) -> dict:
    h = vmcash.holdings(db, [vm.id]).get(vm.id, {"venues": {}, "total": 0.0, "collected": 0.0, "deposited": 0.0,
                                                  "expenses": 0.0, "adjusted": 0.0})
    venues = {v.id: v.name for v in db.scalars(select(Venue))}
    return {**{k: v for k, v in h.items() if k != "venues"},
            "venues": [{"venue_id": vid, "name": venues.get(vid), "balance": b} for vid, b in sorted(h["venues"].items())],
            "entries": _entries(db, vm.id)}


class MoveIn(BaseModel):
    venue_id: int
    amount: float
    message: str | None = None
    photo: str | None = Field(default=None, max_length=5_000_000)  # data:image/jpeg;base64,... from the live camera


class AdjustIn(BaseModel):
    venue_id: int
    amount: float
    message: str


@router.get("/my-cash")
def my_cash(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return _summary(db, _vm(user))


@router.post("/my-cash/{kind}")
def my_cash_move(kind: str, body: MoveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    if kind not in ("deposit", "expense"):
        raise HTTPException(404, "Not found")
    vmcash.record(db, user, _vm(user), body.venue_id, kind, body.amount, body.message, body.photo)
    db.commit()
    return _summary(db, user)


@router.get("/vm-cash/photo/{entry_id}")
def receipt(entry_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    e = get_or_404(db, VmCashEntry, entry_id)
    if e.user_id != user.id and not (can(user, "cash.manage") or can(user, "roster.manage")):
        raise HTTPException(403, "You can't see that receipt.")
    if not e.photo:
        raise HTTPException(404, "No photo on that entry.")
    return Response(e.photo, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@router.get("/vm-cash")
def vm_cash(date_from: date | None = None, date_to: date | None = None,
            user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    """One row per active VM: where they are rostered today, the cash they hold, and their cash movement in the period."""
    d1 = date_to or today()
    d0 = date_from or d1 - timedelta(days=6)
    if d1 < d0 or (d1 - d0).days > 92:
        raise HTTPException(422, "Choose up to 92 days.")
    vms = list(db.scalars(select(User).where(User.role == "vm", User.active.is_(True)).order_by(User.name)))
    held = vmcash.holdings(db, [u.id for u in vms])
    venues = {v.id: v for v in db.scalars(select(Venue))}
    rost = {}
    for r in db.scalars(select(VmRoster).where(VmRoster.date >= d0, VmRoster.date <= d1)):
        rost.setdefault(r.user_id, []).append(r)
    moved = {}
    for uid, kind, total, last in db.execute(select(VmCashEntry.user_id, VmCashEntry.kind, func.sum(VmCashEntry.amount),
                                                    func.max(VmCashEntry.created_at)).group_by(VmCashEntry.user_id, VmCashEntry.kind)):
        moved[(uid, kind)] = (float(total or 0), last)
    rows = []
    for u in vms:
        h = held.get(u.id, {"venues": {}, "total": 0.0, "collected": 0.0, "deposited": 0.0, "expenses": 0.0})
        mine = rost.get(u.id, [])
        t = today()
        todays = sorted({venues[r.venue_id].name for r in mine if r.date == t and r.venue_id in venues})
        days = {}
        for r in mine:
            days.setdefault(r.venue_id, set()).add(r.date)
        rows.append({
            "user_id": u.id, "name": u.name, "phone": u.phone,
            "today": todays,
            "allotted": [{"venue_id": vid, "name": venues[vid].name, "days": len(ds)} for vid, ds in sorted(days.items()) if vid in venues],
            "cash": h["total"], "collected": h["collected"], "deposited": h["deposited"], "expenses": h["expenses"],
            "holding": [{"venue_id": vid, "name": venues[vid].name, "balance": b} for vid, b in sorted(h["venues"].items())
                        if vid in venues and abs(b) > 0.004],
            "last_deposit": moved.get((u.id, "deposit"), (0, None))[1],
        })
    return {"date_from": d0, "date_to": d1, "rows": rows, "total_cash": round(sum(r["cash"] for r in rows), 2)}


@router.get("/vm-cash/{user_id}")
def vm_cash_detail(user_id: int, user: User = Depends(require("roster.manage")), db: Session = Depends(get_db)):
    vm = get_or_404(db, User, user_id)
    if vm.role != "vm":
        raise HTTPException(404, "Not a Venue Manager.")
    return {"name": vm.name, **_summary(db, vm)}


@router.post("/vm-cash/{user_id}/adjust")
def vm_cash_adjust(user_id: int, body: AdjustIn, user: User = Depends(require("cash.manage")), db: Session = Depends(get_db)):
    vm = get_or_404(db, User, user_id)
    vmcash.adjust(db, user, vm, body.venue_id, body.amount, body.message)
    db.commit()
    return {"name": vm.name, **_summary(db, vm)}
