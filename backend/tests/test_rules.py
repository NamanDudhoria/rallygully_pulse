"""Acceptance criteria (PRD §74) exercised through the HTTP API on an isolated database."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.auth import hash_password
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Facility, PricingRule, User, Venue, VmAssignment, VmAttendance, VmRoster, WeeklyHours
from app.auth import reset_throttle

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
    db.flush()
    db.add(VmRoster(venue_id=v.id, user_id=vm.id, date=DAY))
    # The VM has clocked in on site for that shift (attendance has its own tests).
    db.add(VmAttendance(user_id=vm.id, venue_id=v.id, date=DAY, clock_in_at=at(16), in_lat=0, in_lng=0,
                        in_distance_m=0, photo_at=at(16), photo_status="sent"))
    db.commit()
    db.close()
    timeutil.set_clock(at(16))
    reset_throttle()
    c = TestClient(app)
    yield c
    timeutil.set_clock(None)


def login(c, email):
    tok = c.post("/api/auth/login", json={"email": email, "password": "pulse1234"}).json()["token"]
    return {"Authorization": f"Bearer {tok}"}


SOP_JPEG = "data:image/jpeg;base64," + __import__("base64").b64encode(b"\xff\xd8\xff\xe0" + b"0" * 500).decode()


def sop_done(c, h, venue=1):
    """Upload the daily SOP's compulsory photos (venue, branding, toilets) so the day can be closed."""
    for k in ("venue", "branding", "toilets"):
        assert c.post(f"/api/venues/{venue}/sop/photos", headers=h, json={"kind": k, "image": SOP_JPEG}).status_code == 200


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
    assert direct(client, h, end="19:00").status_code == 422        # empty slot
    assert direct(client, h, end="18:30").status_code == 422        # ends before it starts
    assert direct(client, h, end="20:15").status_code == 422        # end must be on :00 / :30
    assert direct(client, h, start="22:00", end="23:30").status_code == 409  # runs past closing


def test_slot_is_start_to_end_and_blocks_those_times(client):
    h = login(client, "vm@x.com")
    r = direct(client, h, start="19:00", end="20:30", phone="9811111119", name="Long")
    assert r.status_code == 200
    b = r.json()
    assert (b["start_min"], b["end_min"]) == (19 * 60, 20 * 60 + 30)
    assert b["booking_value"] == 1200  # 60 min at 800 + 30 min at half the 60-min price
    # the whole 7:00-8:30 slot is closed on that court; the half-hour after it is free
    assert direct(client, h, start="20:00", end="21:00", phone="9811111118", name="Late").status_code == 409
    assert direct(client, h, start="20:30", end="21:00", phone="9811111117", name="After").status_code == 200
    q = client.get(f"/api/venues/1/quote?facility_id=1&date={DAY}&start=19:00&end=20:00", headers=h).json()
    assert q["available"] is False


def test_price_is_fixed_for_vms_and_hq_can_override(client):
    h = login(client, "vm@x.com")
    b = direct(client, h).json()
    assert b["booking_value"] == 800 and b["sport"] == "Pickleball" and b["list_price"] == 800
    assert b["discount_name"] is None
    assert direct(client, h, facility=3, amount=1200, phone="9811111113", name="C").status_code == 403  # VM can't type a price
    assert client.post(f"/api/bookings/{b['id']}/amount", headers=h, json={"amount": 500}).status_code == 403
    hq = login(client, "hq@x.com")
    b2 = direct(client, hq, facility=3, amount=1200, phone="9811111113", name="C").json()
    assert b2["booking_value"] == 1200 and b2["list_price"] == 1600  # HQ override; the configured price is kept on record
    assert client.post(f"/api/bookings/{b['id']}/amount", headers=hq, json={"amount": 700}).json()["booking_value"] == 700


def test_venue_discounts_are_configured_and_applied(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm2@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, hv = login(client, "adm2@x.com"), login(client, "vm@x.com")
    v = next(x for x in client.get("/api/venues", headers=ha).json() if x["id"] == 1)
    body = {k: v[k] for k in ("code", "name", "location", "status", "latitude", "longitude")}
    assert client.put("/api/venues/1", headers=ha, json={**body, "discounts": [{"name": "Bad", "percent": 120}]}).status_code == 422
    assert client.put("/api/venues/1", headers=ha, json={**body, "discounts": [{"name": "A", "percent": 10}, {"name": "a", "percent": 5}]}).status_code == 422
    r = client.put("/api/venues/1", headers=ha, json={**body, "discounts": [{"name": "Member 10%", "percent": 10}, {"name": "Staff 25%", "percent": 25}]})
    assert [(d["name"], d["percent"]) for d in r.json()["discounts"]] == [("Member 10%", 10), ("Staff 25%", 25)]
    ids = {d["name"]: d["id"] for d in r.json()["discounts"]}
    q = client.get(f"/api/venues/1/quote?facility_id=1&date={DAY}&start=19:00&end=20:00&discount_id={ids['Staff 25%']}", headers=hv).json()
    assert q["amount"] == 800 and q["total"] == 600
    b = direct(client, hv, discount_id=ids["Staff 25%"]).json()
    assert (b["booking_value"], b["list_price"], b["discount_name"], b["discount_percent"]) == (600, 800, "Staff 25%", 25)
    assert direct(client, hv, start="20:00", discount_id=ids["Member 10%"], amount=100, phone="9811111114", name="D").status_code == 422  # both
    # leaving a discount out retires it: it can't be used again, and the booking that used it keeps its record
    r = client.put("/api/venues/1", headers=ha, json={**body, "discounts": [{"id": ids["Member 10%"], "name": "Member 10%", "percent": 10}]})
    assert [d["name"] for d in r.json()["discounts"]] == ["Member 10%"]
    assert direct(client, hv, start="21:00", discount_id=ids["Staff 25%"], phone="9811111115", name="E").status_code == 422
    assert client.put("/api/venues/1", headers=ha, json=body).json()["discounts"][0]["name"] == "Member 10%"  # omitted = unchanged
    # a discount from another venue is refused
    other = client.post("/api/venues", headers=ha, json={"code": "ZZZ", "name": "Zed", "discounts": [{"name": "Z", "percent": 5}]}).json()
    assert direct(client, hv, start="22:00", discount_id=other["discounts"][0]["id"], phone="9811111116", name="F").status_code == 422


def test_booking_confirmation_is_emailed_when_customer_has_an_email(client):
    from app.models import Notification
    h = login(client, "vm@x.com")
    assert direct(client, h, email="not-an-email").status_code == 422
    assert direct(client, h, email="  Asha@Example.com ").status_code == 200
    db = SessionLocal()
    mails = db.query(Notification).filter_by(channel="email", template="booking_created").all()
    assert [m.recipient for m in mails] == ["asha@example.com"]
    assert "confirmed" in mails[0].body
    assert db.query(Notification).filter_by(channel="whatsapp", template="booking_created").count() == 1
    db.close()
    assert direct(client, h, facility=3, phone="9822222222", name="Ravi").status_code == 200  # no email given
    db = SessionLocal()
    assert db.query(Notification).filter_by(channel="email", template="booking_created").count() == 1
    db.close()


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
    paid = client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "rg_qr"}).json()
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
    as_vm = client.get("/api/venues/1/day", headers=vm).json()
    assert "metrics" not in as_vm and as_vm["show_performance"] is False  # VMs don't see performance
    m = client.get("/api/venues/1/day", headers=login(client, "hq@x.com")).json()["metrics"]
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
    g2 = client.post(f"/api/community-games/{g['id']}/participants", headers=hq,
                     json={"phone": "+91 98666 66666", "level": "advanced"}).json()
    assert g2["participants"][0]["name"] == "Meera"
    assert client.post(f"/api/community-games/{g['id']}/participants", headers=hq,
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
    sop_done(client, vm)
    assert client.post("/api/venues/1/close", headers=vm).status_code == 409  # all resolved, but the shift runs to 11 PM
    timeutil.set_clock(at(23, 0))
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
    paid = client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "razorpay"}).json()
    assert paid["collected_amount"] == paid["booking_value"]
    assert client.post(f"/api/bookings/{b['id']}/pay", headers=vm, json={"method": "razorpay"}).status_code == 409


def _game(client, hq, capacity=8):
    return client.post("/api/community-games", headers=hq, json={
        "venue_id": 1, "sport": "Pickleball", "date": DAY.isoformat(), "start": "19:00", "end": "21:00",
        "facility_ids": [1, 2], "capacity": capacity, "per_person": 500,
        "community_link": "https://chat.whatsapp.com/x"}).json()


