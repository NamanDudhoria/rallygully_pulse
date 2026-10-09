"""Password hashing, signed bearer tokens, and role/venue-scope guards."""
import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def issue_token(user: User) -> str:
    payload = _b64(json.dumps({"uid": user.id, "exp": int(time.time()) + config.TOKEN_TTL_HOURS * 3600}).encode())
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _decode(token: str) -> int | None:
    try:
        payload, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if data["exp"] < time.time():
        return None
    return data["uid"]


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    uid = _decode(header[7:]) if header.lower().startswith("bearer ") else None
    user = db.get(User, uid) if uid else None
    if not user or not user.active:
        raise HTTPException(401, "Not authenticated")
    return user


def require_hq(user: User = Depends(current_user)) -> User:
    if user.role != "hq":
        raise HTTPException(403, "HQ access required")
    return user


def venue_ids_for(user: User) -> set[int] | None:
    """None means unrestricted (HQ)."""
    return None if user.role == "hq" else {a.venue_id for a in user.venues}


def ensure_venue_access(user: User, venue_id: int) -> None:
    allowed = venue_ids_for(user)
    if allowed is not None and venue_id not in allowed:
        raise HTTPException(403, "You do not have access to this venue")
