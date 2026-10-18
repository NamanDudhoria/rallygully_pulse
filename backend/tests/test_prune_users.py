"""Pruning demo users keeps the HQ admin and removes the rows that belong to the others."""
import pytest
from sqlalchemy import select

from app.db import Base, SessionLocal, engine
from app.models import AuditEvent, AuthToken, User, Venue, VmAssignment
from app.prune_users import prune


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    s.add(v)
    admin = User(name="Admin", email="admin@rallygully.com", password_hash="x", role="hq_admin")
    vm = User(name="Gaurav", email="gaurav@rallygully.com", password_hash="x", role="vm")
    s.add_all([admin, vm])
    s.flush()
    s.add(VmAssignment(user_id=vm.id, venue_id=v.id))
    s.add(AuthToken(user_id=vm.id, token_hash="h", purpose="invite", expires_at=__import__("datetime").datetime(2030, 1, 1)))
    s.add(AuditEvent(actor_id=vm.id, actor_name="Gaurav", action="booking.create", entity="booking", entity_id=1))
    s.commit()
    yield s
    s.close()


def test_prune_keeps_admin_and_clears_references(db):
    out = prune(db, ["ADMIN@rallygully.com"])
    assert out["removed"] == 1 and [u.email for u in db.scalars(select(User))] == ["admin@rallygully.com"]
    assert db.query(VmAssignment).count() == 0 and db.query(AuthToken).count() == 0
    assert db.query(AuditEvent).one().actor_name == "Gaurav"  # the audit trail is left as it was


def test_prune_refuses_without_an_admin(db):
    with pytest.raises(SystemExit):
        prune(db, ["gaurav@rallygully.com"])
    assert db.query(User).count() == 2


def test_plain_postgres_urls_get_the_driver():
    import subprocess
    import sys

    for given in ("postgresql://u:p@h:5432/db", "postgres://u:p@h:5432/db"):
        out = subprocess.run([sys.executable, "-c", "from app import config; print(config.DATABASE_URL)"],
                             env={"PULSE_DATABASE_URL": given, "PATH": ""}, capture_output=True, text=True,
                             cwd=__import__("os").path.dirname(__import__("os").path.dirname(__file__)))
        assert out.stdout.strip() == "postgresql+psycopg://u:p@h:5432/db", out.stderr