def test_vm_only_marks_attendance_and_collects(client):
    hq, vm = login(client, "hq@x.com"), login(client, "vm@x.com")
    g = _game(client, hq)
    r = client.post(f"/api/community-games/{g['id']}/participants", headers=vm,
                    json={"phone": "9811111111", "name": "Asha", "level": "beginner"})
    assert r.status_code == 403  # players are added by the Community team
    g = client.post(f"/api/community-games/{g['id']}/participants", headers=hq,
                    json={"phone": "9811111111", "name": "Asha", "level": "beginner"}).json()
    g = client.post(f"/api/community-games/{g['id']}/participants", headers=hq,
                    json={"phone": "9822222222", "name": "Ravi", "level": "advanced"}).json()
    asha, ravi = g["participants"]
    # Community records Ravi's prepayment before the game
    assert client.patch(f"/api/participants/{ravi['id']}", headers=hq, json={"paid": True, "method": "dist_qr"}).status_code == 200
    # VM: attendance + collect from the one who hasn't paid
    assert client.patch(f"/api/participants/{asha['id']}", headers=vm, json={"attendance": "attended"}).status_code == 200
    assert client.patch(f"/api/participants/{asha['id']}", headers=vm, json={"paid": True, "method": "cash"}).status_code == 200
    assert client.patch(f"/api/participants/{asha['id']}", headers=vm, json={"paid": False}).status_code == 200  # own entry
    assert client.patch(f"/api/participants/{ravi['id']}", headers=vm, json={"paid": False}).status_code == 403
    assert client.patch(f"/api/participants/{ravi['id']}", headers=vm, json={"level": "beginner"}).status_code == 403
    assert client.delete(f"/api/participants/{asha['id']}", headers=vm).status_code == 403


def test_bulk_import_preview_then_add(client):
    hq = login(client, "hq@x.com")
    direct(client, login(client, "vm@x.com"), facility=3, phone="9866666666", name="Meera")
    g = _game(client, hq, capacity=4)
    text = ("Name\tPhone\tLevel\n"
            "Asha Rao\t+91 98111 11111\tBeginner\n"
            "\t98666 66666\t\n"            # existing customer: name comes from the record
            "Ravi\t12345\tadv\n"            # bad phone
            "Kiran\t9833333333\n"
            "Asha again\t9811111111\t\n"    # listed twice
            "Dev\t9844444444\tpro\n"
            "Zoya\t9855555555\t\n")         # over capacity
    url = f"/api/community-games/{g['id']}/participants/import"
    prev = client.post(url, headers=hq, json={"text": text, "default_level": "intermediate"}).json()
    st = {r["line"]: (r["status"], r["level"]) for r in prev["rows"]}
    assert st[2] == ("ready", "Beginner") and st[3][0] == "ready" and st[4][0] == "error"
    assert st[5] == ("ready", "intermediate") and st[6][0] == "skip" and st[7] == ("ready", "pro")
    assert st[8][0] == "skip"  # game full
    assert next(r for r in prev["rows"] if r["line"] == 3)["name"] == "Meera"
    assert client.get(f"/api/community-games/{g['id']}", headers=hq).json()["participant_count"] == 0  # dry run
    done = client.post(url, headers=hq, json={"text": text, "default_level": "intermediate", "dry_run": False}).json()
    assert done["added"] == 4 and done["game"]["participant_count"] == 4


def test_import_free_text_csv_and_xlsx(client):
    import base64
    import io
    from openpyxl import Workbook
    from app.services import player_import as pi
    rows, _ = pi.parse_post("Meera Kapoor 98666 66666 advanced\n+91 98111-11111 - Asha\nNo phone here\nBad 12345")
    assert [(r["name"], r["phone"], r["level"]) for r in rows[:3]] == [
        ("Meera Kapoor", "9866666666", "advanced"), ("Asha", "9811111111", None), ("No phone here", None, None)]
    assert rows[2]["error"] is None and "isn't a valid mobile" in rows[3]["error"]
    wb = Workbook()
    ws = wb.active
    ws.append(["Mobile", "Player Name"])
    ws.append([9822222222, "Ravi"])  # Excel number cell
    buf = io.BytesIO()
    wb.save(buf)
    hq = login(client, "hq@x.com")
    g = _game(client, hq)
    r = client.post(f"/api/community-games/{g['id']}/participants/import", headers=hq, json={
        "file_b64": base64.b64encode(buf.getvalue()).decode(), "filename": "list.xlsx", "dry_run": False}).json()
    assert r["added"] == 1 and r["game"]["participants"][0]["name"] == "Ravi"
    csv = base64.b64encode("phone,name,level\n9833333333,Kiran,beginner\n".encode()).decode()
    r = client.post(f"/api/community-games/{g['id']}/participants/import", headers=hq,
                    json={"file_b64": csv, "filename": "list.csv"}).json()
    assert r["ready"] == 1 and r["rows"][0]["level"] == "beginner"


POST = """OpenPlay Today💜🧡
📍RallyGully, East of Kailash

Please list your names below:

7-8 PM | Intermediate
1. Jatan
2. Jatan + 1
3. Lavitt
4.

8-9 PM | Beginner
1. Ayush Sharma
2. Dhruv Chakraborty
3.
4.
"""


def test_whatsapp_signup_post_imports_matching_section(client):
    hq = login(client, "hq@x.com")
    g = client.post("/api/community-games", headers=hq, json={
        "venue_id": 1, "sport": "Pickleball", "date": DAY.isoformat(), "start": "19:00", "end": "20:00",
        "facility_ids": [1], "capacity": 8, "per_person": 400, "community_link": "https://chat.whatsapp.com/x"}).json()
    url = f"/api/community-games/{g['id']}/participants/import"
    prev = client.post(url, headers=hq, json={"text": POST}).json()
    assert [(s["label"], s["selected"]) for s in prev["sections"]] == [
        ("7-8 PM | Intermediate", True), ("8-9 PM | Beginner", False)]
    assert prev["ready"] == 3  # Jatan, Jatan's guest, Lavitt
    done = client.post(url, headers=hq, json={"text": POST, "dry_run": False}).json()
    ps = done["game"]["participants"]
    assert [p["name"] for p in ps] == ["Jatan", "Jatan's guest", "Lavitt"]
    assert all(p["level"] == "Intermediate" and p["phone"] is None for p in ps)
    assert ps[1]["guest_of_name"] == "Jatan"
    # Both sections on request; the 7-8 names are already in, so only 8-9 is new
    r = client.post(url, headers=hq, json={"text": POST, "sections": ["7-8 PM | Intermediate", "8-9 PM | Beginner"]}).json()
    assert r["ready"] == 2
    # VM on ground can attach a phone to a name-only player, and collect
    vm = login(client, "vm@x.com")
    r = client.patch(f"/api/participants/{ps[0]['id']}", headers=vm, json={"phone": "9811111111"})
    assert r.status_code == 200, r.text
    linked = next(p for p in r.json()["participants"] if p["id"] == ps[0]["id"])
    assert linked["phone"] == "9811111111" and linked["name"] == "Jatan"
    assert client.patch(f"/api/participants/{ps[2]['id']}", headers=vm, json={"paid": True, "method": "cash"}).status_code == 200


def test_mtd_and_custom_baselines(client):
    from app.services.analytics import _window_days
    oct7 = date(2026, 10, 7)
    assert _window_days("mtd", oct7, None) == [date(2026, 10, i) for i in range(1, 7)]
    assert _window_days("mtd", date(2026, 10, 1), None)[0] == date(2026, 9, 1)  # on the 1st: last month
    assert len(_window_days("mtd", date(2026, 10, 1), None)) == 30
    assert _window_days("custom", oct7, (date(2026, 9, 1), date(2026, 9, 3))) == [date(2026, 9, i) for i in (1, 2, 3)]
    hq = login(client, "hq@x.com")
    w = client.get("/api/venues/1/day?cmp_from=2026-09-01&cmp_to=2026-09-30", headers=hq).json()
    assert {"mtd", "custom"} <= set(w["comparisons"]["net_revenue"]["windows"])
    assert client.get("/api/analytics/portfolio?cmp_from=2026-10-05", headers=hq).status_code == 422


def test_background_warm_up_runs(client):
    """The warm-up thread calls the portfolio route directly; a signature change must not break it."""
    from app.services.analytics import warm_once
    db = SessionLocal()
    try:
        warm_once(db)
    finally:
        db.close()


