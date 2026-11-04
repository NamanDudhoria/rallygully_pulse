"""Daily SOP: the photos a Venue Manager uploads during the day for each venue they run.

Four categories: venue, branding and toilets need at least one photo each before the day can be closed; "other"
is optional. Any number of photos per category (up to MAX_PER_KIND), uploaded any time that day by the rostered,
clocked-in VM (or HQ). Photos are kept SOP_RETENTION_DAYS days for HQ to review, then deleted.
"""
from datetime import date, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from .. import audit, config
from ..audit import DomainError
from ..models import SopPhoto, User, VmRoster
from ..timeutil import now_local, today
from .attendance import _decode_photo

KINDS = {"venue": "Venue", "branding": "Branding", "toilets": "Toilets", "other": "Other"}
REQUIRED = ("venue", "branding", "toilets")
MAX_PER_KIND = 20


def applies(db: Session, venue_id: int, d: date) -> bool:
    """The SOP is due on days a VM is rostered at the venue."""
    return bool(db.scalar(select(VmRoster.id).where(VmRoster.venue_id == venue_id, VmRoster.date == d).limit(1)))


def missing(db: Session, venue_id: int, d: date) -> list[str]:
    if not applies(db, venue_id, d):
        return []
    have = set(db.scalars(select(SopPhoto.kind).where(SopPhoto.venue_id == venue_id, SopPhoto.date == d).distinct()))
    return [k for k in REQUIRED if k not in have]


def status(db: Session, venue_id: int, d: date) -> dict:
    names = {i: n for i, n in db.execute(select(User.id, User.name))}
    rows = list(db.execute(select(SopPhoto.id, SopPhoto.kind, SopPhoto.user_id, SopPhoto.taken_at).where(
        SopPhoto.venue_id == venue_id, SopPhoto.date == d).order_by(SopPhoto.id)))
    cats = [{"kind": k, "label": label, "required": k in REQUIRED,
             "photos": [{"id": i, "by": names.get(u), "taken_at": t} for i, kind, u, t in rows if kind == k]}
            for k, label in KINDS.items()]
    gaps = [c["label"] for c in cats if c["required"] and not c["photos"]]
    return {"date": d, "due": applies(db, venue_id, d), "categories": cats, "missing": gaps,
            "complete": not gaps, "editable": d == today()}


def add(db: Session, actor: User, venue_id: int, kind: str, data_url: str) -> SopPhoto:
    if kind not in KINDS:
        raise DomainError("Choose a category: venue, branding, toilets or other.", 422)
    d = today()
    n = db.scalar(select(func.count(SopPhoto.id)).where(SopPhoto.venue_id == venue_id, SopPhoto.date == d,
                                                         SopPhoto.kind == kind))
    if n >= MAX_PER_KIND:
        raise DomainError(f"Up to {MAX_PER_KIND} {KINDS[kind].lower()} photos a day.", 409)
    p = SopPhoto(venue_id=venue_id, date=d, kind=kind, user_id=actor.id, data=_decode_photo(data_url),
                 taken_at=now_local())
    db.add(p)
    db.flush()
    audit.record(db, actor, "sop.photo", "sop_photo", p.id, None, {"kind": kind, "bytes": len(p.data)}, venue_id)
    return p


def remove(db: Session, actor: User, p: SopPhoto) -> None:
    """A wrong photo can be taken off the same day, by whoever uploaded it (or HQ)."""
    from ..permissions import is_hq
    if not is_hq(actor) and (p.user_id != actor.id or p.date != today()):
        raise DomainError("You can only remove your own photos, on the day.", 403)
    audit.record(db, actor, "sop.photo_remove", "sop_photo", p.id, {"kind": p.kind}, None, p.venue_id)
    db.delete(p)


def purge(db: Session) -> int:
    """Scheduler: delete SOP photos older than SOP_RETENTION_DAYS."""
    cutoff = today() - timedelta(days=config.SOP_RETENTION_DAYS)
    return db.execute(delete(SopPhoto).where(SopPhoto.date < cutoff)).rowcount or 0
