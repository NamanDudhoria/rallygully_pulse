"""Venue configuration: create/edit venues, facilities, hours, special dates, pricing — without stranding bookings."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.auth import hash_password, reset_throttle
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Facility, PricingRule, User, Venue, WeeklyHours

DAY = date(2026, 10, 7)  # Wednesday — "today" in these tests
TMRW = DAY + timedelta(days=1)


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    db.add(v)
    db.flush()
    for wd in range(7):
        db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
    db.add_all([Facility(venue_id=v.id, name="Court 1", facility_type="Court", sport="Pickleball"),
                Facility(venue_id=v.id, name="Court 2", facility_type="Court", sport="Pickleball")])
    db.add(PricingRule(venue_id=v.id, sport="Pickleball", start_min=0, end_min=1440, duration_min=60, amount=800,
                       effective_from=date(2026, 1, 1)))
    pw = hash_password("pulse1234")
    db.add_all([User(name="Admin", email="admin@x.com", password_hash=pw, role="hq_admin"),
                User(name="HQ", email="hq@x.com", password_hash=pw, role="hq"),
                User(name="Comm", email="comm@x.com", password_hash=pw, role="community")])
    db.commit()
    db.close()
    timeutil.set_clock(datetime.combine(DAY, datetime.min.time()) + timedelta(hours=12))
    reset_throttle()
    yield TestClient(app)
    timeutil.set_clock(None)


def login(c, email="admin@x.com"):
    return {"Authorization": "Bearer " + c.post("/api/auth/login", json={"email": email, "password": "pulse1234"}).json()["token"]}


def book(c, h, d=TMRW, facility=1, start="19:00", amount=None):
    r = c.post("/api/bookings/direct", headers=h, json={"venue_id": 1, "facility_id": facility, "date": d.isoformat(),
                                                        "start": start, "duration": 60, "phone": "9811111111", "name": "A",
                                                        "amount": amount})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_and_edit_venue(c):
    h = login(c)
    v = c.post("/api/venues", headers=h, json={"code": "skt", "name": "Saket", "location": "Delhi"}).json()
    assert v["code"] == "SKT" and len(v["weekly_hours"]) == 7
    assert c.post("/api/venues", headers=h, json={"code": "SKT", "name": "Dup"}).status_code == 409
    r = c.put(f"/api/venues/{v['id']}", headers=h, json={"code": "SKT2", "name": "Saket Arena", "location": "South Delhi"})
    assert r.json()["name"] == "Saket Arena" and r.json()["code"] == "SKT2"
    assert c.put(f"/api/venues/{v['id']}", headers=h, json={"code": "EOK", "name": "x"}).status_code == 409


def test_venue_deactivation_blocked_by_future_bookings(c):
    h = login(c)
    b = book(c, h)
    r = c.put("/api/venues/1", headers=h, json={"code": "EOK", "name": "East of Kailash", "status": "inactive"})
    assert r.status_code == 409 and "strand 1" in r.json()["detail"]
    c.post(f"/api/bookings/{b['id']}/cancel", headers=h, json={"reason": "venue_closed"})
    assert c.put("/api/venues/1", headers=h, json={"code": "EOK", "name": "East of Kailash",
                                                   "status": "inactive"}).status_code == 200


def test_facility_add_rename_and_guarded_changes(c):
    h = login(c)
    f = c.post("/api/facilities", headers=h, json={"venue_id": 1, "name": "Court 3", "sport": "Padel"}).json()
    assert c.post("/api/facilities", headers=h, json={"venue_id": 1, "name": "Court 3", "sport": "Padel"}).status_code == 409
    book(c, h, facility=f["id"], amount=1600)  # no Padel price configured yet
    base = {"venue_id": 1, "name": "Padel Court", "facility_type": "Court", "sport": "Padel"}
    assert c.put(f"/api/facilities/{f['id']}", headers=h, json=base).json()["name"] == "Padel Court"  # rename is safe
    r = c.put(f"/api/facilities/{f['id']}", headers=h, json={**base, "status": "inactive"})
    assert r.status_code == 409 and "Deactivating Padel Court" in r.json()["detail"]
    assert c.put(f"/api/facilities/{f['id']}", headers=h, json={**base, "sport": "Pickleball"}).status_code == 409
    assert c.put("/api/facilities/2", headers=h, json={**base, "name": "Court 2", "sport": "Pickleball",
                                                       "status": "inactive"}).status_code == 200  # no bookings


def test_hours_change_cannot_strand_bookings(c):
    h = login(c)
    book(c, h, start="21:00")  # Thursday 9–10 PM
    hours = [{"weekday": TMRW.weekday(), "open": "17:00", "close": "21:00"}]
    r = c.put("/api/venues/1/hours", headers=h, json=hours)
    assert r.status_code == 409 and "Thursday" in r.json()["detail"]
    ok = [{"weekday": TMRW.weekday(), "open": "16:00", "close": "23:00"}]
    assert c.put("/api/venues/1/hours", headers=h, json=ok).status_code == 200


def test_special_dates_guarded(c):
    h = login(c)
    book(c, h)
    r = c.post("/api/venues/1/special-dates", headers=h, json={"date": TMRW.isoformat(), "closed": True, "note": "Diwali"})
    assert r.status_code == 409
    sid = c.post("/api/venues/1/special-dates", headers=h, json={"date": (TMRW + timedelta(days=1)).isoformat(),
                                                                 "closed": True}).json()["id"]
    assert c.delete(f"/api/special-dates/{sid}", headers=h).status_code == 200


def test_price_change_is_versioned(c):
    h = login(c)
    b = book(c, h, d=DAY + timedelta(days=10))
    rules = c.get("/api/pricing?venue_id=1", headers=h).json()
    assert rules[0]["status"] == "current"
    assert c.delete(f"/api/pricing/{rules[0]['id']}", headers=h).status_code == 409  # in use → kept for history
    assert c.post(f"/api/pricing/{rules[0]['id']}/change", headers=h,
                  json={"amount": 900, "effective_from": (DAY - timedelta(days=1)).isoformat()}).status_code == 422
    new = c.post(f"/api/pricing/{rules[0]['id']}/change", headers=h,
                 json={"amount": 900, "effective_from": (DAY + timedelta(days=3)).isoformat()}).json()
    statuses = {r["id"]: r["status"] for r in c.get("/api/pricing?venue_id=1", headers=h).json()}
    assert statuses == {rules[0]["id"]: "current", new["id"]: "scheduled"}
    assert c.get(f"/api/bookings/{b['id']}", headers=h).json()["booking_value"] == 800  # existing booking unchanged
    assert c.get(f"/api/venues/1/quote?facility_id=1&date={DAY + timedelta(days=4)}&start=19:00", headers=h).json()["amount"] == 800
    assert c.delete(f"/api/pricing/{new['id']}", headers=h).status_code == 200  # scheduled → removable


def test_config_permissions(c):
    assert c.post("/api/venues", headers=login(c, "hq@x.com"), json={"code": "N1", "name": "N"}).status_code == 200
    comm = login(c, "comm@x.com")
    assert c.post("/api/venues", headers=comm, json={"code": "N2", "name": "N"}).status_code == 403
    assert c.put("/api/facilities/1", headers=comm, json={"venue_id": 1, "name": "x", "sport": "y"}).status_code == 403