def test_confirmation_uses_the_venues_own_community_link_and_twilio(client, monkeypatch):
    import json, urllib.parse
    from app import config
    from app.models import Notification
    admin = User(name="Admin", email="adm@x.com", password_hash=hash_password("pulse1234"), role="hq_admin")
    db = SessionLocal()
    db.add(admin)
    db.commit()
    db.close()
    ha = login(client, "adm@x.com")
    v = next(x for x in client.get("/api/venues", headers=ha).json() if x["id"] == 1)
    body = {k: v[k] for k in ("code", "name", "location", "status", "latitude", "longitude")}
    assert client.put("/api/venues/1", headers=ha, json={**body, "community_link": "http://nope"}).status_code == 422
    r = client.put("/api/venues/1", headers=ha, json={**body, "community_link": "https://chat.whatsapp.com/EOKgroup"})
    assert r.status_code == 200 and r.json()["community_link"] == "https://chat.whatsapp.com/EOKgroup"

    sent = []

    class Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr("app.services.notify.urllib.request.urlopen", lambda req, timeout=0: sent.append(req) or Resp())
    for k, val in (("TWILIO_ACCOUNT_SID", "ACtest"), ("TWILIO_AUTH_TOKEN", "tok"), ("TWILIO_WHATSAPP_FROM", "+919800000000"),
                   ("TWILIO_CONTENT_SID_BOOKING", "HXabc")):
        monkeypatch.setattr(config, k, val)
    h = login(client, "vm@x.com")
    assert direct(client, h).status_code == 200
    form = urllib.parse.parse_qs(sent[0].data.decode())
    assert sent[0].full_url.endswith("/Accounts/ACtest/Messages.json")
    assert form["From"] == ["whatsapp:+919800000000"] and form["To"] == ["whatsapp:+919811111111"]
    assert form["ContentSid"] == ["HXabc"]
    assert json.loads(form["ContentVariables"][0]) == {"venue": "East of Kailash", "community_link": "https://chat.whatsapp.com/EOKgroup"}
    db = SessionLocal()
    assert db.query(Notification).filter_by(channel="whatsapp", status="sent").count() == 1
    db.close()


