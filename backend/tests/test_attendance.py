"""VM attendance: the venue opens only after an on-site clock-in and a camera photo, which is emailed to ops."""
import base64
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, timeutil
from app.auth import hash_password, reset_throttle
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import Facility, Notification, User, Venue, VmAssignment, VmAttendance, VmRoster, WeeklyHours
from app.services import attendance

DAY = date(2026, 10, 7)
EOK = (28.5571, 77.2481)   # venue position
SKT = (28.5245, 77.2066)   # another venue in the same VM's cluster, ~5 km away
NEAR = (28.5590, 77.2481)  # ~210 m north of EOK
FAR = (28.5841, 77.2481)   # ~3 km north of EOK
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"0" * 2000).decode()
PHOTOS = {"venue": JPEG, "branding": JPEG, "toilets": JPEG}  # the three compulsory photos


def at(h, m=0, d=DAY):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m)


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    eok = Venue(code="EOK", name="East of Kailash", latitude=EOK[0], longitude=EOK[1])
    skt = Venue(code="SKT", name="Saket", latitude=SKT[0], longitude=SKT[1])
    bare = Venue(code="NEW", name="New Venue")  # no map position yet
    db.add_all([eok, skt, bare])
    db.flush()
    for v in (eok, skt, bare):
        for wd in range(7):
            db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
        db.add(Facility(venue_id=v.id, name="Court 1", facility_type="Court", sport="Pickleball"))
    pw = hash_password("pulse1234")
    db.add(User(name="HQ", email="hq@x.com", password_hash=pw, role="hq"))
    vm = User(name="Rohit Kumar", email="vm@x.com", password_hash=pw, role="vm")
    vm.venues = [VmAssignment(venue_id=v.id) for v in (eok, skt, bare)]
    db.add(vm)
    db.flush()
    for v in (eok, skt, bare):
        db.add(VmRoster(venue_id=v.id, user_id=vm.id, date=DAY))
    db.commit()
    db.close()
    timeutil.set_clock(at(16, 30))
    reset_throttle()
    yield TestClient(app)
    timeutil.set_clock(None)


def login(c, email):
    return {"Authorization": f"Bearer {c.post('/api/auth/login', json={'email': email, 'password': 'pulse1234'}).json()['token']}"}


def fix(pos, accuracy=20):
    return {"latitude": pos[0], "longitude": pos[1], "accuracy": accuracy}


def test_distance_is_metres():
    assert abs(attendance.distance_m(28.0, 77.0, 29.0, 77.0) - 111_195) < 50  # one degree of latitude
    assert 190 < attendance.distance_m(*EOK, *NEAR) < 230


def test_venue_stays_closed_until_clock_in_and_photo(c):
    vm = login(c, "vm@x.com")
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=vm).status_code == 428
    st = c.get("/api/venues/1/attendance", headers=vm).json()
    assert st["can_clock_in"] and not st["unlocked"] and st["radius_m"] == 500
    r = c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    assert r.status_code == 200 and r.json()["clocked_in"] and not r.json()["unlocked"]
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=vm).status_code == 428  # photo still owed
    r = c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS)
    assert r.status_code == 200 and r.json()["unlocked"]
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=vm).status_code == 200
    assert c.get("/api/auth/me", headers=vm).json()["roster"][0]["clocked_in"] is True


def test_clock_in_refused_away_from_venue(c):
    vm = login(c, "vm@x.com")
    r = c.post("/api/venues/1/clock-in", headers=vm, json=fix(FAR))
    assert r.status_code == 409 and "3.0 km" in r.json()["detail"] and "500 m" in r.json()["detail"]
    r = c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR, accuracy=900))
    assert r.status_code == 422 and "too rough" in r.json()["detail"]
    assert c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR)).status_code == 200
    assert c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR)).status_code == 409  # only once


def test_each_venue_needs_its_own_clock_in(c):
    vm = login(c, "vm@x.com")
    c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS)
    assert c.get(f"/api/venues/2/day?date={DAY}", headers=vm).status_code == 428
    assert c.post("/api/venues/2/clock-in", headers=vm, json=fix(NEAR)).status_code == 409  # Saket is ~4 km away


def test_clock_in_window_and_lateness(c):
    vm = login(c, "vm@x.com")
    timeutil.set_clock(at(15, 30))  # shift 5 PM: clock-in opens at 4 PM
    st = c.get("/api/venues/1/attendance", headers=vm).json()
    assert not st["can_clock_in"] and st["blocker"] == "Clock-in opens at 4:00 PM."
    assert c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR)).status_code == 409
    timeutil.set_clock(at(17, 25))
    st = c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR)).json()
    assert st["late_min"] == 25
    st = c.get(f"/api/venues/1/attendance?date={DAY + timedelta(days=1)}", headers=vm).json()
    assert not st["can_clock_in"]  # not rostered tomorrow


def test_venue_without_map_position_cannot_be_clocked_into(c):
    vm = login(c, "vm@x.com")
    st = c.get("/api/venues/3/attendance", headers=vm).json()
    assert not st["can_clock_in"] and "map location" in st["blocker"]


