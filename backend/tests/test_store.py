"""RG Store: per-venue products and stock, HQ-only restocking, sales that reduce stock, receipts, cash."""
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app import timeutil
from app.auth import hash_password, reset_throttle
from app.db import Base, SessionLocal, engine
from app.main import app
from app.models import (Notification, StoreProduct, User, Venue, VmAssignment, VmAttendance, VmRoster, WeeklyHours)

DAY = date(2026, 10, 7)


def at(h, m=0, d=DAY):
    return datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=m)


@pytest.fixture()
def c():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    db = SessionLocal()
    eok, skt = Venue(code="EOK", name="RallyGully East of Kailash"), Venue(code="SKT", name="Saket")
    db.add_all([eok, skt])
    db.flush()
    for v in (eok, skt):
        for wd in range(7):
            db.add(WeeklyHours(venue_id=v.id, weekday=wd, open_min=17 * 60, close_min=23 * 60))
    pw = hash_password("pulse1234")
    db.add_all([User(name="Admin", email="admin@x.com", password_hash=pw, role="hq_admin"),
                User(name="HQ", email="hq@x.com", password_hash=pw, role="hq"),
                User(name="Coach", email="academy@x.com", password_hash=pw, role="academy")])
    vm = User(name="Mohit", email="vm@x.com", password_hash=pw, role="vm")
    vm.venues = [VmAssignment(venue_id=eok.id), VmAssignment(venue_id=skt.id)]
    db.add(vm)
    db.flush()
    db.add(VmRoster(venue_id=eok.id, user_id=vm.id, date=DAY))  # rostered at EOK only
    db.add(VmAttendance(user_id=vm.id, venue_id=eok.id, date=DAY, clock_in_at=at(16), in_lat=0, in_lng=0,
                        in_distance_m=0, photo_at=at(16), photo_status="sent"))
    db.commit()
    db.close()
    timeutil.set_clock(at(18))
    reset_throttle()
    yield TestClient(app)
    timeutil.set_clock(None)


def login(c, email):
    return {"Authorization": f"Bearer {c.post('/api/auth/login', json={'email': email, 'password': 'pulse1234'}).json()['token']}"}


def product(c, h, name="Water 500ml", price=30, stock=10, low=3, venue=1, category="Drinks"):
    return c.post(f"/api/store/{venue}/products", headers=h, json={"name": name, "category": category, "price": price,
                                                                  "low_stock_level": low, "opening_stock": stock})


def sale(c, h, items, phone="9811111190", venue=1, method="cash", **kw):
    return c.post(f"/api/store/{venue}/sales", headers=h, json={"items": items, "phone": phone, "method": method, **kw})


def test_products_are_per_venue_and_only_hq_manages_them(c):
    admin, hq, vm, coach = (login(c, e) for e in ("admin@x.com", "hq@x.com", "vm@x.com", "academy@x.com"))
    assert product(c, vm).status_code == 403
    assert product(c, coach).status_code == 403
    p = product(c, admin).json()
    assert (p["stock"], p["price"], p["category"]) == (10, 30, "Drinks")
    assert product(c, admin).status_code == 409                      # same name twice at one venue
    assert product(c, hq, venue=2).status_code == 200                # plain HQ is an admin too; a separate list per venue
    assert product(c, admin, name="Free", price=0).status_code == 422
    assert [x["name"] for x in c.get("/api/store/1/products", headers=vm).json()] == ["Water 500ml"]
    assert c.get("/api/store/2/products", headers=vm).status_code == 403   # not rostered at Saket today
    # restocking and corrections are HQ only, and every change is on the product's history
    pid = p["id"]
    assert c.post(f"/api/store/products/{pid}/restock", headers=vm, json={"quantity": 5}).status_code == 403
    assert c.post(f"/api/store/products/{pid}/restock", headers=admin, json={"quantity": 24, "unit_cost": 12, "note": "Metro"}).json()["stock"] == 34
    assert c.post(f"/api/store/products/{pid}/adjust", headers=admin, json={"delta": -2, "reason": ""}).status_code == 422
    assert c.post(f"/api/store/products/{pid}/adjust", headers=admin, json={"delta": -99, "reason": "x"}).status_code == 422
    assert c.post(f"/api/store/products/{pid}/adjust", headers=admin, json={"delta": -4, "reason": "2 bottles leaked"}).json()["stock"] == 30
    moves = c.get(f"/api/store/products/{pid}/moves", headers=admin).json()
    assert [(m["kind"], m["quantity"], m["stock_after"]) for m in moves] == [("adjust", -4, 30), ("restock", 24, 34), ("restock", 10, 10)]
    # a product taken off sale disappears for the VM but stays for HQ
    c.put(f"/api/store/products/{pid}", headers=admin, json={"name": "Water 500ml", "price": 35, "low_stock_level": 3, "active": False})
    assert c.get("/api/store/1/products", headers=vm).json() == []
    assert c.get("/api/store/1/products", headers=admin).json()[0]["price"] == 35