def test_vm_is_whatsapped_tomorrows_allotment_at_1130pm(client, monkeypatch):
    import json, urllib.parse
    from app import config
    from app.services import roster
    db = SessionLocal()
    db.add(User(name="HQ", email="hq2@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    vm_id = db.query(User).filter_by(email="vm@x.com").one().id
    db.commit()
    db.close()
    h = login(client, "hq2@x.com")
    body = {"name": "VM One", "email": "vm@x.com", "role": "vm", "venue_ids": [1], "active": True}
    assert client.put(f"/api/users/{vm_id}", headers=h, json={**body, "phone": "12345"}).status_code == 422
    assert client.put(f"/api/users/{vm_id}", headers=h, json={**body, "phone": "+91 98123 45678"}).json()["phone"] == "9812345678"

    sent = []

    class Resp:
        status = 201
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr("app.services.notify.urllib.request.urlopen", lambda req, timeout=0: sent.append(req) or Resp())
    for k, val in (("TWILIO_ACCOUNT_SID", "ACtest"), ("TWILIO_AUTH_TOKEN", "tok"), ("TWILIO_WHATSAPP_FROM", "+919800000000"),
                   ("TWILIO_CONTENT_SID_ROSTER", "HXroster")):
        monkeypatch.setattr(config, k, val)

    def tick():
        db = SessionLocal()
        n = roster.send_tomorrow(db)
        db.commit()
        db.close()
        return n

    tomorrow = (DAY + timedelta(days=1)).isoformat()
    assert client.post("/api/roster", headers=h, json={"venue_id": 1, "user_id": vm_id, "date": tomorrow}).status_code == 200
    assert sent == []  # rostering sends nothing by itself
    timeutil.set_clock(at(23, 29))
    assert tick() == 0
    timeutil.set_clock(at(23, 30))
    assert tick() == 1
    form = urllib.parse.parse_qs(sent[0].data.decode())
    assert form["To"] == ["whatsapp:+919812345678"] and form["ContentSid"] == ["HXroster"]
    vars_ = json.loads(form["ContentVariables"][0])
    assert set(vars_) == {"venue_name", "time-slot"} and vars_["time-slot"] == "5:00 PM to 11:00 PM"
    assert tick() == 0 and len(sent) == 1  # once only
    # a shift edited after the message was sent is announced again with the new time
    entry = next(e for e in client.get(f"/api/roster?date_from={tomorrow}&date_to={tomorrow}", headers=h).json()["entries"])
    assert client.patch(f"/api/roster/{entry['id']}", headers=h, json={"start": "18:00", "end": "22:00"}).status_code == 200
    assert tick() == 1
    assert json.loads(urllib.parse.parse_qs(sent[1].data.decode())["ContentVariables"][0])["time-slot"] == "6:00 PM to 10:00 PM"
    timeutil.set_clock(at(0, 5, DAY + timedelta(days=1)))  # after midnight "tomorrow" is a new day: nothing sent
    assert tick() == 0


def test_long_slot_is_priced_as_hours_plus_a_half_hour(client):
    from app.models import PricingRule
    db = SessionLocal()
    db.add(PricingRule(venue_id=1, sport="Pickleball", weekdays="0123456", start_min=0, end_min=1440,
                       duration_min=30, amount=500, effective_from=date(2026, 1, 1)))
    db.commit()
    db.close()
    h = login(client, "vm@x.com")
    def price(start, end):
        return client.get(f"/api/venues/1/quote?facility_id=1&date={DAY}&start={start}&end={end}", headers=h).json()["amount"]
    assert price("19:00", "19:30") == 500
    assert price("19:00", "20:00") == 800
    assert price("19:00", "20:30") == 1300   # 800 for the hour + 500 for the half-hour, not 3 x 500
    assert price("19:00", "21:30") == 2100   # 800 + 800 + 500
    assert price("19:00", "21:00") == 1600


def test_day_close_is_the_vms_out_attendance_and_only_after_shift_end(client):
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    sop_done(client, vm)
    timeutil.set_clock(at(22, 59))
    r = client.post("/api/venues/1/close", headers=vm)
    assert r.status_code == 409 and "11:00 PM" in r.json()["detail"]
    out = client.post("/api/venues/1/clock-out", headers=vm, json={"latitude": 0, "longitude": 0})
    assert out.status_code == 409 and "11:00 PM" in out.json()["detail"]   # clock-out has the same rule
    db = SessionLocal()
    assert db.query(VmAttendance).one().clock_out_at is None
    db.close()
    timeutil.set_clock(at(23, 0))
    assert client.post("/api/venues/1/close", headers=vm).json()["status"] == "closed"
    db = SessionLocal()
    a = db.query(VmAttendance).one()
    assert a.clock_out_at.replace(microsecond=0) == at(23, 0) and a.out_lat is None   # stamped by the close, no location taken
    db.close()
    entry = next(e for e in client.get(f"/api/roster?date_from={DAY}&date_to={DAY}", headers=hq).json()["entries"] if e["venue_id"] == 1)
    assert entry["attendance"]["clock_out_at"] and entry["attendance"]["left_away"] is False


def test_hq_can_close_a_day_before_shift_end(client):
    hq = login(client, "hq@x.com")
    timeutil.set_clock(at(18, 0))
    sop_done(client, hq)  # HQ can upload SOP photos too
    assert client.post("/api/venues/1/close", headers=hq).status_code == 200  # HQ isn't held to a VM's shift


def test_admins_are_emailed_when_a_vm_has_not_checked_in_by_shift_start(client):
    from app.models import Notification
    from app.services import attendance
    db = SessionLocal()
    db.add_all([User(name="Admin One", email="a1@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"),
                User(name="Admin Two", email="a2@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"),
                User(name="HQ Only", email="hq3@x.com", password_hash=hash_password("pulse1234"), role="hq")])
    vm2 = User(name="Late Larry", email="vm2@x.com", password_hash=hash_password("pulse1234"), role="vm", phone="9812345678")
    db.add(vm2)
    db.flush()
    db.add(VmAssignment(user_id=vm2.id, venue_id=2))
    db.add(VmRoster(venue_id=2, user_id=vm2.id, date=DAY, created_at=at(20, 0, DAY - timedelta(days=1))))  # rostered the evening before
    db.commit()
    db.close()

    def tick():
        db = SessionLocal()
        n = attendance.alert_missing_checkin(db)
        db.commit()
        db.close()
        return n

    timeutil.set_clock(at(5, 59))
    assert tick() == 0
    timeutil.set_clock(at(6, 0))
    assert tick() == 1
    db = SessionLocal()
    mails = db.query(Notification).filter_by(template="attendance_missing").all()
    assert sorted(m.recipient for m in mails) == ["a1@x.com", "a2@x.com"]  # every admin, not plain HQ
    assert "Late Larry has not checked in at Saket" in mails[0].body.split("\n")[0] and "9812345678" in mails[0].body
    db.close()
    assert tick() == 0   # once only
    # the first VM clocked in (fixture), so no alert for them; clocking in late doesn't send another
    timeutil.set_clock(at(17, 0))
    assert tick() == 0


def _setup_rentals(client):
    """HQ admin sets Court prices' rental list on the venue; returns (admin headers, item ids by name)."""
    db = SessionLocal()
    db.add(User(name="Admin", email="adm3@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha = login(client, "adm3@x.com")
    v = next(x for x in client.get("/api/venues", headers=ha).json() if x["id"] == 1)
    body = {k: v[k] for k in ("code", "name", "location", "status", "latitude", "longitude")}
    r = client.put("/api/venues/1", headers=ha, json={**body, "rental_items": [
        {"name": "Racket", "price": 50}, {"name": "Shoes", "price": 100}]})
    assert r.status_code == 200
    return ha, body, {x["name"]: x["id"] for x in r.json()["rental_items"]}


def test_rental_prices_are_fixed_per_venue_and_validated(client):
    ha, body, ids = _setup_rentals(client)
    assert client.put("/api/venues/1", headers=ha, json={**body, "rental_items": [{"name": "Free", "price": 0}]}).status_code == 422
    assert client.put("/api/venues/1", headers=ha, json={**body, "rental_items": [{"name": "A", "price": 5}, {"name": "a", "price": 6}]}).status_code == 422
    r = client.put("/api/venues/1", headers=ha, json={**body, "rental_items": [{"id": ids["Racket"], "name": "Racket", "price": 60}]})
    assert [(x["name"], x["price"]) for x in r.json()["rental_items"]] == [("Racket", 60)]  # Shoes retired
    assert client.put("/api/venues/1", headers=ha, json=body).json()["rental_items"][0]["name"] == "Racket"  # omitted = unchanged


def test_rentals_are_separate_lines_with_their_own_payment(client):
    ha, body, ids = _setup_rentals(client)
    vm = login(client, "vm@x.com")
    b = direct(client, vm, rentals=[{"item_id": ids["Racket"], "quantity": 2}]).json()
    assert (b["booking_value"], b["rental_total"], b["rental_due"]) == (800, 100, 100)  # court revenue is untouched
    assert b["rentals"][0]["amount"] == 100 and b["rentals"][0]["paid_method"] is None
    more = client.post(f"/api/bookings/{b['id']}/rentals", headers=vm, json={"items": [{"item_id": ids["Shoes"], "quantity": 1}]}).json()
    assert more["rental_total"] == 200 and len(more["rentals"]) == 2
    assert client.post(f"/api/bookings/{b['id']}/rentals", headers=vm, json={"items": [{"item_id": 99999, "quantity": 1}]}).status_code == 422
    assert client.post(f"/api/bookings/{b['id']}/rentals", headers=vm, json={"items": [{"item_id": ids["Shoes"], "quantity": 0}]}).status_code == 422
    # the open rental blocks the day close until it is paid or removed
    timeutil.set_clock(at(23, 0))
    st = client.get("/api/venues/1/close", headers=vm).json()
    assert any("equipment rental" in m["message"] for m in st["missing"])
    gone = client.delete(f"/api/bookings/{b['id']}/rentals/{more['rentals'][1]['id']}", headers=vm).json()
    assert gone["rental_total"] == 100
    assert client.post(f"/api/bookings/{b['id']}/rentals/pay", headers=vm, json={"method": "card"}).status_code == 422
    paid = client.post(f"/api/bookings/{b['id']}/rentals/pay", headers=vm, json={"method": "razorpay"}).json()
    assert paid["rental_due"] == 0 and paid["rentals"][0]["paid_method"] == "razorpay"
    assert paid["payment_status"] == "to_collect"  # the court itself is still owed
    assert client.delete(f"/api/bookings/{b['id']}/rentals/{paid['rentals'][0]['id']}", headers=vm).status_code == 409  # paid: stays
    assert client.post(f"/api/bookings/{b['id']}/rentals/pay", headers=vm, json={"method": "cash"}).status_code == 409  # nothing unpaid


def test_paid_now_covers_court_and_rentals(client):
    ha, body, ids = _setup_rentals(client)
    vm = login(client, "hq@x.com")  # HQ can take payment at booking time; a VM only from 30 min before the start
    b = direct(client, vm, pay_now=True, method="rg_qr", rentals=[{"item_id": ids["Shoes"], "quantity": 1}]).json()
    assert b["payment_status"] == "paid" and b["rental_due"] == 0 and b["rentals"][0]["paid_method"] == "rg_qr"


def test_payment_methods_are_cash_district_qr_rg_qr_razorpay_other(client):
    vm = login(client, "hq@x.com")
    assert direct(client, vm, pay_now=True, method="card", phone="9811111130", name="Q").status_code == 422
    assert direct(client, vm, pay_now=True, method="upi", phone="9811111131", name="Q2").status_code == 422   # UPI is now two QRs
    for i, m in enumerate(("cash", "dist_qr", "rg_qr", "razorpay", "other")):
        r = direct(client, vm, start=f"{17 + i}:00", pay_now=True, method=m, phone=f"98111112{i}0", name="N")
        assert r.status_code == 200 and r.json()["payment_method"] == m


def test_online_booking_entered_by_hand(client):
    vm = login(client, "vm@x.com")
    base = {"venue_id": 1, "date": DAY.isoformat(), "start": "18:00", "end": "19:00", "amount": 650, "phone": "9811111140", "name": "Online Olly"}
    r = client.post("/api/bookings/online", headers=vm, json={**base, "platform": "District", "external_ref": "D-1001"})
    assert r.status_code == 200
    b = r.json()
    assert b["source"] == "district" and b["external_ref"] == "D-1001" and b["platform"] is None
    assert b["booking_value"] == 650 and b["payment_status"] == "paid" and b["facility_id"] is None  # court on arrival
    assert client.post("/api/bookings/online", headers=vm, json={**base, "platform": "District", "external_ref": "D-1001"}).status_code == 409
    h = client.post("/api/bookings/online", headers=vm, json={**base, "platform": "Hudle", "external_ref": "D-1001", "start": "19:00", "end": "20:00", "paid": False})
    assert h.status_code == 200 and h.json()["platform"] == "Hudle" and h.json()["payment_status"] == "to_collect"
    assert h.json()["external_ref"] == "Hudle:D-1001"   # same id on another platform is a different booking
    assert client.post("/api/bookings/online", headers=vm, json={**base, "external_ref": "X", "end": "18:30", "start": "18:00"}).status_code == 200
    assert client.post("/api/bookings/online", headers=vm, json={**base, "external_ref": "Y", "end": "19:45"}).status_code == 422  # off the half-hour
    assert client.post("/api/bookings/online", headers=vm, json={**base, "external_ref": "Z", "end": "20:30"}).status_code == 422  # District is 30 or 60 min
    assert client.post("/api/bookings/online", headers=vm, json={**base, "external_ref": " ", }).status_code == 422
    assert client.post("/api/bookings/online", headers=vm, json={**base, "venue_id": 2, "external_ref": "S1"}).status_code == 403  # not their venue


def _member(client, hq, phone="9811111150", hours=3, venues=(1,), **kw):
    body = {"phone": phone, "name": "Mia Member", "venue_ids": list(venues), "hours": hours, "amount_paid": 2400,
            "method": "rg_qr", **kw}
    return client.post("/api/memberships", headers=hq, json=body)


def test_membership_is_created_by_admin_with_the_mass_payment_recorded(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm4@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, vm = login(client, "adm4@x.com"), login(client, "vm@x.com")
    assert client.post("/api/memberships", headers=vm, json={}).status_code == 403   # not for VMs
    assert _member(client, ha, hours=0.25).status_code == 422                       # half-hour steps
    assert _member(client, ha, method="card").status_code == 422
    assert _member(client, ha, venues=()).status_code == 422
    assert _member(client, ha, expires_on="2026-01-01", starts_on="2026-10-01").status_code == 422
    r = _member(client, ha, expires_on="2026-12-31")
    assert r.status_code == 200
    m = r.json()
    assert (m["total_hours"], m["left_hours"], m["amount_paid"], m["status"]) == (3, 3, 2400, "active")
    assert m["customer"] == {"name": "Mia Member", "phone": "9811111150"}
    assert len(client.get("/api/memberships", headers=ha).json()) == 1


def test_member_booking_is_free_and_hours_come_off_after_play(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm4@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, vm = login(client, "adm4@x.com"), login(client, "vm@x.com")
    mid = _member(client, ha, hours=3).json()["id"]
    lk = client.get(f"/api/customers/lookup?phone=9811111150&venue_id=1&date={DAY}", headers=vm).json()
    assert lk["membership"]["hours_left"] == 3                       # a member is recognised from the number
    b = direct(client, vm, phone="9811111150", name="Mia Member").json()
    assert (b["booking_value"], b["payment_status"], b["payment_method"], b["member_hours"]) == (0, "paid", "membership", 1)
    assert b["list_price"] == 800 and b["collected_amount"] == 0 and b["membership_id"] == mid
    d = client.get(f"/api/memberships/{mid}", headers=ha).json()
    assert (d["used_hours"], d["reserved_hours"], d["left_hours"]) == (0, 1, 2)   # reserved, not yet deducted
    client.post(f"/api/bookings/{b['id']}/attended", headers=vm)
    client.post(f"/api/bookings/{b['id']}/attended", headers=vm)                  # twice must not deduct twice
    d = client.get(f"/api/memberships/{mid}", headers=ha).json()
    assert (d["used_hours"], d["reserved_hours"], d["left_hours"]) == (1, 0, 2) and len(d["uses"]) == 1
    # a different number is charged as usual
    assert direct(client, vm, start="20:00", phone="9811111151", name="Other").json()["booking_value"] == 800


def test_short_balance_covers_what_is_left_and_charges_the_rest(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm4@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, vm = login(client, "adm4@x.com"), login(client, "vm@x.com")
    _member(client, ha, hours=0.5)
    b = direct(client, vm, phone="9811111150", name="Mia Member").json()   # 60 min booked, 30 min of hours
    assert (b["member_hours"], b["payment_status"]) == (0.5, "to_collect")
    assert b["booking_value"] == 400                                        # the uncovered half-hour, at the normal price


def test_membership_respects_venue_expiry_and_cancellation_of_bookings(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm4@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, vm = login(client, "adm4@x.com"), login(client, "vm@x.com")
    expired = _member(client, ha, phone="9811111160", starts_on="2026-01-01", expires_on="2026-10-01")  # ended before DAY
    other = _member(client, ha, phone="9811111161", venues=(2,))                                         # Saket only
    assert expired.status_code == 200 and other.status_code == 200
    for ph in ("9811111160", "9811111161"):
        assert direct(client, vm, phone=ph, name="X", start="19:00" if ph.endswith("0") else "20:00").json()["booking_value"] == 800
    ok = _member(client, ha, phone="9811111162", hours=2).json()
    b = direct(client, vm, start="21:00", phone="9811111162", name="Y").json()
    client.post(f"/api/bookings/{b['id']}/attended", headers=vm)
    assert client.get(f"/api/memberships/{ok['id']}", headers=ha).json()["used_hours"] == 1
    hq = login(client, "hq@x.com")
    assert client.post(f"/api/bookings/{b['id']}/cancel", headers=hq, json={"reason": "venue_closed"}).status_code == 200
    d = client.get(f"/api/memberships/{ok['id']}", headers=ha).json()
    assert (d["used_hours"], d["left_hours"]) == (0, 2)                      # hours are given back
    assert client.post(f"/api/memberships/{ok['id']}/cancel", headers=ha).json()["status"] == "cancelled"
    assert direct(client, vm, start="22:00", phone="9811111162", name="Y").json()["booking_value"] == 800   # no longer covers


def test_member_recognised_at_attendance_on_an_unpaid_booking(client):
    db = SessionLocal()
    db.add(User(name="Admin", email="adm4@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha, vm = login(client, "adm4@x.com"), login(client, "vm@x.com")
    b = direct(client, vm, phone="9811111170", name="Late Joiner").json()    # booked before becoming a member
    assert b["booking_value"] == 800 and b["membership_id"] is None
    mid = _member(client, ha, phone="9811111170", hours=2).json()["id"]
    assert client.post(f"/api/bookings/{b['id']}/attended", headers=vm).status_code == 200
    got = client.get(f"/api/bookings/{b['id']}", headers=vm).json()
    assert (got["booking_value"], got["payment_status"], got["membership_id"]) == (0, "paid", mid)
    assert client.get(f"/api/memberships/{mid}", headers=ha).json()["used_hours"] == 1


def test_admins_get_the_roster_email_once_it_settles_in_the_requested_format(client):
    from app.models import Notification
    from app.services import roster as roster_svc
    db = SessionLocal()
    db.add_all([User(name="Admin One", email="ra1@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"),
                User(name="Admin Two", email="ra2@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"),
                User(name="HQ Only", email="rh@x.com", password_hash=hash_password("pulse1234"), role="hq")])
    vm = db.query(User).filter_by(email="vm@x.com").one()
    vm.name = "Mohit"
    others = {}
    for n, wo in (("Aditya", None), ("Suraj", None), ("Gaurav", (DAY + timedelta(days=2)).weekday()), ("Shubham", None)):
        u = User(name=n, email=f"{n.lower()}@x.com", password_hash=hash_password("pulse1234"), role="vm", week_off=wo)
        db.add(u)
        db.flush()
        others[n] = u.id
        db.add_all([VmAssignment(user_id=u.id, venue_id=1), VmAssignment(user_id=u.id, venue_id=2)])
    db.commit()
    vm_id = vm.id
    db.close()
    hq = login(client, "hq@x.com")
    day = (DAY + timedelta(days=2)).isoformat()      # a Friday
    post = lambda **b: client.post("/api/roster", headers=hq, json={"date": day, **b})
    assert post(venue_id=1, user_id=vm_id).status_code == 200                                        # Mohit, EOK, evening
    assert post(venue_id=1, user_id=others["Aditya"]).status_code == 200                             # + Aditya, same shift
    assert post(venue_id=2, user_id=others["Suraj"], start="09:00", end="17:00").status_code == 200  # Saket, 9am-5pm

    def tick():
        db = SessionLocal()
        n = roster_svc.send_digests(db)
        db.commit()
        db.close()
        return n

    timeutil.set_clock(at(16, 2))
    assert tick() == 0                                   # still inside the quiet period: one email, not one per entry
    timeutil.set_clock(at(16, 10))
    assert tick() == 1
    db = SessionLocal()
    mails = db.query(Notification).filter_by(template="roster_digest").order_by(Notification.id).all()
    assert sorted(m.recipient for m in mails) == ["ra1@x.com", "ra2@x.com"]   # every admin, not plain HQ
    assert mails[0].body.startswith("Roster – Friday")
    body = mails[0].body.split("\n\n", 1)[1]
    assert body.splitlines() == [
        "Roster Date - 9 October 2026 ( Friday )", "", "Morning Shift", "", "* Saket – Suraj (9am-5pm)", "",
        "Evening Shift", "", "* East of Kailash – Aditya + Mohit", "",
        "Week off :- Gaurav", "Leave :- Shubham"]
    db.close()
    assert tick() == 0                                   # nothing changed: nothing more is sent
    # leaves are kept in the database too
    leaves = client.get(f"/api/roster?date_from={day}&date_to={day}", headers=hq).json()["leaves"]
    assert sorted((x["name"], x["kind"]) for x in leaves) == [("Gaurav", "week_off"), ("Shubham", "leave")]
    # a change sends an updated email, and a person given a venue stops being on leave
    assert post(venue_id=2, user_id=others["Shubham"]).status_code == 200
    timeutil.set_clock(at(16, 20))
    assert tick() == 1
    db = SessionLocal()
    last = db.query(Notification).filter_by(template="roster_digest").order_by(Notification.id.desc()).first()
    assert last.body.startswith("Roster – Friday 9 October 2026 (updated)")
    assert "Leave :-" not in last.body and "Saket – Shubham, Suraj" not in last.body
    db.close()
    leaves = client.get(f"/api/roster?date_from={day}&date_to={day}", headers=hq).json()["leaves"]
    assert [(x["name"], x["kind"]) for x in leaves] == [("Gaurav", "week_off")]
    # a day with no roster has no leaves and no email
    quiet = (DAY + timedelta(days=4)).isoformat()
    assert client.get(f"/api/roster?date_from={quiet}&date_to={quiet}", headers=hq).json()["leaves"] == []


def _cash_admin(client):
    db = SessionLocal()
    db.add(User(name="Cash Admin", email="cadm@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    return login(client, "cadm@x.com")


def _venue_cash(client, ha, venue_id=1):
    return client.get(f"/api/venues/{venue_id}/cash", headers=ha).json()


def test_cash_payments_add_to_the_venue_balance_and_other_methods_do_not(client):
    ha, hq = _cash_admin(client), login(client, "hq@x.com")
    assert _venue_cash(client, ha)["balance"] == 0
    cash = direct(client, hq, pay_now=True, method="cash", phone="9811111181", name="C1").json()      # 800 cash
    direct(client, hq, start="20:00", pay_now=True, method="rg_qr", phone="9811111182", name="C2")      # RG QR: not cash
    direct(client, hq, start="21:00", pay_now=True, method="razorpay", phone="9811111183", name="C3")  # not cash
    b = _venue_cash(client, ha)
    assert (b["collected"], b["balance"]) == (800, 800)
    # equipment rentals paid in cash count too, at their own venue only
    ha2, body, ids = _setup_rentals(client)
    r = client.post(f"/api/bookings/{cash['id']}/rentals", headers=hq, json={"items": [{"item_id": ids["Shoes"], "quantity": 2}]}).json()
    client.post(f"/api/bookings/{cash['id']}/rentals/pay", headers=hq, json={"method": "cash"})
    assert _venue_cash(client, ha)["balance"] == 1000
    assert _venue_cash(client, ha, 2)["balance"] == 0
    # a cash refund takes cash back out; refunding a non-cash booking does not
    assert client.post(f"/api/bookings/{cash['id']}/refund", headers=hq, json={"amount": 300, "reason": "Court was unusable"}).status_code == 200
    assert _venue_cash(client, ha)["balance"] == 700 and _venue_cash(client, ha)["refunded"] == 300


def test_admin_updates_cash_with_deposit_and_non_deposit_entries(client):
    ha, hq, vm = _cash_admin(client), login(client, "hq@x.com"), login(client, "vm@x.com")
    direct(client, hq, pay_now=True, method="cash", phone="9811111181", name="C1")
    path = "/api/venues/1/cash"
    assert client.post(path, headers=vm, json={"kind": "deposit", "amount": 100, "depositor_id": 2}).status_code == 403   # VMs can't
    assert client.post(path, headers=ha, json={"kind": "deposit", "amount": 100}).status_code == 422                      # who deposited?
    assert client.post(path, headers=ha, json={"kind": "deposit", "amount": 0, "depositor_id": 2}).status_code == 422
    assert client.post(path, headers=ha, json={"kind": "transfer", "amount": 5}).status_code == 422
    assert client.post(path, headers=ha, json={"kind": "deposit", "amount": 100, "depositor_id": 1}).status_code == 422     # 1 is HQ, not a VM
    r = client.post(path, headers=ha, json={"kind": "deposit", "amount": 500, "depositor_id": 2, "comment": "Bank, SBI Saket"})
    assert r.status_code == 200 and r.json()["balance"] == 300 and r.json()["deposited"] == 500
    e = r.json()["entries"][0]
    assert (e["kind"], e["amount"], e["depositor"], e["comment"], e["by"]) == ("deposit", 500, "VM", "Bank, SBI Saket", "Cash Admin")
    r = client.post(path, headers=ha, json={"kind": "non_deposit", "amount": 1000, "comment": "Opening float"})
    assert r.json()["balance"] == 1300 and r.json()["added"] == 1000
    assert [x["code"] for x in client.get("/api/cash", headers=ha).json()][:2] == ["EOK", "SKT"]
    assert client.get("/api/cash", headers=vm).status_code == 403
    assert _venue_cash(client, ha)["daily"][0]["collected"] == 800
    # plain HQ can see and update cash as well; Venue Managers still can't
    assert client.get("/api/cash", headers=hq).status_code == 200
    assert client.post(path, headers=hq, json={"kind": "deposit", "amount": 50, "depositor_id": 2}).status_code == 200
    assert client.post(path, headers=hq, json={"kind": "non_deposit", "amount": 50}).json()["balance"] == 1300
    assert client.get("/api/cash", headers=vm).status_code == 403
    assert client.get("/api/memberships", headers=hq).status_code == 200     # and memberships, as before
    assert client.get("/api/memberships", headers=vm).status_code == 403


def test_cash_before_the_account_started_is_not_counted(client):
    from app.models import CashAccount
    from app.services import cash
    ha, hq = _cash_admin(client), login(client, "hq@x.com")
    direct(client, hq, pay_now=True, method="cash", phone="9811111181", name="C1")      # collected at 16:00
    db = SessionLocal()
    db.query(CashAccount).delete()
    cash.ensure_accounts(db, at(17, 0))                                                  # tracking starts at 5 PM
    db.commit()
    db.close()
    assert _venue_cash(client, ha)["balance"] == 0
    timeutil.set_clock(at(17, 30))
    direct(client, hq, start="21:00", pay_now=True, method="cash", phone="9811111182", name="C2")
    assert _venue_cash(client, ha)["balance"] == 800


def test_daily_sop_photos_in_four_categories(client):
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    st = client.get("/api/venues/1/sop", headers=vm).json()
    assert st["due"] and not st["complete"] and st["missing"] == ["Venue", "Branding", "Toilets"]
    assert [c["kind"] for c in st["categories"]] == ["venue", "branding", "toilets", "other"]
    assert [c["required"] for c in st["categories"]] == [True, True, True, False]
    # not done yet: the day can't be closed
    timeutil.set_clock(at(23, 0))
    msgs = [m["message"] for m in client.get("/api/venues/1/close", headers=vm).json()["missing"]]
    assert "Daily SOP: no toilets photo yet" in msgs
    # several photos per category, any time in the day; "other" is optional
    up = lambda k, img=SOP_JPEG: client.post("/api/venues/1/sop/photos", headers=vm, json={"kind": k, "image": img})
    assert up("branding").status_code == 200 and up("branding").status_code == 200
    assert up("selfie").status_code == 422
    assert up("venue", "data:image/png;base64,iVBORw0KGgo=").status_code == 422
    up("venue")
    st = up("toilets").json()
    assert st["complete"] and [len(c["photos"]) for c in st["categories"]] == [1, 2, 1, 0]
    assert client.get("/api/venues/1/close", headers=vm).json()["can_close"]
    # photos can be viewed, and a wrong one taken off the same day
    pid = st["categories"][1]["photos"][0]["id"]
    img = client.get(f"/api/venues/1/sop/photos/{pid}", headers=hq)
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg" and img.content.startswith(b"\xff\xd8\xff")
    assert len(client.delete(f"/api/venues/1/sop/photos/{pid}", headers=vm).json()["categories"][1]["photos"]) == 1
    # VMs can't touch other venues; HQ sees any day
    assert client.post("/api/venues/2/sop/photos", headers=vm, json={"kind": "venue", "image": SOP_JPEG}).status_code == 403
    assert client.get(f"/api/venues/1/sop?date={DAY}", headers=hq).json()["complete"]


def test_sop_not_due_without_a_rostered_vm_and_old_photos_are_purged(client):
    from app.models import SopPhoto
    from app.services import sop
    hq = login(client, "hq@x.com")
    assert client.get("/api/venues/2/sop", headers=hq).json()["due"] is False   # nobody rostered at Saket
    vm = login(client, "vm@x.com")
    sop_done(client, vm)
    timeutil.set_clock(at(12, 0, DAY + timedelta(days=31)))
    db = SessionLocal()
    assert sop.purge(db) == 3
    db.commit()
    assert db.query(SopPhoto).count() == 0
    db.close()


def test_vm_can_give_a_custom_discount_with_a_reason(client):
    vm = login(client, "vm@x.com")
    cd = lambda kind, value, reason="Regular, asked nicely": {"kind": kind, "value": value, "reason": reason}
    # percent off: 800 -> 680
    b = direct(client, vm, custom_discount=cd("percent", 15)).json()
    assert (b["booking_value"], b["list_price"], b["discount_name"], b["discount_percent"]) == (680, 800, "Custom 15%", 15)
    assert b["discount_custom"] and b["discount_reason"] == "Regular, asked nicely"
    # flat rupees off: 800 -> 700
    b = direct(client, vm, start="20:00", phone="9811111201", name="F", custom_discount=cd("flat", 100)).json()
    assert (b["booking_value"], b["discount_name"], b["discount_percent"]) == (700, "Custom ₹100", 12.5)
    # no cap: up to the full price
    assert direct(client, vm, start="21:00", phone="9811111202", name="G", custom_discount=cd("percent", 100)).json()["booking_value"] == 0
    # the rules
    bad = lambda **kw: direct(client, vm, start="22:00", phone="9811111203", name="H", **kw).status_code
    assert bad(custom_discount=cd("percent", 10, "  ")) == 422          # reason required
    assert bad(custom_discount=cd("percent", 120)) == 422
    assert bad(custom_discount=cd("flat", 900)) == 422                  # more than the price
    assert bad(custom_discount=cd("flat", 0)) == 422
    assert bad(custom_discount=cd("half", 10)) == 422
    assert bad(custom_discount=cd("percent", 10), amount=500) in (403, 422)   # VMs still can't type a price
    # live preview on the quote
    q = client.get(f"/api/venues/1/quote?facility_id=1&date={DAY}&start=22:00&end=23:00&custom_kind=flat&custom_value=150", headers=vm).json()
    assert (q["amount"], q["total"]) == (800, 650)


def test_custom_discount_report_shows_who_gave_what_and_why(client):
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    direct(client, vm, custom_discount={"kind": "percent", "value": 25, "reason": "Rain delay"})
    direct(client, vm, start="20:00", phone="9811111211", name="K", custom_discount={"kind": "flat", "value": 50, "reason": "Late start"})
    direct(client, vm, start="21:00", phone="9811111212", name="L")   # no discount: not in the report
    assert client.get(f"/api/discounts/custom?date_from={DAY}&date_to={DAY}", headers=vm).status_code == 403
    r = client.get(f"/api/discounts/custom?date_from={DAY}&date_to={DAY}", headers=hq).json()
    assert r["count"] == 2 and r["total_given"] == 250
    assert sorted((x["reason"], x["off"]) for x in r["items"]) == [("Late start", 50), ("Rain delay", 200)]
    assert r["by_person"] == [{"name": "VM", "count": 2, "given": 250}]


def _store_product(client, name="Water", price=30, stock=10):
    db = SessionLocal()
    db.add(User(name="Store Admin", email="sadm@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    ha = login(client, "sadm@x.com")
    return ha, client.post("/api/store/1/products", headers=ha, json={"name": name, "price": price, "low_stock_level": 0,
                                                                       "opening_stock": stock}).json()["id"]


def test_one_bill_for_court_rentals_and_store_items_split_into_baskets(client):
    from app.models import Payment, RentalCharge, StoreSale
    ha, water = _store_product(client)
    _, body, ids = _setup_rentals(client)
    vm = login(client, "vm@x.com")
    b = direct(client, vm, rentals=[{"item_id": ids["Racket"], "quantity": 2}]).json()           # court 800 + rackets 100
    r = client.post(f"/api/bookings/{b['id']}/store", headers=vm, json={"items": [{"item_id": water, "quantity": 3}]})
    assert r.status_code == 200
    bill = r.json()
    assert (bill["total"], bill["due"], bill["paid"]) == (990, 990, 0)
    assert bill["by_basket"] == {"court": 800, "rental": 100, "store": 90}
    assert client.get("/api/store/1/products", headers=vm).json()[0]["stock"] == 7     # handed over: stock already down
    # one payment settles everything
    timeutil.set_clock(at(19, 0))
    paid = client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "dist_qr"}).json()
    assert (paid["due"], paid["paid"]) == (0, 990) and all(x["paid"] and x["method"] == "dist_qr" for x in paid["lines"])
    assert client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "dist_qr"}).status_code == 409
    # ...but each part sits in its own basket
    db = SessionLocal()
    assert [p.amount for p in db.query(Payment).filter_by(source_type="booking")] == [800]       # court revenue only
    assert sum(x.amount for x in db.query(RentalCharge).filter(RentalCharge.paid_at.is_not(None))) == 100
    s = db.query(StoreSale).one()
    assert (s.status, s.total, s.booking_id, s.payment_method) == ("confirmed", 90, b["id"], "dist_qr")
    db.close()
    sales = client.get("/api/store/1/sales", headers=ha).json()
    assert sales["totals"]["revenue"] == 90                                                         # in the store's books


def test_unpaid_store_items_can_come_off_the_bill(client):
    ha, water = _store_product(client)
    vm = login(client, "vm@x.com")
    b = direct(client, vm).json()
    bill = client.post(f"/api/bookings/{b['id']}/store", headers=vm, json={"items": [{"item_id": water, "quantity": 2}]}).json()
    sid = next(x["id"] for x in bill["lines"] if x["basket"] == "store")
    assert client.get("/api/store/1/sales", headers=ha).json()["totals"]["revenue"] == 0          # unpaid isn't revenue yet
    bill = client.delete(f"/api/bookings/{b['id']}/store/{sid}", headers=vm).json()
    assert [x["basket"] for x in bill["lines"]] == ["court"]
    assert client.get("/api/store/1/products", headers=vm).json()[0]["stock"] == 10


def test_vm_can_cancel_before_or_mid_session_with_a_reason(client):
    vm = login(client, "vm@x.com")
    before = direct(client, vm).json()                                   # 19:00-20:00
    mid = direct(client, vm, start="20:00", phone="9811111301", name="Mid").json()
    assert client.post(f"/api/bookings/{before['id']}/cancel", headers=vm, json={"reason": ""}).status_code == 422
    r = client.post(f"/api/bookings/{before['id']}/cancel", headers=vm, json={"reason": "customer_request"}).json()
    assert r["status"] == "cancelled"
    timeutil.set_clock(at(20, 20))
    client.post(f"/api/bookings/{mid['id']}/attended", headers=vm)
    r = client.post(f"/api/bookings/{mid['id']}/cancel", headers=vm, json={"reason": "left_mid_session"})   # already playing
    assert r.status_code == 200 and r.json()["cancel_reason"] == "left_mid_session"
    late = direct(client, vm, start="21:00", phone="9811111302", name="Late").json()
    timeutil.set_clock(at(22, 5))
    client.post(f"/api/bookings/{late['id']}/pay", headers=login(client, "hq@x.com"), json={"method": "cash"})
    assert client.post(f"/api/bookings/{late['id']}/cancel", headers=vm, json={"reason": "other", "note": "x"}).status_code == 409  # ended


def test_customer_spend_and_average_bill(client):
    ha, water = _store_product(client)
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    b = direct(client, vm).json()
    client.post(f"/api/bookings/{b['id']}/store", headers=vm, json={"items": [{"item_id": water, "quantity": 2}]})
    timeutil.set_clock(at(19, 0))
    client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "cash"})        # bill 1: 800 + 60
    client.post("/api/store/1/sales", headers=vm, json={"items": [{"product_id": water, "quantity": 1}], "phone": "9811111111",
                                                       "method": "cash"})                         # bill 2: 30 standalone
    sp = client.get(f"/api/customers/{b['customer']['id']}", headers=hq).json()["spend"]
    assert (sp["court"], sp["store"], sp["total"], sp["bills"], sp["average_bill"]) == (800, 90, 890, 2, 445)


def test_only_the_mode_of_a_recorded_payment_can_be_edited(client):
    from app.models import Payment
    ha, water = _store_product(client)
    _, body, ids = _setup_rentals(client)
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    b = direct(client, vm, rentals=[{"item_id": ids["Racket"], "quantity": 1}]).json()
    client.post(f"/api/bookings/{b['id']}/store", headers=vm, json={"items": [{"item_id": water, "quantity": 2}]})
    timeutil.set_clock(at(19, 0))
    edit = lambda h, **kw: client.post(f"/api/bookings/{b['id']}/bill/method", headers=h, json=kw)
    assert edit(vm, basket="court", method="cash").status_code == 409             # nothing paid yet
    client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "cash"})
    cash_admin = _cash_admin(client)
    assert _venue_cash(client, cash_admin)["balance"] == 800 + 50 + 60
    # court: cash -> RG QR; the amount and everything else stay, and the cash balance follows
    bill = edit(vm, basket="court", method="rg_qr").json()
    assert [x["method"] for x in bill["lines"] if x["basket"] == "court"] == ["rg_qr"] and bill["total"] == 910
    db = SessionLocal()
    assert [(p.amount, p.method) for p in db.query(Payment).filter_by(source_type="booking")] == [(800, "rg_qr")]
    db.close()
    assert _venue_cash(client, cash_admin)["balance"] == 110
    # a rental line and the store items are edited on their own
    rid = next(x["id"] for x in bill["lines"] if x["basket"] == "rental")
    sid = next(x["id"] for x in bill["lines"] if x["basket"] == "store")
    assert edit(vm, basket="rental", id=rid, method="dist_qr").status_code == 200
    assert edit(vm, basket="store", id=sid, method="razorpay").status_code == 200
    assert {x["basket"]: x["method"] for x in client.get(f"/api/bookings/{b['id']}/bill", headers=vm).json()["lines"]} == {
        "court": "rg_qr", "rental": "dist_qr", "store": "razorpay"}
    # rules
    assert edit(vm, basket="court", method="rg_qr").status_code == 409            # already that
    assert edit(vm, basket="court", method="upi").status_code == 422             # not an option any more
    assert edit(vm, basket="court").status_code == 422 and edit(vm, basket="store", method="cash").status_code == 422
    # a VM can't edit once the day is closed; HQ still can
    client.post(f"/api/bookings/{b['id']}/attended", headers=vm)
    sop_done(client, vm)
    timeutil.set_clock(at(23, 0))
    assert client.post("/api/venues/1/close", headers=vm).status_code == 200
    assert edit(vm, basket="court", method="cash").status_code == 403
    assert edit(hq, basket="court", method="cash").status_code == 200
    # and it all shows in the audit trail
    trail = [e for e in client.get("/api/audit?limit=100", headers=hq).json() if e["action"] == "payment.method"]
    assert len(trail) == 4


def test_old_upi_records_can_be_relabelled_and_online_payments_cannot_be_edited(client):
    from app.models import Booking, Payment
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    b = direct(client, hq, pay_now=True, method="cash").json()
    db = SessionLocal()
    db.query(Payment).update({"method": "upi"})
    db.query(Booking).update({"payment_method": "upi"})                    # a payment recorded before the QR split
    db.commit()
    db.close()
    edit = lambda bid, m: client.post(f"/api/bookings/{bid}/bill/method", headers=hq, json={"basket": "court", "method": m})
    assert edit(b["id"], "rg_qr").status_code == 200
    online = client.post("/api/system/district/simulate", headers=hq, json={"raw": EMAIL.format(pay="Paid")}).json()["booking_id"]
    assert edit(online, "cash").status_code == 409                          # prepaid on the platform


def test_standalone_store_sale_mode_can_be_edited(client):
    ha, water = _store_product(client)
    vm = login(client, "vm@x.com")
    s = client.post("/api/store/1/sales", headers=vm, json={"items": [{"product_id": water, "quantity": 1}], "phone": "9811111111", "method": "cash"}).json()
    assert client.post(f"/api/store/sales/{s['id']}/method", headers=vm, json={"method": "rg_qr"}).json()["payment_method"] == "rg_qr"
    assert client.get("/api/store/1/sales", headers=ha).json()["totals"]["by_method"] == {"rg_qr": 30}
    assert client.post(f"/api/store/sales/{s['id']}/method", headers=vm, json={"method": "bogus"}).status_code == 422


def _vm_cash_setup(client):
    """A second VM (Rani) at Saket and a cash admin; returns their logins."""
    db = SessionLocal()
    rani = User(name="Rani", email="rani@x.com", password_hash=hash_password("pulse1234"), role="vm")
    rani.venues = [VmAssignment(venue_id=2)]
    db.add(rani)
    db.add(User(name="Cash Admin", email="vcadm@x.com", password_hash=hash_password("pulse1234"), role="hq_admin"))
    db.commit()
    db.close()
    return login(client, "rani@x.com"), login(client, "vcadm@x.com")


def test_vm_cash_is_what_they_collected_less_deposits_and_expenses(client):
    from app.models import Notification
    _, ha = _vm_cash_setup(client)
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    assert client.get("/api/my-cash", headers=ha).status_code == 403                     # only VMs hold cash
    assert client.get("/api/my-cash", headers=vm).json()["total"] == 0
    timeutil.set_clock(at(19, 0))
    b = direct(client, vm).json()
    client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "cash"})          # 800 in cash, by Mohit
    direct(client, hq, start="20:00", pay_now=True, method="cash", phone="9811111401", name="H")    # cash taken by HQ, not Mohit
    direct(client, vm, start="21:00", phone="9811111402", name="U")
    client.post("/api/bookings/3/bill/pay", headers=vm, json={"method": "rg_qr"})                   # QR: not cash
    me = client.get("/api/my-cash", headers=vm).json()
    assert (me["total"], me["collected"], me["deposited"], me["expenses"]) == (800, 800, 0, 0)
    assert me["venues"] == [{"venue_id": 1, "name": "East of Kailash", "balance": 800}]
    # deposit: needs the receipt photo
    move = lambda kind, **kw: client.post(f"/api/my-cash/{kind}", headers=vm, json={"venue_id": 1, **kw})
    assert move("deposit", amount=500).status_code == 422
    assert move("deposit", amount=500, photo="data:image/png;base64,iVBORw0KGgo=").status_code == 422
    assert move("deposit", amount=900, photo=SOP_JPEG).status_code == 409                          # can't deposit more than held
    r = move("deposit", amount=500, photo=SOP_JPEG, message="SBI Saket, slip 4411")
    assert r.status_code == 200 and (r.json()["total"], r.json()["deposited"]) == (300, 500)
    # expense: needs a message, the photo is optional
    assert move("expense", amount=100).status_code == 422
    r = move("expense", amount=120, message="Mop and bucket")
    assert (r.json()["total"], r.json()["expenses"]) == (180, 120)
    assert move("expense", amount=500, message="x").status_code == 409
    assert move("transfer", amount=5).status_code == 404
    # each action emailed to Amitesh, with the receipt attached when there is one
    db = SessionLocal()
    mails = db.query(Notification).filter_by(template="vm_cash").order_by(Notification.id).all()
    assert [m.recipient for m in mails] == ["amitesh@rallygully.com"] * 2
    assert "Cash deposit ₹500 by VM" in mails[0].body and "SBI Saket, slip 4411" in mails[0].body and "attached" in mails[0].body
    assert "Cash still with VM: ₹300" in mails[0].body
    assert "Cash expense ₹120 by VM" in mails[1].body and "Mop and bucket" in mails[1].body and "No photo" in mails[1].body
    db.close()
    # the receipt can be opened by the VM and HQ, not by another VM
    rani, _ = login(client, "rani@x.com"), None
    eid = next(e["id"] for e in client.get("/api/my-cash", headers=vm).json()["entries"] if e["kind"] == "deposit")
    assert client.get(f"/api/vm-cash/photo/{eid}", headers=vm).content.startswith(b"\xff\xd8\xff")
    assert client.get(f"/api/vm-cash/photo/{eid}", headers=hq).status_code == 200
    assert client.get(f"/api/vm-cash/photo/{eid}", headers=rani).status_code == 403
    # the venue's own balance came down with them: 800 (Mohit) + 800 (HQ) - 500 - 120
    assert client.get("/api/venues/1/cash", headers=ha).json()["balance"] == 980


def test_hq_sees_every_vms_cash_in_the_roster_performance_table_and_can_adjust(client):
    rani, ha = _vm_cash_setup(client)
    vm, hq = login(client, "vm@x.com"), login(client, "hq@x.com")
    timeutil.set_clock(at(19, 0))
    b = direct(client, vm).json()
    client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "cash"})
    client.post("/api/my-cash/deposit", headers=vm, json={"venue_id": 1, "amount": 300, "photo": SOP_JPEG})
    assert client.get("/api/vm-cash", headers=vm).status_code == 403
    t = client.get(f"/api/vm-cash?date_from={DAY}&date_to={DAY}", headers=hq).json()
    rows = {r["name"]: r for r in t["rows"]}
    assert rows["VM"]["today"] == ["East of Kailash"] and rows["VM"]["cash"] == 500
    assert (rows["VM"]["collected"], rows["VM"]["deposited"]) == (800, 300) and rows["VM"]["last_deposit"]
    assert rows["VM"]["allotted"] == [{"venue_id": 1, "name": "East of Kailash", "days": 1}]
    assert rows["Rani"]["today"] == [] and rows["Rani"]["cash"] == 0
    assert t["total_cash"] == 500
    # HQ Admin can adjust a VM's cash (an opening balance, a correction) with a reason
    adj = lambda **kw: client.post(f"/api/vm-cash/{rows['Rani']['user_id']}/adjust", headers=ha, json={"venue_id": 2, **kw})
    assert adj(amount=250, message="").status_code == 422
    assert adj(amount=0, message="x").status_code == 422
    assert client.post(f"/api/vm-cash/{rows['Rani']['user_id']}/adjust", headers=vm, json={"venue_id": 2, "amount": 5, "message": "x"}).status_code == 403   # a VM can't
    assert adj(amount=250, message="Opening cash").json()["total"] == 250
    assert client.get("/api/my-cash", headers=rani).json()["total"] == 250
    d = client.get(f"/api/vm-cash/{rows['VM']['user_id']}", headers=hq).json()
    assert [e["kind"] for e in d["entries"]] == ["deposit"]


def test_a_deposit_hq_enters_for_a_vm_also_comes_off_that_vms_cash(client):
    ha = _cash_admin(client)
    vm = login(client, "vm@x.com")
    timeutil.set_clock(at(19, 0))
    b = direct(client, vm).json()
    client.post(f"/api/bookings/{b['id']}/bill/pay", headers=vm, json={"method": "cash"})
    assert client.post("/api/venues/1/cash", headers=ha, json={"kind": "deposit", "amount": 300, "depositor_id": 2}).status_code == 200
    assert client.get("/api/my-cash", headers=vm).json()["total"] == 500


def test_player_levels_are_kept_as_written():
    from app.services.player_import import parse_post
    text = ("7-8 PM | Beginner +\n1. Asha\n2. Ravi advance\n3. Meera, 9811112222, Intermediate +\n"
            "8-9 PM | Advance\n1. Kiran\n")
    rows, secs = parse_post(text)
    assert [s["level"] for s in secs] == ["Beginner +", "Advance"]
    assert [(r["name"], r["level"]) for r in rows] == [
        ("Asha", "Beginner +"), ("Ravi", "advance"), ("Meera", "Intermediate +"), ("Kiran", "Advance")]


def test_courts_assigned_by_community_and_seen_by_vm(client):
    hq, vm = login(client, "hq@x.com"), login(client, "vm@x.com")
    g = _game(client, hq, capacity=4)
    pid = client.post(f"/api/community-games/{g['id']}/participants", headers=hq,
                      json={"name": "Asha", "level": "Beginner +"}).json()["participants"][0]["id"]
    fid = g["facility_ids"][0]
    done = client.patch(f"/api/participants/{pid}", headers=hq, json={"facility_id": fid}).json()
    assert done["participants"][0]["facility_id"] == fid
    assert client.patch(f"/api/participants/{pid}", headers=hq, json={"facility_id": 9999}).status_code == 422
    assert client.patch(f"/api/participants/{pid}", headers=vm, json={"facility_id": None}).status_code in (403, 404)
    seen = client.get(f"/api/community-games/{g['id']}", headers=vm)
    if seen.status_code == 200:
        assert seen.json()["participants"][0]["facility_id"] == fid
    assert client.patch(f"/api/participants/{pid}", headers=hq, json={"facility_id": None}).json()["participants"][0]["facility_id"] is None
