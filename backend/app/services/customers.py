import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..audit import DomainError
from ..models import Customer


def normalize_phone(raw: str) -> str:
    """Canonical Indian mobile: 10 digits. Accepts +91 / 0 prefixes and separators."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    if len(digits) != 10 or digits[0] not in "6789":
        raise DomainError("Enter a valid 10-digit mobile number.", 422)
    return digits


def find_by_phone(db: Session, phone: str) -> Customer | None:
    return db.scalar(select(Customer).where(Customer.phone == normalize_phone(phone)))


def get_or_create(db: Session, phone: str, name: str | None = None) -> tuple[Customer, bool]:
    """Phone is the identity. A name, once set, is locked (PRD §45.1)."""
    phone = normalize_phone(phone)
    c = db.scalar(select(Customer).where(Customer.phone == phone))
    created = False
    if not c:
        c = Customer(phone=phone)
        db.add(c)
        created = True
    name = (name or "").strip()
    if name and not c.name_locked:
        c.name = name
        c.name_locked = True
    db.flush()
    return c, created
