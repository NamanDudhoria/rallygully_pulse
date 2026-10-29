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
    assert client.patch(f"/api/participants/{ravi['id']}", headers=hq, json={"paid": True, "method": "upi"}).status_code == 200
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
    assert st[2] == ("ready", "beginner") and st[3][0] == "ready" and st[4][0] == "error"
    assert st[5] == ("ready", "intermediate") and st[6][0] == "skip" and st[7] == ("ready", "advanced")
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
    assert all(p["level"] == "intermediate" and p["phone"] is None for p in ps)
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
    b = direct(client, vm, pay_now=True, method="upi", rentals=[{"item_id": ids["Shoes"], "quantity": 1}]).json()
    assert b["payment_status"] == "paid" and b["rental_due"] == 0 and b["rentals"][0]["paid_method"] == "upi"


def test_payment_methods_are_cash_upi_razorpay_other(client):
    vm = login(client, "hq@x.com")
    assert direct(client, vm, pay_now=True, method="card", phone="9811111130", name="Q").status_code == 422
    for i, m in enumerate(("cash", "upi", "razorpay", "other")):
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
            "method": "upi", **kw}
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
