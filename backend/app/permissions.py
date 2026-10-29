"""Roles → permissions. Every API check goes through ``can``/``require``; nothing compares role names.

Roles (one per user):
- hq_admin   General HQ with people management.
- hq         General HQ: full operations, configuration, roster, audit — not people management.
- community  Community Operations: manages Community Games; full analytics; read-only venue views.
- academy    Academy: manages Academy bookings and reconciliation; full analytics; read-only venue views.
- vm         Venue Manager: operates a venue only on dates they are rostered for it.
"""
from datetime import date

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

ROLES = {
    "hq_admin": "HQ Admin",
    "hq": "HQ",
    "community": "Community Operations",
    "academy": "Academy",
    "vm": "Venue Manager",
}

PERMISSIONS = {
    "portfolio.view": "Portfolio dashboard, performance drill-down and insights",
    "venue.view": "Read-only daily performance and availability for any venue",
    "venue.operate": "Venue operations: bookings, payments, blocks, participants, day close",
    "bookings.view": "Booking list across all venues",
    "community.manage": "Create, edit and cancel Community Games",
    "academy.manage": "Create and manage Academy bookings and reconciliation",
    "events.manage": "Create and manage Corporate / Private events",
    "corrections": "Correct past or closed records, refunds, paid-amount changes",
    "customers.view": "Customer profiles and history",
    "config.manage": "Venues, facilities, hours, special dates and pricing",
    "roster.manage": "Daily VM roster",
    "users.manage": "Create people, set roles, send invites and reset links",
    "cash.manage": "See venue cash balances and record deposits and other changes to them",
    "audit.view": "Audit trail",
    "system.view": "Integrations: District ingestion, WhatsApp and email logs",
}

_ALL = set(PERMISSIONS)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "hq_admin": _ALL,
    "hq": _ALL - {"users.manage"},
    "community": {"portfolio.view", "venue.view", "community.manage", "customers.view"},
    "academy": {"portfolio.view", "venue.view", "academy.manage"},
    "vm": {"venue.operate"},
}


def can(user, perm: str) -> bool:
    return user is not None and perm in ROLE_PERMISSIONS.get(user.role, set())


def is_hq(user) -> bool:
    """HQ-level authority over operational records (overrides VM-only time limits)."""
    return user is None or can(user, "corrections")


def roster_scoped(user) -> bool:
    return user is not None and user.role == "vm"


def require(*perms: str):
    """FastAPI dependency: the user must hold at least one of ``perms``."""
    from .auth import current_user

    def dep(user=Depends(current_user)):
        if not any(can(user, p) for p in perms):
            raise HTTPException(403, "You don't have access to this.")
        return user
    return dep


def rostered(db: Session, user, venue_id: int, d: date) -> bool:
    from .models import VmRoster
    return db.scalar(select(VmRoster.id).where(VmRoster.user_id == user.id, VmRoster.venue_id == venue_id,
                                               VmRoster.date == d)) is not None


def ensure_venue(db: Session, user, venue_id: int, d: date, write: bool = False) -> None:
    """Gate venue access for a specific date.

    Writes need ``venue.operate``; reads accept ``venue.view`` too. VMs additionally
    need a roster entry for that venue on that date, and to have clocked in there that day.
    """
    needed = ("venue.operate",) if write else ("venue.operate", "venue.view")
    if not any(can(user, p) for p in needed):
        raise HTTPException(403, "You have view-only access here." if can(user, "venue.view")
                            else "You don't have access to this venue.")
    if roster_scoped(user) and not rostered(db, user, venue_id, d):
        from .models import Venue
        v = db.get(Venue, venue_id)
        raise HTTPException(403, f"You're not rostered for {v.name if v else 'this venue'} on "
                                 f"{d.strftime('%a %d %b')}. Ask HQ to add you to the roster.")
    if roster_scoped(user):
        from .services import attendance
        if not attendance.unlocked(db, user, venue_id, d):
            # 428: the client shows the clock-in screen instead of the venue.
            raise HTTPException(428, "Clock in at the venue to open it for the day.")


def roster_pairs(db: Session, user, start: date, end: date) -> set[tuple[int, date]]:
    from .models import VmRoster
    return {(r.venue_id, r.date) for r in db.scalars(select(VmRoster).where(
        VmRoster.user_id == user.id, VmRoster.date >= start, VmRoster.date <= end))}


def for_user(user) -> dict:
    return {"role": user.role, "role_label": ROLES.get(user.role, user.role),
            "permissions": sorted(ROLE_PERMISSIONS.get(user.role, set()))}
