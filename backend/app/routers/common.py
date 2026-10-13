from datetime import date

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..timeutil import parse_hhmm, today


def get_or_404(db: Session, model, ident):
    obj = db.get(model, ident)
    if obj is None:
        raise HTTPException(404, f"{model.__name__} not found")
    return obj


def day_or_today(d: date | None) -> date:
    return d or today()


def minutes(value: str | int) -> int:
    if isinstance(value, int):
        return value
    try:
        return parse_hhmm(value)
    except (ValueError, AttributeError):
        raise HTTPException(422, f"Invalid time '{value}' (use HH:MM)") from None


def custom_window(cmp_from: date | None, cmp_to: date | None) -> tuple[date, date] | None:
    """Optional custom comparison range (both ends, up to a year)."""
    if cmp_from is None and cmp_to is None:
        return None
    if cmp_from is None or cmp_to is None or cmp_to < cmp_from or (cmp_to - cmp_from).days > 366:
        raise HTTPException(422, "Choose a custom comparison range of up to one year.")
    return cmp_from, cmp_to
