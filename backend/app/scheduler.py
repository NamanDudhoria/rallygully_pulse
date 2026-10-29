"""Background jobs: District ingestion (every 5 min), payment overdue, missed day-close escalation,
the evening-before alert for venues with no VM rostered tomorrow, and each VM's WhatsApp for tomorrow at 11:30 PM."""
import asyncio
import logging
import time

from . import config
from .db import SessionLocal
from .services import attendance, bookings, dayclose, district, roster

log = logging.getLogger("pulse.scheduler")


def _tick(run_district: bool) -> None:
    db = SessionLocal()
    try:
        if run_district:
            run = district.run_ingestion(db)
            if run.messages:
                log.info("district ingestion: %s msgs, %s created, %s dup, %s errors",
                         run.messages, run.created, run.duplicates, len(run.errors or []))
        bookings.refresh_overdue(db)
        dayclose.escalate_missed(db)
        roster.alert_gaps(db)
        roster.send_tomorrow(db)
        attendance.retry_photos(db)
        attendance.alert_missing_checkin(db)
        db.commit()
    except Exception:  # noqa: BLE001 — never let a job crash the loop; operational records stay intact
        db.rollback()
        log.exception("scheduler tick failed")
    finally:
        db.close()


async def loop() -> None:
    last_district = 0.0
    while True:
        due = time.monotonic() - last_district >= config.DISTRICT_POLL_SECONDS
        if due:
            last_district = time.monotonic()
        await asyncio.to_thread(_tick, due)
        await asyncio.sleep(60)
