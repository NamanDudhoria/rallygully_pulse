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
