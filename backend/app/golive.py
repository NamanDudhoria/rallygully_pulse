"""Clear the demo data before real use, keeping people.

    python -m app.golive                 # dry run: shows what would be removed
    python -m app.golive --confirm [--history ..\\Historic_data_ingestion] [--bookings ..\\online_bookings]

Keeps: users, their logins/invites and venue teams; venues and their facilities.
Removes: every demo booking, customer, payment, refund, community game, academy series, corporate
event, block, roster entry, day close, notification and audit entry — and the placeholder weekly
hours, special dates and prices the demo invented (set the real ones under Configuration).
A copy of the database is saved next to it first.
"""
import argparse
import shutil
from datetime import datetime

from sqlalchemy import func, select, text

from . import config
from .db import SessionLocal, engine, ensure_schema
from .models import (AcademyOccurrence, AcademyReconciliation, AcademySeries, Allocation, AuditEvent, Booking,
                     CommunityGame, CorporateEvent, Customer, DayClose, IngestionRun, Notification, OperationalBlock,
                     Participant, Payment, PricingRule, Refund, RosterAlert, SpecialDate, User, Venue, VmRoster,
                     WeeklyHours)

# Children before parents.
DEMO_TABLES = [Participant, CommunityGame, Refund, Payment, Allocation, Booking, AcademyReconciliation,
               AcademyOccurrence, AcademySeries, CorporateEvent, OperationalBlock, DayClose, Notification,
               IngestionRun, RosterAlert, VmRoster, Customer, PricingRule, SpecialDate, WeeklyHours]


def counts(db) -> dict:
    out = {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in DEMO_TABLES}
    out["audit_events (non-user)"] = db.scalar(select(func.count()).select_from(AuditEvent).where(
        ~AuditEvent.action.like("user.%")))
    return out


def backup() -> str | None:
    url = config.DATABASE_URL
    if not url.startswith("sqlite:///"):
        return None
    path = url.replace("sqlite:///", "", 1)
    dest = f"{path}.backup-{datetime.now():%Y%m%d-%H%M%S}"
    with engine.connect() as c:  # fold the WAL into the main file so the copy is complete
        c.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(path, dest)
    return dest


def clear(db) -> None:
    if engine.dialect.name == "sqlite":
        db.execute(text("PRAGMA foreign_keys=OFF"))
    for m in DEMO_TABLES:
        db.query(m).delete(synchronize_session=False)
    db.query(AuditEvent).filter(~AuditEvent.action.like("user.%")).delete(synchronize_session=False)
    db.commit()
    if engine.dialect.name == "sqlite":
        db.execute(text("PRAGMA foreign_keys=ON"))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--confirm", action="store_true", help="actually delete (otherwise a dry run)")
    ap.add_argument("--history", help="folder of historical sales workbooks to import afterwards")
    ap.add_argument("--bookings", help="folder of platform booking exports (Hudle, Playo, Khelomore, District) "
                                       "to import after the sales history: occupancy, bookings, customers")
    a = ap.parse_args()
    ensure_schema()
    db = SessionLocal()
    try:
        before = counts(db)
        print("Demo data:", {k: v for k, v in before.items() if v})
        print(f"Kept: {db.scalar(select(func.count()).select_from(User))} users, "
              f"{db.scalar(select(func.count()).select_from(Venue))} venues (with facilities)")
        if not a.confirm:
            print("Dry run — nothing changed. Re-run with --confirm to clear.")
            return
        dest = backup()
        if dest:
            print(f"Backup saved: {dest}")
        clear(db)
        print("Cleared:", {k: v for k, v in counts(db).items() if v} or "all demo tables are empty")
    finally:
        db.close()
    if a.history:
        from .history.ingest import run_ingest
        report = "history_report.md"
        print("History import:", run_ingest(a.history, report))
        print(f"Report written to {report}")
    if a.bookings:
        from .history.bookings_ingest import run as run_bookings
        report = "bookings_report.md"
        out = run_bookings(a.bookings, report)
        print("Bookings import:", {k: v for k, v in out.items() if k != "skipped"})
        print(f"Report written to {report}")
    print("Next: set each venue's opening hours and prices under Configuration before taking bookings.")


if __name__ == "__main__":
    main()
