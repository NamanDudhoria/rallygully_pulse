from datetime import date, datetime

from sqlalchemy.orm import Session

from .models import AuditEvent, User


def _clean(value):
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


def record(db: Session, actor: User | None, action: str, entity: str, entity_id: int | None,
           before: dict | None = None, after: dict | None = None, venue_id: int | None = None) -> None:
    db.add(AuditEvent(
        actor_id=actor.id if actor else None,
        actor_name=actor.name if actor else "System",
        venue_id=venue_id, entity=entity, entity_id=entity_id, action=action,
        before=_clean(before) if before else None, after=_clean(after) if after else None,
    ))


class DomainError(Exception):
    """A business-rule violation; surfaced to clients as HTTP 409/422."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status
