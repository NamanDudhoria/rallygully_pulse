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
    assert direct(client, h, duration=90).status_code == 422


def test_price_from_config_and_editable(client):
    h = login(client, "vm@x.com")
    b = direct(client, h).json()
    assert b["booking_value"] == 800 and b["sport"] == "Pickleball"
    b2 = direct(client, h, facility=3, amount=1200).json()
    assert b2["booking_value"] == 1200


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
    assert json.loads(form["ContentVariables"][0])["3"] == "https://chat.whatsapp.com/EOKgroup"
    db = SessionLocal()
    assert db.query(Notification).filter_by(channel="whatsapp", status="sent").count() == 1
    db.close()
