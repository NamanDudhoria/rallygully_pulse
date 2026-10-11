"""Roles, screen/API access, roster-gated VM access, invites, password reset and sessions."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.auth import hash_password, reset_throttle
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Facility, Notification, PricingRule, User, Venue, VmAssignment, VmRoster, WeeklyHours
from app.services import roster

DAY = date(2026, 10, 7)


def at(h, m=0, d=DAY):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m)


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    eok, skt = Venue(code="EOK", name="East of Kailash"), Venue(code="SKT", name="Saket")
    db.add_all([eok, skt])
    db.flush()
    for v in (eok, skt):
        for wd in range(7):
            db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
        db.add(Facility(venue_id=v.id, name="Court 1", facility_type="Court", sport="Pickleball"))
        db.add(PricingRule(venue_id=v.id, sport="Pickleball", start_min=0, end_min=1440, duration_min=60,
                           amount=800, effective_from=date(2026, 1, 1)))
    pw = hash_password("pulse1234")
    for role in ("hq_admin", "hq", "community", "academy"):
        db.add(User(name=role, email=f"{role}@x.com", password_hash=pw, role=role))
    vm = User(name="Rohit", email="vm@x.com", password_hash=pw, role="vm")
    vm.venues = [VmAssignment(venue_id=eok.id)]
    other = User(name="Kavya", email="vm2@x.com", password_hash=pw, role="vm")
    other.venues = [VmAssignment(venue_id=eok.id)]
    db.add_all([vm, other])
    db.flush()
    db.add(VmRoster(venue_id=eok.id, user_id=vm.id, date=DAY))
    db.commit()
    db.close()
    timeutil.set_clock(at(16))
    reset_throttle()
    yield TestClient(app)
    timeutil.set_clock(None)


def login(c, email, pw="pulse1234"):
    r = c.post("/api/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


def direct(c, h, venue=1, facility=1, d=DAY):
    return c.post("/api/bookings/direct", headers=h, json={
        "venue_id": venue, "facility_id": facility, "date": d.isoformat(), "start": "19:00", "duration": 60,
        "phone": "9811111111", "name": "Asha"})


GAME = {"venue_id": 1, "sport": "Pickleball", "date": DAY.isoformat(), "start": "20:00", "end": "22:00",
        "facility_ids": [1], "capacity": 8, "per_person": 400, "community_link": "https://chat.whatsapp.com/x"}
ACADEMY = {"venue_id": 1, "name": "Juniors", "facility_ids": [1], "start_date": (DAY + timedelta(days=2)).isoformat(),
           "start": "17:00", "end": "18:00", "value_per_occurrence": 1000}


# ── Roster-gated VM access ───────────────────────────────────────────────────
def test_vm_needs_roster_for_that_venue_and_date(c):
    h = login(c, "vm@x.com")
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=h).status_code == 200
    assert direct(c, h).status_code == 200
    r = c.get(f"/api/venues/1/day?date={DAY + timedelta(days=1)}", headers=h)
    assert r.status_code == 403 and "not rostered" in r.json()["detail"]
    assert direct(c, h, d=DAY + timedelta(days=1)).status_code == 403
    assert c.get(f"/api/venues/2/day?date={DAY}", headers=h).status_code == 403  # other venue


def test_multiple_vms_share_a_venue_day(c):
    admin = login(c, "hq_admin@x.com")
    assert c.post("/api/roster", headers=admin, json={"venue_id": 1, "user_id": 6, "date": DAY.isoformat()}).status_code == 200
    h1, h2 = login(c, "vm@x.com"), login(c, "vm2@x.com")
    b = direct(c, h1).json()
    timeutil.set_clock(at(19))
    assert c.post(f"/api/bookings/{b['id']}/pay", headers=h2, json={"method": "cash"}).status_code == 200


def test_vm_lists_only_rostered_venue_days(c):
    h = login(c, "vm@x.com")
    direct(c, h)
    admin = login(c, "hq_admin@x.com")
    direct(c, admin, venue=2, facility=2)  # Saket booking the VM must not see
    rows = c.get("/api/bookings", headers=h).json()
    assert rows and all(b["venue_id"] == 1 and b["date"] == DAY.isoformat() for b in rows)
    assert [v["id"] for v in c.get("/api/venues", headers=h).json()] == [1]
    me = c.get("/api/auth/me", headers=h).json()
    assert me["roster"][0]["venue_id"] == 1 and "venue.operate" in me["permissions"]


def test_rostering_outside_team_joins_team(c):
    hq = login(c, "hq@x.com")  # roster.manage without users.manage
    r = c.post("/api/roster", headers=hq, json={"venue_id": 2, "user_id": 5, "date": DAY.isoformat()})
    assert r.status_code == 200
    db = SessionLocal()
    assert db.get(VmAssignment, (5, 2)) is not None
    db.close()
    assert c.get(f"/api/venues/2/day?date={DAY}", headers=login(c, "vm@x.com")).status_code == 200
    vms = c.get(f"/api/roster?date_from={DAY}&date_to={DAY}", headers=hq).json()["vms"]
    assert {v["name"] for v in vms} == {"Rohit", "Kavya"}
    r = c.post("/api/roster", headers=hq, json={"venue_id": 1, "user_id": 3, "date": DAY.isoformat()})
    assert r.status_code == 422  # community user isn't a VM


def test_roster_copy_week(c):
    admin = login(c, "hq_admin@x.com")
    r = c.post("/api/roster/copy", headers=admin, json={"from_date": DAY.isoformat(),
                                                        "to_date": (DAY + timedelta(days=7)).isoformat(), "days": 1})
    assert r.json()["added"] == 1
    data = c.get(f"/api/roster?date_from={DAY + timedelta(days=7)}&date_to={DAY + timedelta(days=7)}", headers=admin).json()
    assert [e["name"] for e in data["entries"]] == ["Rohit"]


def test_evening_gap_alert_once(c):
    timeutil.set_clock(at(20, 5))
    db = SessionLocal()
    try:
        assert roster.alert_gaps(db) == 2  # both venues operate tomorrow, nobody rostered
        db.commit()
        assert roster.alert_gaps(db) == 0  # not repeated
        db.commit()
        mails = db.query(Notification).filter(Notification.template == "roster_gap").all()
        assert len(mails) == 1 and "East of Kailash" in mails[0].body
    finally:
        db.close()


def test_leaving_team_removes_future_roster(c):
    admin = login(c, "hq_admin@x.com")
    r = c.put("/api/users/5", headers=admin, json={"name": "Rohit", "email": "vm@x.com", "role": "vm", "venue_ids": [2]})
    assert r.status_code == 200
    db = SessionLocal()
    assert db.query(VmRoster).filter(VmRoster.user_id == 5).count() == 0
    db.close()


# ── Role permissions ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("email,allowed,denied", [
    ("community@x.com",
     ["/api/analytics/portfolio", "/api/community-games", "/api/customers", f"/api/venues/1/day?date={DAY}"],
     ["/api/academy", "/api/events", "/api/audit", "/api/users", "/api/roster?date_from=2026-10-07&date_to=2026-10-08",
      "/api/pricing", "/api/system/notifications", "/api/bookings"]),
    ("academy@x.com",
     ["/api/analytics/portfolio", "/api/academy", f"/api/venues/1/day?date={DAY}"],
     ["/api/customers", "/api/events", "/api/audit", "/api/users", "/api/pricing"]),
    ("hq@x.com",
     ["/api/analytics/portfolio", "/api/academy", "/api/events", "/api/audit", "/api/pricing", "/api/customers",
      "/api/roster?date_from=2026-10-07&date_to=2026-10-08", "/api/bookings"],
     ["/api/users"]),
    ("vm@x.com",
     [f"/api/venues/1/day?date={DAY}", "/api/bookings"],
     ["/api/analytics/portfolio", "/api/academy", "/api/customers", "/api/audit", "/api/users", "/api/pricing",
      "/api/analytics/drill?date_from=2026-10-01&date_to=2026-10-06"]),
])
def test_screen_access_matrix(c, email, allowed, denied):
    h = login(c, email)
    for url in allowed:
        assert c.get(url, headers=h).status_code == 200, (email, url)
    for url in denied:
        assert c.get(url, headers=h).status_code == 403, (email, url)


def test_community_manages_games_but_cannot_operate_bookings(c):
    h = login(c, "community@x.com")
    g = c.post("/api/community-games", headers=h, json=GAME)
    assert g.status_code == 200
    assert c.post("/api/academy", headers=h, json=ACADEMY).status_code == 403
    assert direct(c, h).status_code == 403
    assert c.post(f"/api/venues/1/close?date={DAY}", headers=h).status_code == 403
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=h).json()["can_operate"] is False


def test_academy_manages_academy_only(c):
    h = login(c, "academy@x.com")
    assert c.post("/api/academy", headers=h, json=ACADEMY).status_code == 200
    assert c.post("/api/community-games", headers=h, json=GAME).status_code == 403


def test_vm_cannot_refund_or_lodge_games(c):
    h = login(c, "vm@x.com")
    b = direct(c, h).json()
    assert c.post(f"/api/bookings/{b['id']}/refund", headers=h, json={"amount": 100, "reason": "x"}).status_code == 403
    assert c.post("/api/community-games", headers=h, json=GAME).status_code == 403


# ── People, invites, passwords, sessions ─────────────────────────────────────
def test_invite_flow_email_based(c):
    admin = login(c, "hq_admin@x.com")
    r = c.post("/api/users", headers=admin, json={"name": "Neha", "email": "Neha@RallyGully.com", "role": "community"})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "neha@rallygully.com" and body["status"] == "invited"
    link = body["invite"]["link"]  # no SMTP in tests → link handed to the admin
    token = link.split("token=")[1]
    assert c.post("/api/auth/login", json={"email": "neha@rallygully.com", "password": "whatever1"}).status_code == 401
    assert c.get(f"/api/auth/token?token={token}").json() == {"valid": True, "purpose": "invite",
                                                              "email": "neha@rallygully.com", "name": "Neha"}
    assert c.post("/api/auth/set-password", json={"token": token, "password": "short"}).status_code == 422
    assert c.post("/api/auth/set-password", json={"token": token, "password": "Courtside-2026"}).status_code == 200
    assert c.post("/api/auth/set-password", json={"token": token, "password": "Another-2026"}).status_code == 400
    login(c, "neha@rallygully.com", "Courtside-2026")
    assert c.post("/api/users", headers=admin, json={"name": "Dup", "email": "neha@rallygully.com",
                                                     "role": "vm"}).status_code == 409


def test_only_admin_manages_people(c):
    h = login(c, "hq@x.com")
    assert c.post("/api/users", headers=h, json={"name": "X", "email": "x@x.com", "role": "hq"}).status_code == 403


def test_forgot_password_does_not_leak_or_log_link(c):
    a = c.post("/api/auth/forgot", json={"email": "nobody@x.com"}).json()
    b = c.post("/api/auth/forgot", json={"email": "vm@x.com"}).json()
    assert a == b
    db = SessionLocal()
    n = db.query(Notification).filter(Notification.template == "user_reset").one()
    assert "token=" not in n.body and "withheld" in n.body
    db.close()


def test_password_change_signs_out_other_sessions(c):
    old = login(c, "vm@x.com")
    r = c.post("/api/auth/change-password", headers=old, json={"current_password": "pulse1234",
                                                               "new_password": "Baseline-2026"})
    assert r.status_code == 200
    assert c.get("/api/auth/me", headers=old).status_code == 401
    assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['token']}"}).status_code == 200


def test_disabling_user_ends_session(c):
    h = login(c, "vm2@x.com")
    admin = login(c, "hq_admin@x.com")
    c.put("/api/users/6", headers=admin, json={"name": "Kavya", "email": "vm2@x.com", "role": "vm",
                                               "venue_ids": [1], "active": False})
    assert c.get("/api/auth/me", headers=h).status_code == 401


def test_admin_cannot_lock_self_out(c):
    admin = login(c, "hq_admin@x.com")
    r = c.put("/api/users/1", headers=admin, json={"name": "a", "email": "hq_admin@x.com", "role": "hq"})
    assert r.status_code == 409


def test_login_throttle(c):
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": "vm@x.com", "password": "wrong-pass"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": "vm@x.com", "password": "pulse1234"})
    assert r.status_code == 429
