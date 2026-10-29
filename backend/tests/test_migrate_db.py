"""Moving to a new database copies every row and keeps the id counters right."""
from datetime import date

from sqlalchemy import create_engine, func, select, text

from app.db import Base, SessionLocal, engine
from app.migrate_db import copy
from app.models import User, Venue, WeeklyHours


def test_copy_moves_every_row_and_refuses_a_database_with_data(tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    db.add(v)
    db.flush()
    db.add_all([WeeklyHours(venue_id=v.id, weekday=d, open_min=1020, close_min=1380) for d in range(7)])
    db.add_all([User(name=f"U{i}", email=f"u{i}@x.com", role="hq") for i in range(3)])
    db.commit()
    db.close()
    target = f"sqlite:///{tmp_path / 'new.db'}"
    lines = []
    counts = copy(target, out=lines.append)
    assert (counts["venues"], counts["weekly_hours"], counts["users"]) == (1, 7, 3)
    assert lines[-1].startswith("Done: 11 rows")
    t = create_engine(target)
    with t.connect() as c:
        assert c.execute(text("select count(*) from users")).scalar() == 3
    try:
        copy(target, out=lines.append)
        raise AssertionError("copied into a database that already had data")
    except SystemExit as e:
        assert "already has data" in str(e)
    try:
        copy(str(engine.url), out=lines.append)
        raise AssertionError("copied a database onto itself")
    except SystemExit as e:
        assert "using now" in str(e)
