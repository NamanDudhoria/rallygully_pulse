"""Acceptance criteria (PRD §74) exercised through the HTTP API on an isolated database."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Facility, PricingRule, User, Venue, VmAssignment, WeeklyHours

DAY = date(2026, 10, 7)  # a Wednesday


def at(h, m=0, d=DAY):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m)


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    v = Venue(code="EOK", name="East of Kailash")
    other = Venue(code="SKT", name="Saket")
    db.add_all([v, other])
    db.flush()
    for wd in range(7):
        db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
        db.add(WeeklyHours(venue_id=other.id, weekday=wd, open_min=6 * 60, close_min=22 * 60))
    for i, sport in enumerate(["Pickleball", "Pickleball", "Padel"]):
        db.add(Facility(venue_id=v.id, name=f"Court {i + 1}", facility_type="Court", sport=sport, sort_order=i))
    db.add(Facility(venue_id=other.id, name="Court A", facility_type="Court", sport="Badminton"))
    db.add(PricingRule(venue_id=v.id, sport="Pickleball", weekdays="0123456", start_min=0, end_min=1440,
                       duration_min=60, amount=800, effective_from=date(2026, 1, 1)))
    db.add(PricingRule(venue_id=v.id, sport="Padel", weekdays="0123456", start_min=0, end_min=1440,
                       duration_min=60, amount=1600, effective_from=date(2026, 1, 1)))
    pw = hash_password("pulse1234")
    db.add(User(name="HQ", email="hq@x.com", password_hash=pw, role="hq"))
    vm = User(name="VM", email="vm@x.com", password_hash=pw, role="vm")
    vm.venues = [VmAssignment(venue_id=v.id)]
    db.add(vm)
    db.commit()
    db.close()
    timeutil.set_clock(at(16))
    c = TestClient(app)
    yield c
    timeutil.set_clock(None)


def login(c, email):
    tok = c.post("/api/auth/login", json={"email": email, "password": "pulse1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


def direct(c, h, facility=1, start="19:00", duration=60, phone="9811111111", name="Asha", **kw):
    return c.post("/api/bookings/direct", headers=h, json={
        "venue_id": 1, "facility_id": facility, "date": DAY.isoformat(), "start": start, "duration": duration,
        "phone": phone, "name": name, **kw})


EMAIL = """Subject: Booking Confirmed - DST-1001