def test_a_sale_reduces_stock_and_sends_receipts(c):
    admin, vm = login(c, "admin@x.com"), login(c, "vm@x.com")
    water = product(c, admin).json()["id"]
    chips = product(c, admin, name="Chips", price=20, stock=5, category="Snacks").json()["id"]
    r = sale(c, vm, [{"product_id": water, "quantity": 2}, {"product_id": chips, "quantity": 1}, {"product_id": water, "quantity": 1}],
             name="Asha", email="asha@example.com")
    assert r.status_code == 200
    s = r.json()
    assert s["total"] == 110 and s["status"] == "confirmed" and s["receipt"] == f"RG-{s['id']:05d}"
    assert [(i["name"], i["quantity"]) for i in s["items"]] == [("Water 500ml", 3), ("Chips", 1)]   # same item merged
    stock = {p["name"]: p["stock"] for p in c.get("/api/store/1/products", headers=vm).json()}
    assert stock == {"Water 500ml": 7, "Chips": 4}
    db = SessionLocal()
    wa = db.query(Notification).filter_by(template="store_receipt", channel="whatsapp").one()
    mail = db.query(Notification).filter_by(template="store_receipt", channel="email").one()
    assert wa.recipient == "9811111190" and "3 × Water 500ml" in wa.body and "₹110" in wa.body
    assert mail.recipient == "asha@example.com" and "Total: ₹110.00" in mail.body
    db.close()
    # no email given: WhatsApp only
    sale(c, vm, [{"product_id": chips, "quantity": 1}], phone="9811111191")
    db = SessionLocal()
    assert db.query(Notification).filter_by(template="store_receipt", channel="email").count() == 1
    db.close()


def test_a_sale_is_all_or_nothing_when_stock_runs_short(c):
    admin, vm = login(c, "admin@x.com"), login(c, "vm@x.com")
    water = product(c, admin, stock=2).json()["id"]
    chips = product(c, admin, name="Chips", price=20, stock=5).json()["id"]
    r = sale(c, vm, [{"product_id": chips, "quantity": 2}, {"product_id": water, "quantity": 3}])
    assert r.status_code == 409 and "Only 2 Water 500ml left" in r.json()["detail"]
    stock = {p["name"]: p["stock"] for p in c.get("/api/store/1/products", headers=vm).json()}
    assert stock == {"Water 500ml": 2, "Chips": 5}                  # nothing taken
    sale(c, vm, [{"product_id": water, "quantity": 2}])
    r = sale(c, vm, [{"product_id": water, "quantity": 1}])
    assert r.status_code == 409 and "out of stock" in r.json()["detail"]


def test_sale_rules(c):
    admin, vm = login(c, "admin@x.com"), login(c, "vm@x.com")
    water = product(c, admin).json()["id"]
    other = product(c, admin, venue=2).json()["id"]
    assert sale(c, vm, []).status_code == 422
    assert sale(c, vm, [{"product_id": water, "quantity": 1}], method="card").status_code == 422
    assert sale(c, vm, [{"product_id": water, "quantity": 0}]).status_code == 422
    assert sale(c, vm, [{"product_id": water, "quantity": 1}], phone="12345").status_code == 422      # phone is required and valid
    assert sale(c, vm, [{"product_id": other, "quantity": 1}]).status_code == 422                     # another venue's product
    assert sale(c, vm, [{"product_id": other, "quantity": 1}], venue=2).status_code == 403           # not rostered there
    assert sale(c, login(c, "academy@x.com"), [{"product_id": water, "quantity": 1}]).status_code == 403
    assert sale(c, admin, [{"product_id": other, "quantity": 1}], venue=2).status_code == 200        # HQ can sell anywhere


def test_low_stock_alert_reaches_admins_once_as_it_crosses(c):
    admin, vm = login(c, "admin@x.com"), login(c, "vm@x.com")
    water = product(c, admin, stock=5, low=3).json()["id"]
    sale(c, vm, [{"product_id": water, "quantity": 1}])               # 4 left: above the level
    db = SessionLocal()
    assert db.query(Notification).filter_by(template="store_low_stock").count() == 0
    db.close()
    sale(c, vm, [{"product_id": water, "quantity": 1}])               # 3 left: crosses
    sale(c, vm, [{"product_id": water, "quantity": 1}])               # 2 left: already low, no repeat
    db = SessionLocal()
    alerts = db.query(Notification).filter_by(template="store_low_stock").all()
    assert sorted(a.recipient for a in alerts) == ["admin@x.com", "hq@x.com"]
    assert "Water 500ml: 3 left" in alerts[0].body and "East of Kailash" in alerts[0].body
    db.close()


def test_void_puts_stock_back_and_cash_counts_store_sales(c):
    admin, hq, vm = login(c, "admin@x.com"), login(c, "hq@x.com"), login(c, "vm@x.com")
    water = product(c, admin).json()["id"]
    cash_sale = sale(c, vm, [{"product_id": water, "quantity": 2}]).json()
    sale(c, vm, [{"product_id": water, "quantity": 1}], method="upi", phone="9811111192")
    assert c.get("/api/venues/1/cash", headers=admin).json()["balance"] == 60       # only the cash sale
    day = c.get("/api/store/1/sales", headers=vm).json()
    assert day["totals"] == {"sales": 2, "revenue": 90, "by_method": {"cash": 60, "upi": 30}, "voided": 0}
    assert day["top_items"][0] == {"name": "Water 500ml", "quantity": 3, "amount": 90}
    assert c.post(f"/api/store/sales/{cash_sale['id']}/void", headers=vm, json={"reason": "x"}).status_code == 403
    assert c.post(f"/api/store/sales/{cash_sale['id']}/void", headers=hq, json={"reason": ""}).status_code == 422
    v = c.post(f"/api/store/sales/{cash_sale['id']}/void", headers=hq, json={"reason": "Rang up twice"}).json()
    assert v["status"] == "voided"
    assert c.post(f"/api/store/sales/{cash_sale['id']}/void", headers=hq, json={"reason": "again"}).status_code == 409
    assert c.get("/api/store/1/products", headers=vm).json()[0]["stock"] == 9       # 10 - 2 - 1 + 2
    assert c.get("/api/venues/1/cash", headers=admin).json()["balance"] == 0
    assert c.get("/api/store/1/sales", headers=vm).json()["totals"]["voided"] == 1
    # store money stays out of court revenue: no booking payments were created
    db = SessionLocal()
    from app.models import Payment
    assert db.query(Payment).count() == 0
    db.close()
