"""Sign-in, profile, password reset / invite acceptance, password change."""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit, config, serialize
from ..auth import (check_password_policy, check_throttle, clear_failures, create_auth_token, current_user,
                    hash_password, issue_token, link_for, lookup_auth_token, record_failure, verify_password)
from ..db import get_db
from ..models import User, Venue, VmRoster
from ..permissions import for_user
from ..services import notify, roster
from ..timeutil import now_local

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class EmailIn(BaseModel):
    email: str


class SetPasswordIn(BaseModel):
    token: str
    password: str


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def _ip(request: Request) -> str:
    return request.client.host if request.client else "?"


def profile(db: Session, u: User) -> dict:
    out = {**serialize.user(u), **for_user(u)}
    if u.role == "vm":
        today = now_local().date()
        rows = db.execute(select(VmRoster, Venue.name).join(Venue, Venue.id == VmRoster.venue_id)
                          .where(VmRoster.user_id == u.id, VmRoster.date >= today - timedelta(days=1),
                                 VmRoster.date <= today + timedelta(days=14)).order_by(VmRoster.date, Venue.id))
        out["roster"] = []
        for r, name in rows:
            w = roster.window(db, r)
            out["roster"].append({"date": r.date, "venue_id": r.venue_id, "venue": name,
                                  "start_min": w[0] if w else None, "end_min": w[1] if w else None})
    return out


@router.post("/login")
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    check_throttle(email, _ip(request))
    u = db.scalar(select(User).where(func.lower(User.email) == email))
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        record_failure(email, _ip(request))
        if u and u.active and not u.password_hash:
            raise HTTPException(401, "Your invite hasn't been accepted yet — use the link in your invite email.")
        raise HTTPException(401, "Incorrect email or password")
    clear_failures(email, _ip(request))
    u.last_login_at = now_local()
    db.commit()
    return {"token": issue_token(u), "user": profile(db, u)}


@router.get("/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return profile(db, user)


@router.post("/forgot")
def forgot(body: EmailIn, request: Request, db: Session = Depends(get_db)):
    """Always answers the same way, so it can't be used to discover who has an account."""
    email = body.email.strip().lower()
    check_throttle(email, _ip(request))
    record_failure(email, _ip(request))  # counts toward the throttle to stop email flooding
    u = db.scalar(select(User).where(func.lower(User.email) == email))
    if u and u.active:
        purpose = "reset" if u.password_hash else "invite"
        raw = create_auth_token(db, u, purpose)
        send_link_email(db, u, purpose, raw)
        db.commit()
    return {"ok": True, "message": "If that email has a Pulse account, a link is on its way."}


def send_link_email(db: Session, u: User, purpose: str, raw: str) -> None:
    link = link_for(raw)
    if purpose == "invite":
        subject = "You've been invited to RallyGully Pulse"
        body = (f"Hi {u.name},\n\nYou've been added to RallyGully Pulse. Set your password here "
                f"(valid for 72 hours):\n" + "{link}" + f"\n\nSign in with {u.email}.")
    else:
        subject = "Reset your RallyGully Pulse password"
        body = (f"Hi {u.name},\n\nUse this link to set a new password (valid for 1 hour):\n" + "{link}" + "\n\n"
                "If you didn't ask for this, ignore this email — your password stays the same.")
    notify.email(db, u.email, subject, body.replace("{link}", link), "user", u.id, template=f"user_{purpose}",
                 log_body=body.replace("{link}", "[link withheld from logs]"))


@router.get("/token")
def token_info(token: str, db: Session = Depends(get_db)):
    t = lookup_auth_token(db, token)
    if not t:
        return {"valid": False}
    u = db.get(User, t.user_id)
    return {"valid": bool(u and u.active), "purpose": t.purpose, "email": u.email if u else None,
            "name": u.name if u else None}


@router.post("/set-password")
def set_password(body: SetPasswordIn, db: Session = Depends(get_db)):
    t = lookup_auth_token(db, body.token)
    u = db.get(User, t.user_id) if t else None
    if not t or not u or not u.active:
        raise HTTPException(400, "This link is invalid or has expired. Ask for a new one.")
    check_password_policy(body.password, u.email)
    u.password_hash = hash_password(body.password)
    u.token_version = (u.token_version or 0) + 1  # sign out everywhere else
    u.last_login_at = now_local()
    t.used_at = now_local()
    clear_failures(u.email, "-")
    audit.record(db, u, f"user.{'invite_accepted' if t.purpose == 'invite' else 'password_reset'}", "user", u.id)
    db.commit()
    return {"token": issue_token(u), "user": profile(db, u)}


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    check_throttle(user.email, _ip(request))
    if not verify_password(body.current_password, user.password_hash):
        record_failure(user.email, _ip(request))
        raise HTTPException(400, "Current password is incorrect.")
    check_password_policy(body.new_password, user.email)
    user.password_hash = hash_password(body.new_password)
    user.token_version = (user.token_version or 0) + 1
    audit.record(db, user, "user.password_changed", "user", user.id)
    db.commit()
    return {"token": issue_token(user), "user": profile(db, user)}


@router.post("/logout-everywhere")
def logout_everywhere(user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.token_version = (user.token_version or 0) + 1
    db.commit()
    return {"ok": True}


def smtp_configured() -> bool:
    return bool(config.SMTP_HOST)
