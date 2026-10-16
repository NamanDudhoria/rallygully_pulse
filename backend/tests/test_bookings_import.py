"""Platform booking exports → bookings, court time and imputed offline play, without touching revenue."""
import csv
import shutil
from datetime import date

import pytest

from app.db import Base, SessionLocal, engine
from app.history.bookings_ingest import run
from app.models import Facility, HistBooking, HistBookingMonth, HistRevenue, HistSlot, HistSource, Venue

HEAD = ["Shortcode", "Order ID", "Order Status", "Action", "Slot Start Date", "Slot Start Time", "Slot End Time",
        "Venue", "Sport", "Arena / Court", "Booking Medium", "Total Amount", "Refund Amount", "Customer Name",
        "Booking Date"]


def _row(oid, day, start, end, amount, medium="Online", status="complete", action="sale", venue="RallyGully | East of Kailash",
         name="Asha Rao", court="Court 1"):
    return [oid, oid, status, action, day, start, end, venue, "Pickleball", court, medium, amount, "-", name, day]


@pytest.fixture()
def folder(tmp_path):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    db.add(v)
    db.flush()
    db.add_all([Facility(venue_id=v.id, name=f"Court {i}", facility_type="Court", sport="Pickleball") for i in (1, 2)])
    src = HistSource(file="EOK Sept.xlsx", sheet="Summary", content_hash="x", venue_id=v.id, venue_label="EOK",
                     year=2026, month=9, status="ingested")
    db.add(src)
    db.flush()
    # Sales file for Sept: District ₹2,000 and offline pay & play ₹4,000 (₹1,000 of it recorded below).
    for ch, g in (("district", 2000.0), ("offline_bookings", 4000.0), ("memberships", 9000.0)):
        db.add(HistRevenue(source_id=src.id, venue_id=v.id, year=2026, month=9, date_from=date(2026, 9, 1),
                           date_to=date(2026, 9, 30), basis="month", vertical="offline", channel=ch, gross=g,
                           sheet="Summary", row=1))
    db.commit()
    db.close()
    rows = [_row("A1", "2026-09-05", "18:00:00", "19:00:00", 1000),
            _row("A2", "2026-09-06", "19:00:00", "20:00:00", 1000, name="  asha   RAO "),       # same customer
            _row("B1", "2026-09-07", "20:00:00", "21:00:00", 1000, medium="Offline", name="Vik"),
            _row("C1", "2026-09-08", "18:00:00", "19:00:00", 1000, status="refund"),          # refunded: skipped
            _row("C1", "2026-09-08", "18:00:00", "19:00:00", 1000, action="refund"),          # its refund row
            _row("J1", "2026-09-08", "18:00:00", "19:00:00", 1000, venue="RallyGully | Jaipur")]  # excluded venue
    for name in ("district1.csv", "district1 (1).csv"):  # a re-downloaded copy must not double count
        with open(tmp_path / name, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(HEAD)
            w.writerows(rows)
    return tmp_path


def test_bookings_imported_once_and_revenue_untouched(folder):
    out = run(str(folder))
    db = SessionLocal()
    try:
        assert db.query(HistBooking).count() == 3
        assert {b.medium for b in db.query(HistBooking)} == {"online", "offline"}
        assert round(sum(r.gross for r in db.query(HistRevenue)), 2) == 15000.0  # revenue is never changed
        keys = {b.customer_key for b in db.query(HistBooking) if b.customer.lower().strip().startswith("asha")}
        assert len(keys) == 1  # same name, same venue → one customer
        assert out["skipped"].get("excluded venue (Jaipur, Ambala, …)") == 1
        assert out["skipped"].get("district: order refund") == 1
    finally:
        db.close()


def test_unexplained_offline_money_becomes_hours_within_capacity(folder):
    run(str(folder))
    db = SessionLocal()
    try:
        a = db.query(HistBookingMonth).one().data
        # ₹4,000 offline in the sales file − ₹1,000 recorded offline = ₹3,000 to place; District is fully explained.
        assert a["offline_to_place"] == 3000.0 and a["unexplained"] == {"offline_bookings": 3000.0}
        price = a["imputed"]["Pickleball"]["price_per_hour"]
        assert price == 1000.0  # from the online bookings: ₹1,000 an hour
        assert a["imputed"]["Pickleball"]["hours"] == 3.0 and a["unplaced_hours"] == 0
        slots = db.query(HistSlot).all()
        assert all(s.recorded_min + s.imputed_min <= s.capacity_min + 1e-6 for s in slots)
        assert round(sum(s.imputed_min for s in slots)) == 180
        assert round(sum(s.recorded_min for s in slots)) == 180
        assert {s.hour for s in slots if s.capacity_min} == {18, 19, 20}  # hours the venue actually ran
    finally:
        db.close()
