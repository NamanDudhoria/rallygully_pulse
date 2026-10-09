from datetime import date, datetime, timedelta

from . import config

_offset = timedelta(0)


def now_local() -> datetime:
    """Naive venue-local wall-clock time (IST). Tests may shift it via set_clock()."""
    return datetime.now(config.TZ).replace(tzinfo=None) + _offset


def set_clock(at: datetime | None) -> None:
    """Pin 'now' to ``at`` (tests / demos). Pass None to restore real time."""
    global _offset
    _offset = timedelta(0) if at is None else at - datetime.now(config.TZ).replace(tzinfo=None)


def today() -> date:
    return now_local().date()


def minute_of(dt: datetime, on: date) -> int:
    """Minutes from midnight of ``on`` to ``dt`` (negative before, >1440 after)."""
    return int((dt - datetime.combine(on, datetime.min.time())).total_seconds() // 60)


def at_minute(on: date, minute: int) -> datetime:
    return datetime.combine(on, datetime.min.time()) + timedelta(minutes=minute)


def hhmm(minute: int) -> str:
    minute %= 1440
    return f"{minute // 60:02d}:{minute % 60:02d}"


def parse_hhmm(value: str) -> int:
    h, m = value.strip().split(":")[:2]
    return int(h) * 60 + int(m)


def on_boundary(minute: int) -> bool:
    return minute % config.SLOT_MINUTES == 0


def ceil_slot(minute: int) -> int:
    s = config.SLOT_MINUTES
    return -(-minute // s) * s