Booking ID: DST-1001
Venue: EOK
Date: 07 Oct 2026
Time: 7:00 PM - 8:00 PM
Amount: Rs. 800
Payment Status: {pay}
Phone: +91 98222 22222
"""


def test_inventory_never_double_allocates(client):
    h = login(client, "vm@x.com")
    assert direct(client, h).status_code == 200
    r = direct(client, h, start="19:30", duration=30, phone="9811111112", name="B")
    assert r.status_code == 409 and "already allocated" in r.json()["detail"]
    hq = login(client, "hq@x.com")
    blk = client.post("/api/blocks", headers=hq, json={"venue_id": 1, "facility_id": 1, "date": DAY.isoformat(),
                                                      "start": "19:00", "end": "20:00", "reason": "maintenance"})
    assert blk.status_code == 409


def test_slot_boundaries_and_hours(client):
    h = login(client, "vm@x.com")
    assert direct(client, h, start="19:15").status_code == 422
    assert direct(client, h, start="16:00").status_code == 409  # before opening
    assert direct(client, h, duration=90).status_code == 422


def test_price_from_config_and_editable(client):
    h = login(client, "vm@x.com")
    b = direct(client, h).json()
    assert b["booking_value"] == 800 and b["sport"] == "Pickleball"
    b2 = direct(client, h, facility=3, amount=1200).json()
    assert b2["booking_value"] == 1200


def test_vm_is_venue_scoped(client):
    h = login(client, "vm@x.com")
    r = client.post("/api/bookings/direct", headers=h, json={
        "venue_id": 2, "facility_id": 4, "date": DAY.isoformat(), "start": "07:00", "duration": 60,
        "phone": "9811111111", "name": "A"})
    assert r.status_code == 403
    assert client.get("/api/venues/2/day", headers=h).status_code == 403
    assert client.get("/api/audit", headers=h).status_code == 403


def test_district_ingestion_idempotent_and_identity(client):
    hq = login(client, "hq@x.com")
    r1 = client.post("/api/system/district/simulate", headers=hq, json={"raw": EMAIL.format(pay="Paid")}).json()
    r2 = client.post("/api/system/district/simulate", headers=hq, json={"raw": EMAIL.format(pay="Paid")}).json()
    assert r1["result"] == "created" and r2["result"] == "duplicate" and r1["booking_id"] == r2["booking_id"]
    b = client.get(f"/api/bookings/{r1['booking_id']}", headers=hq).json()
    assert b["facility_id"] is None and b["customer"]["phone"] == "9822222222" and b["customer"]["name"] is None
    # A later Direct booking supplies the name; it then locks.
    vm = login(client, "vm@x.com")
    direct(client, vm, start="21:00", phone="9822222222", name="Ravi")
    direct(client, vm, start="22:00", phone="9822222222", name="Someone Else")
    c = client.get(f"/api/bookings/{r1['booking_id']}", headers=hq).json()["customer"]
    assert c["name"] == "Ravi" and c["name_locked"]


def test_unassigned_district_holds_venue_capacity(client):
    hq = login(client, "hq@x.com")
    vm = login(client, "vm@x.com")
    raw = EMAIL.format(pay="Paid")
    client.post("/api/system/district/simulate", headers=hq, json={"raw": raw})
    client.post("/api/system/district/simulate", headers=hq,
                json={"raw": raw.replace("DST-1001", "DST-1002").replace("98222 22222", "98333 33333")})
    assert direct(client, vm, facility=1).status_code == 200  # 3 courts, 2 District → 1 free
    r = direct(client, vm, facility=2, phone="9844444444", name="Z")
    assert r.status_code == 409 and "unassigned District" in r.json()["detail"]


def test_district_assignment_attendance_and_no_show_timing(client):
    hq = login(client, "hq@x.com")
    vm = login(client, "vm@x.com")
    bid = client.post("/api/system/district/simulate", headers=hq, json={"raw": EMAIL.format(pay="Paid")}).json()["booking_id"]
    timeutil.set_clock(at(19, 29))
    r = client.post(f"/api/bookings/{bid}/no-show", headers=vm)
    assert r.status_code == 409 and "7:30" in r.json()["detail"]
    timeutil.set_clock(at(19, 5))
    b = client.post(f"/api/bookings/{bid}/assign", headers=vm, json={"facility_id": 2}).json()
    assert b["attendance"] == "attended" and b["facility"] == "Court 2"


def test_reassignment_keeps_history_and_frees_slot(client):
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    timeutil.set_clock(at(19, 15))
    moved = client.post(f"/api/bookings/{b['id']}/reassign", headers=vm, json={"facility_id": 2}).json()
    assert moved["facility_id"] == 2 and moved["original_facility_id"] == 1
    assert [a["facility_id"] for a in moved["assignments"]] == [1, 2]
    assert moved["assignments"][0]["released_at"] is not None
    # Court 1 7–8 PM is bookable again as a full slot.
    assert direct(client, vm, facility=1, phone="9855555555", name="New").status_code == 200


def test_payment_window_overdue_and_release(client):
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    assert b["payment_window_end_min"] == 20 * 60 + 30
    r = client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "cash"})
    assert r.status_code == 409  # window opens 18:30
    timeutil.set_clock(at(20, 31))
    day = client.get("/api/venues/1/day", headers=vm).json()
    bk = next(x for x in day["activities"]["bookings"] if x["id"] == b["id"])
    assert bk["payment_status"] == "overdue"
    grid = client.get("/api/venues/1/grid", headers=vm).json()
    assert any(c["ref_id"] == b["id"] for c in grid["cells"])  # inventory still held
    ext = client.post(f"/api/bookings/{b['id']}/extend-window", headers=vm, json={"end": "21:30"}).json()
    assert ext["payment_status"] == "to_collect"
    paid = client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "upi"}).json()
    assert paid["payment_status"] == "paid" and paid["collected_amount"] == 800


def test_overdue_cancel_needs_no_reason(client):
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    timeutil.set_clock(at(20, 45))
    client.get("/api/venues/1/day", headers=vm)
    r = client.post(f"/api/bookings/{b['id']}/cancel", headers=vm, json={"reason": ""}).json()
    assert r["status"] == "cancelled" and r["cancel_reason"] == "payment_overdue"


def test_refund_hits_original_date_and_occupancy_needs_attendance(client):
    hq = login(client, "hq@x.com")
    vm = login(client, "vm@x.com")
    bid = client.post("/api/system/district/simulate", headers=hq, json={"raw": EMAIL.format(pay="Paid")}).json()["booking_id"]
    timeutil.set_clock(at(19))
    client.post(f"/api/bookings/{bid}/assign", headers=vm, json={"facility_id": 1})
    timeutil.set_clock(at(23, 50))
    m = client.get("/api/venues/1/day", headers=hq).json()["metrics"]
    assert m["net_revenue"] == 800 and m["occupied_units"] == 2 and m["sellable_units"] == 36
    timeutil.set_clock(at(12, d=DAY + timedelta(days=2)))
    client.post(f"/api/bookings/{bid}/refund", headers=hq, json={"amount": 800, "reason": "Court lights failed"})
    m = client.get(f"/api/venues/1/day?date={DAY.isoformat()}", headers=hq).json()["metrics"]
    assert m["net_revenue"] == 0 and m["refunds"] == 800


def test_blocks_reduce_sellable_not_revenue(client):
    vm = login(client, "vm@x.com")
    timeutil.set_clock(at(17))
    client.post("/api/blocks", headers=vm, json={"venue_id": 1, "facility_id": 3, "date": DAY.isoformat(),
                                                 "start": "17:00", "end": "23:00", "reason": "maintenance"})
    m = client.get("/api/venues/1/day", headers=vm).json()["metrics"]
    assert m["blocked_units"] == 12 and m["sellable_units"] == 24 and m["net_revenue"] == 0


def test_recurring_academy_materializes_occurrences(client):
    hq = login(client, "hq@x.com")
    r = client.post("/api/academy", headers=hq, json={
        "venue_id": 1, "name": "Juniors", "facility_ids": [1], "recurring": True, "weekdays": "02",
        "start_date": "2026-10-05", "end_date": "2026-10-18", "start": "17:00", "end": "19:00",
        "value_per_occurrence": 1000}).json()
    assert r["scheduled"] == 4
    assert {o["date"] for o in r["occurrences"]} == {"2026-10-05", "2026-10-07", "2026-10-12", "2026-10-14"}


def test_community_participants_link_to_customer_and_whatsapp(client):
    hq = login(client, "hq@x.com")
    vm = login(client, "vm@x.com")
    g = client.post("/api/community-games", headers=hq, json={
        "venue_id": 1, "sport": "Pickleball", "date": DAY.isoformat(), "start": "19:00", "end": "21:00",
        "facility_ids": [1, 2], "capacity": 8, "per_person": 500, "community_link": "https://chat.whatsapp.com/x"}).json()
    direct(client, vm, facility=3, phone="9866666666", name="Meera")
    g2 = client.post(f"/api/community-games/{g['id']}/participants", headers=vm,
                     json={"phone": "+91 98666 66666", "level": "advanced"}).json()
    assert g2["participants"][0]["name"] == "Meera"
    assert client.post(f"/api/community-games/{g['id']}/participants", headers=vm,
                       json={"phone": "9877777777", "level": "beginner"}).status_code == 422  # new needs name
    notes = client.get("/api/system/notifications", headers=hq).json()
    assert any(n["template"] == "community_link" for n in notes)
    timeutil.set_clock(at(19, 10))
    r = client.post(f"/api/community-games/{g['id']}/facilities", headers=hq, json={"facility_ids": [2]})
    assert r.status_code == 409  # locked after start


def test_day_close_requires_resolution(client):
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    timeutil.set_clock(at(22, 30))
    st = client.get("/api/venues/1/close", headers=vm).json()
    assert not st["can_close"] and len(st["missing"]) >= 1
    assert client.post("/api/venues/1/close", headers=vm).status_code == 409
    client.post(f"/api/bookings/{b['id']}/attended", headers=vm)
    client.post(f"/api/bookings/{b['id']}/extend-window", headers=vm, json={"end": "23:00"})
    client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "cash"})
    assert client.post("/api/venues/1/close", headers=vm).json()["status"] == "closed"


def test_missed_close_escalates(client):
    from app.services import dayclose
    vm = login(client, "vm@x.com")
    direct(client, vm)
    timeutil.set_clock(at(23, 31))
    db = SessionLocal()
    try:
        assert dayclose.escalate_missed(db) == 2  # EOK and SKT both operated and neither closed
        db.commit()
    finally:
        db.close()
    hq = login(client, "hq@x.com")
    mail = [n for n in client.get("/api/system/notifications?channel=email", headers=hq).json()]
    assert mail and "Missed day close" in mail[0]["body"]


def test_correction_flows_to_history(client):
    hq = login(client, "hq@x.com")
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    timeutil.set_clock(at(19))
    client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "cash"})
    timeutil.set_clock(at(10, d=DAY + timedelta(days=1)))
    before = client.get(f"/api/venues/1/day?date={DAY.isoformat()}", headers=hq).json()["metrics"]["net_revenue"]
    client.post(f"/api/bookings/{b['id']}/amount", headers=hq, json={"amount": 1000})
    after = client.get(f"/api/venues/1/day?date={DAY.isoformat()}", headers=hq).json()["metrics"]["net_revenue"]
    assert (before, after) == (800, 1000)
    log = client.get(f"/api/audit?entity=booking&entity_id={b['id']}", headers=hq).json()
    assert any(a["action"] == "booking.amount_change" for a in log)


def test_partial_payment_impossible(client):
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    timeutil.set_clock(at(19))
    paid = client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "card"}).json()
    assert paid["collected_amount"] == paid["booking_value"]
    assert client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "card"}).status_code == 409
