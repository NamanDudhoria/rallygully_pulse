"""Password hashing, signed session tokens, login throttling, and invite/reset tokens.

Sessions are HMAC-signed and carry the user's ``token_version``; bumping it (password
change, deactivation) invalidates every session the user holds.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from collections import defaultdict, deque
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import AuthToken, User
from .timeutil import now_local

PASSWORD_MIN, PASSWORD_MAX = 8, 128
INVITE_TTL = timedelta(hours=72)
RESET_TTL = timedelta(hours=1)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)
    return f"pbkdf2${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        return False
    try:
        _, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(digest.hex(), digest_hex)


def check_password_policy(password: str, email: str | None = None) -> None:
    """NIST 800-63B style: length over composition rules; reject the obvious."""
    if not PASSWORD_MIN <= len(password or "") <= PASSWORD_MAX:
        raise HTTPException(422, f"Password must be {PASSWORD_MIN}–{PASSWORD_MAX} characters.")
    lowered = password.lower()
    if email and lowered == email.lower().split("@")[0]:
        raise HTTPException(422, "Password can't be your email name.")
    if lowered in {"password", "password1", "12345678", "123456789", "qwerty123", "rallygully", "pulse123"}:
        raise HTTPException(422, "That password is too common.")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def issue_token(user: User) -> str:
    payload = _b64(json.dumps({"uid": user.id, "tv": user.token_version or 0,
                               "exp": int(time.time()) + config.TOKEN_TTL_HOURS * 3600}).encode())
    sig = _b64(hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def _decode(token: str) -> dict | None:
    try:
        payload, sig = token.split(".")
    except ValueError:
        return None
    expected = _b64(hmac.new(config.SECRET_KEY.encode(), payload.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    return data if data.get("exp", 0) >= time.time() else None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    header = request.headers.get("authorization", "")
    data = _decode(header[7:]) if header.lower().startswith("bearer ") else None
    user = db.get(User, data["uid"]) if data else None
    if not user or not user.active or data.get("tv", 0) != (user.token_version or 0):
        raise HTTPException(401, "Not authenticated")
    return user


# ── Login throttling (per email and per client IP, in-process) ───────────────
_FAIL_WINDOW = 15 * 60
_FAIL_LIMIT = 5
_failures: dict[str, deque] = defaultdict(deque)
_fail_lock = threading.Lock()


def _keys(email: str, ip: str) -> list[str]:
    return [f"e:{email.lower()}", f"i:{ip}"]


def check_throttle(email: str, ip: str) -> None:
    now = time.time()
    with _fail_lock:
        for k in _keys(email, ip):
            q = _failures[k]
            while q and now - q[0] > _FAIL_WINDOW:
                q.popleft()
            limit = _FAIL_LIMIT if k.startswith("e:") else _FAIL_LIMIT * 4
            if len(q) >= limit:
                wait = int(_FAIL_WINDOW - (now - q[0])) // 60 + 1
                raise HTTPException(429, f"Too many attempts. Try again in {wait} min or reset your password.")


def record_failure(email: str, ip: str) -> None:
    with _fail_lock:
        for k in _keys(email, ip):
            _failures[k].append(time.time())


def clear_failures(email: str, ip: str) -> None:
    with _fail_lock:
        for k in _keys(email, ip):
            _failures.pop(k, None)


def reset_throttle() -> None:  # tests
    with _fail_lock:
        _failures.clear()


# ── Invite / reset tokens ────────────────────────────────────────────────────
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_auth_token(db: Session, user: User, purpose: str) -> str:
    """Issue a single-use link token; older unused tokens of the same purpose are revoked."""
    now = now_local()
    for old in db.scalars(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose,
                                                  AuthToken.used_at.is_(None))):
        old.used_at = now
    raw = secrets.token_urlsafe(32)
    db.add(AuthToken(user_id=user.id, purpose=purpose, token_hash=_hash(raw),
                     expires_at=now + (INVITE_TTL if purpose == "invite" else RESET_TTL)))
    db.flush()
    return raw


def lookup_auth_token(db: Session, raw: str) -> AuthToken | None:
    t = db.scalar(select(AuthToken).where(AuthToken.token_hash == _hash(raw or "")))
    if not t or t.used_at or t.expires_at < now_local():
        return None
    return t


def link_for(raw: str) -> str:
    return f"{config.APP_URL.rstrip('/')}/set-password?token={raw}"
