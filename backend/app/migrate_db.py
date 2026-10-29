"""Copy every Pulse table from this server's database (PULSE_DATABASE_URL) into another, empty database.

Stop Pulse first, so nothing is written during the copy, then run as the service user:

    sudo systemctl stop pulse
    sudo -u pulse bash -c 'set -a; . /opt/pulse/backend/.env.pulse; set +a; \\
        cd /opt/pulse/backend && .venv/bin/python -m app.migrate_db'

It asks for the new database's connection string (typed hidden, so it stays out of the shell history), creates
the tables there, copies every row in an order that respects the links between tables, resets the id counters,
and checks each table's row count matches. Everything is copied in one transaction: if anything fails, the new
database is left without data. The current database is only read, never changed.
"""
import getpass
import os
import sys

from sqlalchemy import create_engine, func, inspect, select, text

from . import models  # noqa: F401 (registers every table on Base.metadata)
from .db import Base
from .db import engine as source

BATCH = 1000


def _normalise(url: str) -> str:
    url = url.strip()
    for plain in ("postgres://", "postgresql://"):
        if url.startswith(plain):
            return "postgresql+psycopg://" + url[len(plain):]
    return url


def _engine(url: str):
    if url.startswith("sqlite"):
        return create_engine(url)
    return create_engine(url, connect_args={"prepare_threshold": None})


def copy(target_url: str, out=print) -> dict[str, int]:
    target_url = _normalise(target_url)
    if "<" in target_url or ">" in target_url:
        raise SystemExit("That looks like a placeholder: paste the real connection string.")
    if target_url == source.url.render_as_string(hide_password=False):
        raise SystemExit("That is the database Pulse is using now. Give the new one.")
    target = _engine(target_url)
    out(f"From: {source.url.render_as_string(hide_password=True)}")
    out(f"To:   {target.url.render_as_string(hide_password=True)}")

    Base.metadata.create_all(target)
    tables = Base.metadata.sorted_tables  # parents before children
    with target.connect() as c:
        busy = [t.name for t in tables if c.execute(select(func.count()).select_from(t)).scalar()]
    if busy:
        raise SystemExit(f"The new database already has data (in {', '.join(busy[:5])}). Use an empty database.")

    src_insp = inspect(source)
    have_tables = set(src_insp.get_table_names())
    counts: dict[str, int] = {}
    with source.connect() as s, target.begin() as t:
        for table in tables:
            if table.name not in have_tables:
                counts[table.name] = 0
                continue
            src_cols = {c["name"] for c in src_insp.get_columns(table.name)}
            cols = [c for c in table.columns if c.name in src_cols]  # a column the old database never had stays empty
            n = 0
            result = s.execution_options(stream_results=True).execute(select(*cols))
            while rows := result.fetchmany(BATCH):
                t.execute(table.insert(), [dict(r._mapping) for r in rows])
                n += len(rows)
            counts[table.name] = n
            out(f"  {table.name:<28} {n:>8}")
        if target.dialect.name == "postgresql":
            for table in tables:
                pk = list(table.primary_key.columns)
                if len(pk) == 1 and pk[0].name == "id" and pk[0].autoincrement in (True, "auto"):
                    t.execute(text(f"SELECT setval(pg_get_serial_sequence('\"{table.name}\"', 'id'), "
                                   f"COALESCE((SELECT MAX(id) FROM \"{table.name}\"), 1), "
                                   f"(SELECT MAX(id) FROM \"{table.name}\") IS NOT NULL)"))
            for table in tables:  # Pulse talks to the database directly; keep Supabase's public API locked out
                t.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))

    with target.connect() as c:
        wrong = [(n, counts[n], c.execute(select(func.count()).select_from(tb)).scalar())
                 for tb in tables for n in [tb.name]
                 if c.execute(select(func.count()).select_from(tb)).scalar() != counts[n]]
    if wrong:
        raise SystemExit("Row counts don't match: " + ", ".join(f"{n} {a}→{b}" for n, a, b in wrong))
    out(f"Done: {sum(counts.values())} rows in {len(tables)} tables copied and checked.")
    return counts


def main() -> None:
    url = os.getenv("PULSE_TARGET_DATABASE_URL") or getpass.getpass("New database connection string (hidden): ")
    if not url.strip():
        sys.exit("No connection string given.")
    copy(url)


if __name__ == "__main__":
    main()
