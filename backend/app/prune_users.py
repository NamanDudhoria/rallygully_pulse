"""Delete every user except the ones you keep (default: the HQ admin), before going live.

    python -m app.prune_users                                  # dry run: lists who would go
    python -m app.prune_users --confirm [--keep admin@rallygully.com] [--reset-password]

Rows that belong to a removed user (invites, venue-team links, roster entries) are deleted. Records that merely
point at one (who processed a refund, who closed a day) keep existing with the reference cleared; the audit trail
is untouched and keeps the person's name. A copy of the database is saved first.
"""
import argparse
import getpass

from sqlalchemy import delete, func, select, update

from .auth import hash_password
from .db import SessionLocal, engine, ensure_schema
from .golive import backup
from .models import Base, User

DEFAULT_KEEP = "admin@rallygully.com"


def user_refs():
    """(table, column) for every foreign key that points at users.id."""
    for table in Base.metadata.sorted_tables:
        for col in table.columns:
            if any(fk.column.table.name == "users" for fk in col.foreign_keys) and table.name != "users":
                yield table, col


def prune(db, keep_emails: list[str]) -> dict:
    keep = [e.strip().lower() for e in keep_emails]
    kept = db.scalars(select(User).where(func.lower(User.email).in_(keep))).all()
    missing = set(keep) - {u.email.lower() for u in kept}
    if missing:
        raise SystemExit(f"No such user to keep: {', '.join(sorted(missing))}")
    if not any(u.role == "hq_admin" and u.active for u in kept):
        raise SystemExit("Refusing: the users you keep must include an active HQ admin, or nobody could sign in.")
    doomed = [u.id for u in db.scalars(select(User).where(User.id.notin_([u.id for u in kept])))]
    out = {"removed": len(doomed), "kept": [u.email for u in kept], "deleted_rows": {}, "cleared_refs": {}}
    if not doomed:
        return out
    for table, col in user_refs():
        if col.nullable and not col.primary_key:
            n = db.execute(update(table).where(col.in_(doomed)).values({col.name: None})).rowcount
            if n:
                out["cleared_refs"][f"{table.name}.{col.name}"] = n
        else:
            n = db.execute(delete(table).where(col.in_(doomed))).rowcount
            if n:
                out["deleted_rows"][table.name] = n
    db.execute(delete(User).where(User.id.in_(doomed)))
    db.commit()
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--keep", action="append", help=f"email to keep (repeatable; default {DEFAULT_KEEP})")
    ap.add_argument("--confirm", action="store_true", help="actually delete (otherwise a dry run)")
    ap.add_argument("--reset-password", action="store_true", help="set a new password for the kept admin (prompted)")
    a = ap.parse_args()
    keep = a.keep or [DEFAULT_KEEP]
    ensure_schema()
    db = SessionLocal()
    try:
        everyone = db.scalars(select(User).order_by(User.id)).all()
        for u in everyone:
            print(f"  {'KEEP  ' if u.email.lower() in [k.lower() for k in keep] else 'DELETE'} {u.email:32} {u.role}")
        if not a.confirm:
            print("Dry run — nothing changed. Re-run with --confirm to delete.")
            return
        password = None
        if a.reset_password:
            password = getpass.getpass("New password for the HQ admin (min 10 characters): ")
            if len(password) < 10 or password != getpass.getpass("Repeat: "):
                raise SystemExit("Password too short or not matching — nothing changed.")
        dest = backup()
        if dest:
            print(f"Backup saved: {dest}")
        print(prune(db, keep))
        if password:
            admin = db.scalar(select(User).where(func.lower(User.email) == keep[0].lower()))
            admin.password_hash = hash_password(password)
            admin.token_version = (admin.token_version or 0) + 1  # signs out every earlier session
            db.commit()
            print(f"Password changed for {admin.email}.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
