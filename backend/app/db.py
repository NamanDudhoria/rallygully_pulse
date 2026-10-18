import threading

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

if config.DATABASE_URL.startswith("sqlite"):
    engine = create_engine(config.DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # prepare_threshold=None: no server-side prepared statements, so a transaction pooler (Supabase, PgBouncer) works.
    engine = create_engine(config.DATABASE_URL, connect_args={"prepare_threshold": None}, pool_pre_ping=True,
                           pool_size=5, max_overflow=5, pool_recycle=1800)

if config.DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

# Serializes inventory check-then-write within this process. On Postgres, pair this
# with an exclusion constraint on (facility_id, date, int4range(start_min, end_min)).
inventory_lock = threading.RLock()


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema() -> None:
    """Create missing tables, then add any new nullable columns to existing ones.

    Lightweight stand-in for migrations so an existing local database picks up
    additive schema changes without a reseed. Only nullable columns are added.
    """
    from sqlalchemy import inspect, text

    from . import models  # noqa: F401 — register every table on Base.metadata

    Base.metadata.create_all(engine)
    if engine.dialect.name == "postgresql":
        _lock_down_postgres()
    insp = inspect(engine)
    if engine.dialect.name == "sqlite":
        for table in Base.metadata.sorted_tables:
            db_cols = {c["name"]: c for c in insp.get_columns(table.name)}
            if any(col.nullable and col.name in db_cols and not db_cols[col.name]["nullable"] for col in table.columns):
                _sqlite_rebuild(table, [c for c in table.columns if c.name in db_cols])
        insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            have = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in have and col.nullable:
                    conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" '
                                      f'{col.type.compile(engine.dialect)}'))


def _lock_down_postgres() -> None:
    """Turn on row-level security for every table. Pulse reaches the database as its owner, which bypasses it,
    so nothing changes for the app; but on Supabase it stops the public Data API (anon / authenticated keys) from
    reading or writing these tables. Best effort: a role that doesn't own a table can't change it."""
    from sqlalchemy import text

    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            try:
                with conn.begin_nested():
                    conn.execute(text(f'ALTER TABLE "{table.name}" ENABLE ROW LEVEL SECURITY'))
            except Exception:  # noqa: BLE001
                pass


def _sqlite_rebuild(table, keep_cols) -> None:
    """SQLite can't relax NOT NULL in place: copy the table into the current definition."""
    from sqlalchemy import text

    cols = ", ".join(f'"{c.name}"' for c in keep_cols)
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.execute(text(f'ALTER TABLE "{table.name}" RENAME TO "_old_{table.name}"'))
        for idx in table.indexes:
            conn.execute(text(f'DROP INDEX IF EXISTS "{idx.name}"'))
        table.create(conn)
        conn.execute(text(f'INSERT INTO "{table.name}" ({cols}) SELECT {cols} FROM "_old_{table.name}"'))
        conn.execute(text(f'DROP TABLE "_old_{table.name}"'))
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