def test_three_photos_are_compulsory_and_must_be_camera_jpegs(c):
    vm = login(c, "vm@x.com")
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS).status_code == 409  # no clock-in
    c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    png = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n" + b"0" * 100).decode()
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json={**PHOTOS, "branding": png}).status_code == 422
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json={**PHOTOS, "venue": "nonsense"}).status_code == 422
    r = c.post("/api/venues/1/clock-in/photos", headers=vm, json={"venue": JPEG, "branding": JPEG})
    assert r.status_code == 422 and "toilets" in r.json()["detail"]
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json={"venue": JPEG}).status_code == 422
    # nothing was kept by the refused attempts: the venue is still closed and the full set still works
    assert c.get(f"/api/venues/1/day?date={DAY}", headers=vm).status_code == 428
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS).status_code == 200
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS).status_code == 409   # only once


def test_photos_are_emailed_together_and_kept_only_until_delivered(c, monkeypatch):
    from app.models import AttendancePhoto
    vm = login(c, "vm@x.com")
    c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    r = c.post("/api/venues/1/clock-in/photos", headers=vm, json={**PHOTOS, "other": JPEG})   # the optional fourth
    assert r.json()["photo_delivery"] == "simulated"  # no SMTP configured in tests
    db = SessionLocal()
    kept = db.query(AttendancePhoto).order_by(AttendancePhoto.id).all()
    assert [p.kind for p in kept] == ["venue", "branding", "toilets", "other"]
    assert all(p.data and p.data.startswith(b"\xff\xd8\xff") for p in kept)  # held for a retry
    n = db.query(Notification).filter_by(template="attendance_photo").one()
    assert n.recipient == config.ATTENDANCE_EMAIL and "Rohit Kumar clocked in at East of Kailash" in n.body
    assert "Clock-in: 4:30 PM IST" in n.body  # the pinned clock is 16:30 venue time
    assert "Attached (4):\n  1. Venue\n  2. Branding\n  3. Toilets\n  4. Other" in n.body
    assert "base64" not in n.body and "maps.google.com" in n.body
    db.close()

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr("app.services.notify.smtplib.SMTP", FakeSMTP)
    timeutil.set_clock(at(16, 45))  # past the retry interval
    db = SessionLocal()
    assert attendance.retry_photos(db) == 1
    db.commit()
    a = db.query(VmAttendance).one()
    assert a.photo_status == "sent" and a.photo_sent_at is not None
    assert all(p.data is None for p in db.query(AttendancePhoto))  # deleted once sent
    db.close()
    att = [p for p in sent[0].iter_attachments()]
    assert [x.get_filename() for x in att] == [f"EOK-2026-10-07-rohit-{k}.jpg" for k in ("venue", "branding", "toilets", "other")]
    assert all(x.get_content_type() == "image/jpeg" for x in att)


def test_optional_photo_can_be_left_out(c):
    vm = login(c, "vm@x.com")
    c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    assert c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS).status_code == 200
    db = SessionLocal()
    n = db.query(Notification).filter_by(template="attendance_photo").one()
    assert "Attached (3):\n  1. Venue\n  2. Branding\n  3. Toilets" in n.body and "Other" not in n.body
    db.close()


def test_clock_out_and_what_hq_sees(c):
    vm, hq = login(c, "vm@x.com"), login(c, "hq@x.com")
    c.post("/api/venues/1/clock-in", headers=vm, json=fix(NEAR))
    c.post("/api/venues/1/clock-in/photos", headers=vm, json=PHOTOS)
    timeutil.set_clock(at(23, 5))
    r = c.post("/api/venues/1/clock-out", headers=vm, json=fix(FAR))
    assert r.status_code == 200 and r.json()["clocked_out"]
    assert c.post("/api/venues/1/clock-out", headers=vm, json=fix(NEAR)).status_code == 409
    entry = next(e for e in c.get(f"/api/roster?date_from={DAY}&date_to={DAY}", headers=hq).json()["entries"]
                 if e["venue_id"] == 1)
    a = entry["attendance"]
    assert a["distance_m"] < 250 and a["photo"] == "simulated" and a["left_away"] is True
    duty = c.get(f"/api/venues/1/day?date={DAY}", headers=hq).json()["on_duty"][0]
    assert duty["attendance"]["clock_in_at"] and duty["attendance"]["clock_out_at"]
    assert c.get(f"/api/venues/2/day?date={DAY}", headers=hq).status_code == 200  # HQ is never clock-in gated
    assert c.post("/api/venues/1/clock-in", headers=hq, json=fix(NEAR)).status_code == 403  # VMs only


def test_vm_lists_hide_days_not_clocked_in(c):
    vm = login(c, "vm@x.com")
    assert c.get("/api/bookings", headers=vm).json() == []


def test_hq_sets_venue_map_position(c):
    admin = User(name="Admin", email="admin@x.com", password_hash=hash_password("pulse1234"), role="hq_admin")
    db = SessionLocal()
    db.add(admin)
    db.commit()
    db.close()
    h = login(c, "admin@x.com")
    body = {"code": "NEW", "name": "New Venue", "location": "", "status": "active", "latitude": 28.6, "longitude": 77.2}
    r = c.put("/api/venues/3", headers=h, json=body)
    assert r.status_code == 200 and r.json()["latitude"] == 28.6
    assert c.put("/api/venues/3", headers=h, json={**body, "longitude": None}).status_code == 422
    assert c.put("/api/venues/3", headers=h, json={**body, "latitude": 95}).status_code == 422
