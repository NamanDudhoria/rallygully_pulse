"""Pages must not run a query per booking / per venue: over a remote database each query costs a network trip."""
from datetime import date

import pytest
from sqlalchemy import event

from app.db import Base, SessionLocal, engine
from app.models import Booking, Customer, Venue
from app.services import dayclose

DAY = date(2026, 10, 7)


@pytest.fixture()
def db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    s = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    s.add(v)
    s.flush()
    for i in range(30):
        c = Customer(phone=f"+91900000{i:04d}", name=f"Player {i}")
        s.add(c)
        s.flush()
        s.add(Booking(source="direct", customer_id=c.id, venue_id=v.id, date=DAY, start_min=17 * 60 + 30 * (i % 8),
                      end_min=18 * 60 + 30 * (i % 8), booking_value=800, payment_status="to_collect",
                      payment_window_end_min=1440))
    s.commit()
    s.expire_all()
    yield s, v.id
    s.close()


def _count(s, fn):
    n = {"q": 0}

    def bump(*_a, **_k):
        n["q"] += 1
    event.listen(engine, "before_cursor_execute", bump)
    try:
        out = fn()
    finally:
        event.remove(engine, "before_cursor_execute", bump)
    return out, n["q"]


def test_day_close_check_loads_customers_in_bulk(db):
    s, vid = db
    items, queries = _count(s, lambda: dayclose.missing_items(s, vid, DAY))
    assert len(items) >= 30 and "Player 0" in items[0]["message"]  # every booking is named, in start-time order
    assert queries <= 4, f"{queries} queries for 30 bookings: customers are being fetched one by one again"
